"""run_discovery: the offline batch orchestrator.

Reads the corpus and trace history, clusters repeated frontier workflows,
proposes atomic skill candidates, enforces the no-overlap rule, runs the
promotion gate (judge + measured savings), and writes results:

  promoted skills  -> src/skills/<id>.json
  merges           -> parts appended to the existing skill's file
  pending          -> src/skills/_candidates/<id>.json (judge/runner missing)
  every decision   -> src/traces/discovery.jsonl (with decision_path)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .cluster import cluster_history, load_tasks, load_traces
from .embeddings import get_embedder
from .gate import JudgeFn, RunFrontierFn, RunSkillFn, judge_gate
from .llm import BaseLLM, get_llm
from .models import GateDecision, Skill
from .overlap import merge_skills, overlap_check
from .propose import propose_skill

CANDIDATES_DIRNAME = "_candidates"
PRUNED_DIRNAME = "_pruned"


class DiscoveryReport(BaseModel):
    ts: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat()
    )
    llm: str = "unknown"
    embedder: str = "unknown"
    n_tasks: int = 0
    n_traces: int = 0
    n_existing_skills: int = 0
    clusters_found: int = 0
    decisions: list[GateDecision] = Field(default_factory=list)
    promoted: list[str] = Field(default_factory=list)
    merged: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    decisions_log: str = ""
    dry_run: bool = False


def _load_skills(skills_dir: str) -> list[Skill]:
    skills: list[Skill] = []
    if not os.path.isdir(skills_dir):
        return skills
    for fname in sorted(os.listdir(skills_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(skills_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                skills.append(Skill(**json.load(f)))
        except Exception:
            continue  # skip files that are not valid skills
    return skills


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _append_jsonl(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def run_discovery(
    tasks_path: str,
    traces_path: str,
    skills_dir: str,
    *,
    llm: Optional[BaseLLM] = None,
    embedder=None,
    judge: Optional[JudgeFn] = None,
    run_frontier: Optional[RunFrontierFn] = None,
    run_skill: Optional[RunSkillFn] = None,
    mock: Optional[bool] = None,
    min_cluster_size: int = 3,
    distance_threshold: float = 0.35,
    max_candidates: int = 5,
    validation_tasks: int = 3,
    min_judge_score: float = 0.75,
    require_all_pass: bool = True,
    merge_threshold: float = 0.80,
    reject_threshold: float = 0.92,
    decisions_log_path: Optional[str] = None,
    dry_run: bool = False,
) -> DiscoveryReport:
    """Run one discovery batch over the trace history.

    Integrations (judge, run_frontier, run_skill) are injected so the gate
    can call the proof agent's judge panel and the runner's execution paths.
    When they are absent, candidates are filed as pending - never promoted
    on unmeasured numbers.
    """
    llm = llm or get_llm(mock=mock)
    embedder = embedder or get_embedder(mock=mock)
    decisions_log_path = decisions_log_path or os.path.join(
        os.path.dirname(traces_path) or ".", "discovery.jsonl"
    )

    report = DiscoveryReport(
        llm=llm.name,
        embedder=getattr(embedder, "name", type(embedder).__name__),
        decisions_log=decisions_log_path,
        dry_run=dry_run,
    )

    tasks = load_tasks(tasks_path)
    traces = load_traces(traces_path)
    tasks_by_id = {t.id: t for t in tasks}
    skills = _load_skills(skills_dir)
    report.n_tasks = len(tasks)
    report.n_traces = len(traces)
    report.n_existing_skills = len(skills)

    clusters = cluster_history(
        tasks,
        traces,
        embedder,
        min_cluster_size=min_cluster_size,
        distance_threshold=distance_threshold,
    )
    report.clusters_found = len(clusters)

    taken_ids = {s.id for s in skills}
    for cluster in clusters[:max_candidates]:
        cluster = cluster.model_copy(
            update={"task_ids": [tid for tid in cluster.task_ids if tid in tasks_by_id]}
        )
        if not cluster.task_ids:
            continue

        candidate = propose_skill(
            cluster,
            tasks_by_id,
            llm,
            taken_skill_ids=taken_ids,
            existing_skill_names=[s.name for s in skills],
        )

        overlap = overlap_check(
            candidate, skills, embedder,
            merge_threshold=merge_threshold, reject_threshold=reject_threshold,
        )
        if overlap.decision == "reject":
            decision = GateDecision(
                cluster_id=cluster.id,
                decision="reject",
                skill_id=candidate.id,
                merge_target_id=overlap.target_id,
                reasons=[
                    f"overlap_check: duplicate of {overlap.target_id} "
                    f"(similarity {overlap.max_similarity})"
                ],
            )
        elif overlap.decision == "merge":
            target = next(s for s in skills if s.id == overlap.target_id)
            merged = merge_skills(target, candidate)
            decision = GateDecision(
                cluster_id=cluster.id,
                decision="merge",
                skill_id=candidate.id,
                merge_target_id=target.id,
                reasons=[
                    f"overlap_check: merged into {target.id} "
                    f"(similarity {overlap.max_similarity}); "
                    f"{len(candidate.parts)} new part(s) appended"
                ],
            )
            if not dry_run:
                _write_json(
                    os.path.join(skills_dir, f"{target.id}.json"),
                    merged.model_dump(),
                )
        else:
            decision = judge_gate(
                cluster,
                candidate,
                tasks_by_id,
                judge=judge,
                run_frontier=run_frontier,
                run_skill=run_skill,
                k=validation_tasks,
                min_judge_score=min_judge_score,
                require_all_pass=require_all_pass,
            )
            if decision.decision == "promote":
                if not dry_run:
                    _write_json(
                        os.path.join(skills_dir, f"{candidate.id}.json"),
                        candidate.model_dump(),
                    )
                skills.append(candidate)
                taken_ids.add(candidate.id)
            elif decision.decision in {"pending_judge", "pending_runner"}:
                if not dry_run:
                    _write_json(
                        os.path.join(
                            skills_dir, CANDIDATES_DIRNAME, f"{candidate.id}.json"
                        ),
                        candidate.model_dump(),
                    )

        report.decisions.append(decision)
        if not dry_run:
            record = decision.model_dump()
            record["ts"] = datetime.now(timezone.utc).astimezone().isoformat()
            _append_jsonl(decisions_log_path, record)

    for d in report.decisions:
        key = d.skill_id or "(unknown)"
        if d.decision == "promote":
            report.promoted.append(key)
        elif d.decision == "merge":
            report.merged.append(f"{key} -> {d.merge_target_id}")
        elif d.decision == "reject":
            report.rejected.append(key)
        else:
            report.pending.append(key)
    return report
