"""judge_gate: the promotion gate. Never bypassed, never faked.

Promotion requires, on measured validation runs of sample cluster tasks:
  1. JUDGE: the judge panel scores skill-path output against frontier-path
     output on the same tasks, and the candidate passes.
  2. SAVINGS: measured skill cost < measured frontier cost on those runs.

If the judge or the runner is not available, the candidate is held as
pending - it is NOT promoted on projected or assumed numbers.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Optional

from .models import Cluster, GateDecision, JudgeVerdict, RunResult, Skill, Task

# Integration contracts (implemented by the proof agent and the runner agent):
#   judge: (task: dict, frontier_output: str, skill_output: str)
#          -> dict(score: float 0..1, passed: bool, rationale: str)
#   run_frontier: (task: dict) -> dict(output, tokens_in, tokens_out, latency_ms, cost_usd)
#   run_skill:    (task: dict, skill: dict) -> dict(same shape)
JudgeFn = Callable[[dict, str, str], dict]
RunFrontierFn = Callable[[dict], dict]
RunSkillFn = Callable[[dict, dict], dict]


def _stable_sample(task_ids: list[str], k: int, seed_material: str) -> list[str]:
    """Deterministic sample so validation runs are reproducible."""
    seed = int(hashlib.md5(seed_material.encode()).hexdigest()[:8], 16)
    ranked = sorted(task_ids, key=lambda tid: (seed ^ int(hashlib.md5(tid.encode()).hexdigest()[:8], 16)))
    return ranked[: min(k, len(ranked))]


def judge_gate(
    cluster: Cluster,
    candidate: Skill,
    tasks_by_id: dict[str, Task],
    *,
    judge: Optional[JudgeFn],
    run_frontier: Optional[RunFrontierFn],
    run_skill: Optional[RunSkillFn],
    k: int = 3,
    min_judge_score: float = 0.75,
    require_all_pass: bool = True,
    min_savings_usd_per_task: float = 0.0,
) -> GateDecision:
    """Run the promotion gate for one candidate skill.

    Returns a GateDecision whose measurements all come from real validation
    runs. `k` sample tasks from the cluster are executed both ways
    (frontier and skill) and judged pairwise.
    """
    if judge is None:
        return GateDecision(
            cluster_id=cluster.id,
            decision="pending_judge",
            skill_id=candidate.id,
            reasons=["judge panel not available; candidate held, gate not bypassed"],
        )
    if run_frontier is None or run_skill is None:
        return GateDecision(
            cluster_id=cluster.id,
            decision="pending_runner",
            skill_id=candidate.id,
            reasons=["runner validation path not available; candidate held, savings not projected"],
        )

    sample_ids = _stable_sample(cluster.task_ids, k, seed_material=candidate.id)
    verdicts: list[JudgeVerdict] = []
    frontier_costs: list[float] = []
    skill_costs: list[float] = []
    reasons: list[str] = []

    for tid in sample_ids:
        task = tasks_by_id[tid]
        task_dict = task.model_dump()  # no `type` field exists on this model
        fr = RunResult(**run_frontier(task_dict))
        sk = RunResult(**run_skill(task_dict, candidate.model_dump()))
        try:
            verdict = JudgeVerdict(**judge(
                task_dict, fr.output, sk.output,
                usage_frontier=fr.model_dump(exclude={"output"}),
                usage_skill=sk.model_dump(exclude={"output"}),
            ))
        except TypeError:
            # fallback judge adapters with the plain 3-arg contract
            verdict = JudgeVerdict(**judge(task_dict, fr.output, sk.output))
        verdicts.append(verdict)
        frontier_costs.append(fr.cost_usd)
        skill_costs.append(sk.cost_usd)

    n = len(sample_ids)
    mean_fr = sum(frontier_costs) / n
    mean_sk = sum(skill_costs) / n
    mean_score = sum(v.score for v in verdicts) / n
    savings = mean_fr - mean_sk
    all_passed = all(v.passed for v in verdicts)

    reasons.append(
        f"validation on {n} tasks: mean judge score {mean_score:.3f} "
        f"({n if all_passed else sum(v.passed for v in verdicts)}/{n} passed)"
    )
    reasons.append(
        f"measured cost per task: frontier ${mean_fr:.4f} vs skill ${mean_sk:.4f} "
        f"(savings ${savings:.4f}/task)"
    )
    for tid, v in zip(sample_ids, verdicts):
        if v.rationale:
            reasons.append(f"judge[{tid}]: {v.rationale}")

    judge_ok = mean_score >= min_judge_score and (all_passed or not require_all_pass)
    savings_ok = savings > min_savings_usd_per_task

    if judge_ok and savings_ok:
        return GateDecision(
            cluster_id=cluster.id,
            decision="promote",
            skill_id=candidate.id,
            reasons=reasons,
            n_validation_tasks=n,
            mean_frontier_cost_usd=round(mean_fr, 6),
            mean_skill_cost_usd=round(mean_sk, 6),
            mean_judge_score=round(mean_score, 4),
            savings_usd_per_task=round(savings, 6),
        )

    if not judge_ok:
        reasons.append(
            f"REJECTED: judge bar not met (need mean >= {min_judge_score}"
            + (" and all pass" if require_all_pass else "")
            + ")"
        )
    if not savings_ok:
        reasons.append(
            f"REJECTED: skill path is not cheaper (measured savings ${savings:.4f}/task)"
        )
    return GateDecision(
        cluster_id=cluster.id,
        decision="reject",
        skill_id=candidate.id,
        reasons=reasons,
        n_validation_tasks=n,
        mean_frontier_cost_usd=round(mean_fr, 6),
        mean_skill_cost_usd=round(mean_sk, 6),
        mean_judge_score=round(mean_score, 4),
        savings_usd_per_task=round(savings, 6),
    )
