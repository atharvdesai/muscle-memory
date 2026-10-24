"""LLM-as-judge panel for the muscle-memory promotion gate.

Public interface (frozen for the integrator):

    from src.judge import Judge, judge_gate, PairResult, GateVerdict

    judge = Judge()                      # LLM panel when ANTHROPIC_API_KEY or
                                         # OPENAI_API_KEY is set, deterministic
                                         # heuristic mock otherwise.
    q = judge.score(task, output)        # quality_score in [0, 1]
    pair = judge.compare(task, skill_output, frontier_output)  # PairResult

    verdict = judge_gate(judge, tasks, run_skill, run_frontier,
                         measured_savings_usd=..., min_tasks=3)

`judge_gate` is the ONLY judge-side entry point discovery's `judge_gate`
decision-graph node should call. It runs the candidate skill and the frontier
path on the same held-out tasks, measures quality on both, and refuses to pass
anything that was not measured. It never fabricates numbers and never sees
Task.type (callers pass tasks with the label stripped; this module ignores the
field entirely if present).

See src/judge/README.md for the full contract.
"""

from .judge import (
    GateVerdict,
    Judge,
    JudgeConfig,
    JudgePanel,
    PairResult,
    SideResult,
    judge_gate,
    judge_pair,
)
from .rubric import RUBRIC

__all__ = [
    "GateVerdict",
    "Judge",
    "JudgeConfig",
    "JudgePanel",
    "PairResult",
    "RUBRIC",
    "SideResult",
    "judge_gate",
    "judge_pair",
]
