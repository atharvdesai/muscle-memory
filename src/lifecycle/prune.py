"""Lifecycle: prune skills that stop paying off.

A skill is pruned when measured trace data shows ANY of:
  - LOW HIT RATE: since the skill was created, the share of runs that used
    it fell below min_hit_rate (with enough runs to be fair).
  - QUALITY DECAY: judged quality on the skill's recent runs is below
    min_quality, or dropped sharply versus its earlier runs.
  - NEGATIVE SAVINGS: the skill's recorded savings went negative, or its
    measured cost per run is no longer cheaper than the frontier path.

Skills with insufficient data are always kept. Pruned skills are moved to
src/skills/_pruned/ (recoverable for the demo narrative) and every decision
is appended to src/traces/lifecycle.jsonl with the measured numbers.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from ..discovery.cluster import load_traces
from ..discovery.models import Skill

PRUNED_DIRNAME = "_pruned"


class PruneDecision(BaseModel):
    skill_id: str
    pruned: bool
    reasons: list[str] = Field(default_factory=list)
    # measured numbers behind the decision
    n_runs_since_creation: int = 0
    n_skill_runs: int = 0
    hit_rate: float | None = None
    recent_mean_quality: float | None = None
    prior_mean_quality: float | None = None
    skill_mean_cost_usd: float | None = None
    frontier_mean_cost_usd: float | None = None


class PruneReport(BaseModel):
    ts: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat()
    )
    decisions: list[PruneDecision] = Field(default_factory=list)
    pruned: list[str] = Field(default_factory=list)
    kept: list[str] = Field(default_factory=list)
    dry_run: bool = False


def _load_skill_files(skills_dir: str) -> list[tuple[str, Skill]]:
    out = []
    if not os.path.isdir(skills_dir):
        return out
    for fname in sorted(os.listdir(skills_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(skills_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                out.append((path, Skill(**json.load(f))))
        except Exception:
            continue
    return out


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def prune_skills(
    skills_dir: str,
    traces_path: str,
    *,
    min_hit_rate: float = 0.05,
    min_eligible_runs: int = 50,
    min_quality: float = 0.6,
    quality_window: int = 5,
    decay_drop: float = 0.15,
    dry_run: bool = False,
    lifecycle_log_path: str | None = None,
) -> PruneReport:
    """Evaluate every active skill against measured trace data.

    Args:
        min_hit_rate: prune when the skill's share of runs since its
            creation falls below this (only once >= min_eligible_runs runs
            have happened, so young skills are never unfairly pruned).
        min_quality: prune when recent judged quality falls below this.
        quality_window: number of recent judged runs compared against the
            previous window for the decay check.
        decay_drop: prune when quality drops by more than this between the
            prior and recent windows.
    """
    lifecycle_log_path = lifecycle_log_path or os.path.join(
        os.path.dirname(traces_path) or ".", "lifecycle.jsonl"
    )
    traces = load_traces(traces_path)
    report = PruneReport(dry_run=dry_run)

    frontier_costs = [t.cost_usd for t in traces if t.route == "frontier"]
    frontier_mean = _mean(frontier_costs)

    for path, skill in _load_skill_files(skills_dir):
        skill_traces = [
            t for t in traces if t.route == "skill" and t.skill_id == skill.id
        ]
        runs_since = [t for t in traces if t.ts >= skill.created_at]
        reasons: list[str] = []

        n_since = len(runs_since)
        n_skill = len(skill_traces)
        hit_rate = (n_skill / n_since) if n_since else None

        # 1) low hit rate
        if n_since >= min_eligible_runs and hit_rate is not None and hit_rate < min_hit_rate:
            reasons.append(
                f"low_hit_rate: used in {n_skill}/{n_since} runs since creation "
                f"({hit_rate:.3f} < {min_hit_rate})"
            )

        # 2) quality decay (judged runs only, chronological)
        judged = sorted(
            (t for t in skill_traces if t.quality_score is not None),
            key=lambda t: t.ts,
        )
        scores = [t.quality_score for t in judged if t.quality_score is not None]
        recent_mean = prior_mean = None
        if len(scores) >= quality_window:
            recent_mean = _mean(scores[-quality_window:])
            if recent_mean is not None and recent_mean < min_quality:
                reasons.append(
                    f"quality_decay: recent mean quality {recent_mean:.3f} < {min_quality} "
                    f"over last {quality_window} judged runs"
                )
        if len(scores) >= 2 * quality_window:
            prior_mean = _mean(scores[-2 * quality_window : -quality_window])
            if (
                prior_mean is not None
                and recent_mean is not None
                and prior_mean - recent_mean > decay_drop
            ):
                reasons.append(
                    f"quality_decay: dropped {prior_mean - recent_mean:.3f} "
                    f"({prior_mean:.3f} -> {recent_mean:.3f}) between windows"
                )

        # 3) negative savings - recorded stats AND measured cost check
        if skill.stats.savings_usd < 0:
            reasons.append(
                f"negative_savings: recorded savings_usd is {skill.stats.savings_usd:.4f}"
            )
        skill_mean_cost = _mean([t.cost_usd for t in skill_traces])
        if (
            skill_mean_cost is not None
            and frontier_mean is not None
            and n_skill >= quality_window
            and skill_mean_cost >= frontier_mean
        ):
            reasons.append(
                f"negative_savings: measured skill cost ${skill_mean_cost:.4f}/run "
                f">= frontier ${frontier_mean:.4f}/run over {n_skill} runs"
            )

        decision = PruneDecision(
            skill_id=skill.id,
            pruned=bool(reasons),
            reasons=reasons,
            n_runs_since_creation=n_since,
            n_skill_runs=n_skill,
            hit_rate=round(hit_rate, 4) if hit_rate is not None else None,
            recent_mean_quality=round(recent_mean, 4) if recent_mean is not None else None,
            prior_mean_quality=round(prior_mean, 4) if prior_mean is not None else None,
            skill_mean_cost_usd=(
                round(skill_mean_cost, 6) if skill_mean_cost is not None else None
            ),
            frontier_mean_cost_usd=(
                round(frontier_mean, 6) if frontier_mean is not None else None
            ),
        )
        report.decisions.append(decision)

        if reasons:
            report.pruned.append(skill.id)
            if not dry_run:
                pruned_dir = os.path.join(skills_dir, PRUNED_DIRNAME)
                os.makedirs(pruned_dir, exist_ok=True)
                shutil.move(path, os.path.join(pruned_dir, os.path.basename(path)))
        else:
            report.kept.append(skill.id)

        if not dry_run:
            record = decision.model_dump()
            record["ts"] = datetime.now(timezone.utc).astimezone().isoformat()
            with open(lifecycle_log_path, "a", encoding="utf-8") as f:
                os.makedirs(os.path.dirname(lifecycle_log_path) or ".", exist_ok=True)
                f.write(json.dumps(record) + "\n")

    return report
