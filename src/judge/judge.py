"""Core judge interface + the discovery `judge_gate` callable.

Contract highlights (see README.md for the full spec):

- Judge.compare() scores a skill-path output and a frontier-path output for
  the SAME task through the same panel and returns both quality scores plus
  a winner ("skill" | "frontier" | "tie").
- judge_gate() is the only promotion-gate entry point. It requires at least
  `min_tasks` measured pairs, passes only when measured skill quality does
  not regress beyond `quality_tolerance` AND measured savings clear
  `min_savings_usd`. It cannot be bypassed: there is no flag, no default
  pass, and every number in the verdict comes from a run it executed itself.
- The judge never reads Task.type. It ignores the field if present.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .llm import LLMClient
from .panel import HeuristicJudge, LLMPanelJudge, PanelScore
from .rubric import RUBRIC, TIE_MARGIN, weighted_quality


@dataclass
class JudgeConfig:
    panel_size_note: str = "3 stances, alternating A/B order"  # informational
    tie_margin: float = TIE_MARGIN
    force_mock: bool = False   # also settable via MUSCLE_MEMORY_MOCK=1


@dataclass
class SideResult:
    label: str                          # "skill" | "frontier"
    output: str
    dim_scores: dict[str, float]        # dimension -> mean 1-5
    quality_score: float                # weighted, normalized [0, 1]
    rationales: list[str] = field(default_factory=list)
    member_scores: list[float] = field(default_factory=list)  # per-stance weighted quality


@dataclass
class PairResult:
    task_id: str
    skill: SideResult
    frontier: SideResult
    winner: str                         # "skill" | "frontier" | "tie"
    judge_mode: str                     # "llm-panel" | "heuristic-mock"
    panel_members: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateVerdict:
    passed: bool
    reason: str
    measured: dict                      # every number here came from a real run
    pairs: list[dict] = field(default_factory=list)  # per-task PairResult dicts

    def to_dict(self) -> dict:
        return asdict(self)


class Judge:
    """Rubric-based judge. LLM panel when keys exist, heuristic mock otherwise.

    Also importable as JudgePanel (alias expected by src/discovery/gate.py)."""

    def __init__(self, config: JudgeConfig | None = None, client: LLMClient | None = None):
        self.config = config or JudgeConfig()
        if self.config.force_mock:
            os.environ["MUSCLE_MEMORY_MOCK"] = "1"
        self.client = client or LLMClient()
        self._panel = HeuristicJudge() if self.client.is_mock else LLMPanelJudge(self.client)

    @property
    def mode(self) -> str:
        return "heuristic-mock" if self.client.is_mock else "llm-panel"

    # -- single-sided scoring: fills Trace.quality_score -------------------
    def score(self, task: dict, output: str) -> float:
        """Quality score in [0, 1] for one output. Used to fill trace rows."""
        side, _ = self._score_against_reference(task, output)
        return side.quality_score

    # -- pairwise comparison ------------------------------------------------
    def compare(self, task: dict, skill_output: str, frontier_output: str) -> PairResult:
        """Score both paths on the same task through the same panel."""
        skill_ps, frontier_ps = self._panel.score_pair(task, skill_output, frontier_output)
        skill = SideResult("skill", skill_output, skill_ps.dim_scores,
                           weighted_quality(skill_ps.dim_scores), skill_ps.rationales,
                           skill_ps.member_qualities)
        frontier = SideResult("frontier", frontier_output, frontier_ps.dim_scores,
                              weighted_quality(frontier_ps.dim_scores), frontier_ps.rationales,
                              frontier_ps.member_qualities)
        delta = skill.quality_score - frontier.quality_score
        winner = "tie" if abs(delta) < self.config.tie_margin else ("skill" if delta > 0 else "frontier")
        members = max(skill_ps.members_used, frontier_ps.members_used)
        return PairResult(task.get("id", "unknown"), skill, frontier, winner,
                          self.mode, members)

    def _score_against_reference(self, task: dict, output: str):
        # Implemented as a pair against an empty control so both sides of the
        # panel share one code path; the control side is discarded.
        pair = self.compare(task, output, "")
        return pair.skill, pair.frontier


# --- comparison evidence log -------------------------------------------------
# Every pairwise scoring (judge_pair or judge_gate) appends one line here so
# the dashboard's Proof view can show both outputs, both quality scores, and
# the winner for the SAME task. Set MUSCLE_MEMORY_COMPARISONS_LOG="" to disable.
DEFAULT_COMPARISONS_LOG = os.environ.get(
    "MUSCLE_MEMORY_COMPARISONS_LOG", "src/traces/comparisons.jsonl")


def _log_comparison(record: dict, log_path: str | None = None) -> None:
    path = DEFAULT_COMPARISONS_LOG if log_path is None else log_path
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # evidence logging must never break the gate


def _clip(text: str, limit: int = 8000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def judge_pair(task: dict, frontier_output: str, skill_output: str,
               judge: "Judge | None" = None,
               log_path: str | None = None,
               usage_frontier: dict | None = None,
               usage_skill: dict | None = None) -> dict:
    """Discovery-gate contract: score a skill output against a frontier output.

    This is the exact signature src/discovery/gate.py imports:
        from src.judge import judge_pair
        verdict = judge_pair(task, frontier_output, skill_output)
    Returns {"score": float 0..1, "passed": bool, "rationale": str} where
    `score` is the SKILL path's quality score and `passed` means the skill
    path did not regress beyond the tie margin. Logs both outputs to the
    comparisons evidence log for the dashboard Proof view.
    """
    j = judge or Judge()
    pair = j.compare(task, skill_output, frontier_output)
    passed = pair.skill.quality_score >= pair.frontier.quality_score - j.config.tie_margin
    rationale = ("; ".join((pair.skill.rationales + pair.frontier.rationales)[:2])
                 or f"winner: {pair.winner}")
    record = {
        "task_id": pair.task_id,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt": _clip(task.get("prompt", ""), 2000),
        "context": _clip(task.get("context", ""), 4000),
        "frontier_output": _clip(frontier_output),
        "skill_output": _clip(skill_output),
        "quality_frontier": round(pair.frontier.quality_score, 4),
        "quality_skill": round(pair.skill.quality_score, 4),
        "winner": pair.winner,
        "passed": passed,
        "judge_mode": pair.judge_mode,
        "panel_members": pair.panel_members,
        "panel_scores": {
            "stances": ["strict and skeptical", "balanced", "generous but honest"],
            "skill": pair.skill.member_scores,
            "frontier": pair.frontier.member_scores,
        },
        "rationales_full": {
            "skill": [_clip(r, 800) for r in pair.skill.rationales],
            "frontier": [_clip(r, 800) for r in pair.frontier.rationales],
        },
        "rationale": _clip(rationale, 1000),
    }
    if usage_frontier is not None:
        record["frontier_usage"] = usage_frontier
    if usage_skill is not None:
        record["skill_usage"] = usage_skill
    _log_comparison(record, log_path)
    return {"score": round(pair.skill.quality_score, 4),
            "passed": bool(passed), "rationale": rationale}


def _run_one(runner, task) -> tuple[str, dict]:
    """Normalize a runner result.

    Runners may return either a plain output string, or a dict
    {"output": str, "tokens_in": int, "tokens_out": int, "cost_usd": float}
    when they have measured usage. Savings are only computed from the dict
    form - strings mean "usage unknown", never "usage zero".
    """
    res = runner(task)
    if isinstance(res, dict) and "output" in res:
        return str(res["output"]), {
            "tokens_in": res.get("tokens_in"),
            "tokens_out": res.get("tokens_out"),
            "cost_usd": res.get("cost_usd"),
        }
    return str(res), {"tokens_in": None, "tokens_out": None, "cost_usd": None}


def judge_gate(judge: Judge,
               tasks: list[dict],
               run_skill: Callable[[dict], object],
               run_frontier: Callable[[dict], object],
               measured_savings_usd: float | None = None,
               min_tasks: int = 3,
               quality_tolerance: float = 0.05,
               min_savings_usd: float = 0.0) -> GateVerdict:
    """The promotion gate's judge node. Never bypassed, never faked.

    Args:
        judge: a Judge instance.
        tasks: held-out tasks for the candidate cluster, Task schema with
            `type` stripped by the caller (ignored here regardless).
        run_skill / run_frontier: callables executing one task on each path.
            Return a plain output string, or a dict with measured usage (see
            _run_one). Each task is executed on BOTH paths by this function.
        measured_savings_usd: savings measured by discovery from the trace
            log for the candidate's cluster (None = not yet measurable).
        min_tasks: minimum measured pairs required for a pass. Below this the
            gate FAILS CLOSED with reason "insufficient_evidence".
        quality_tolerance: skill mean quality must be >= frontier mean minus
            this margin (a tie keeps the flat-quality property).
        min_savings_usd: when measured_savings_usd is provided, it must clear
            this bar. When None, savings are reported as "unmeasured" and the
            quality bar alone decides - the savings check stays with
            discovery's trace-based measurement, not skipped globally.

    Returns:
        GateVerdict with every number measured by this call.
    """
    clean_tasks = [{k: v for k, v in t.items() if k != "type"} for t in tasks]
    if len(clean_tasks) < min_tasks:
        return GateVerdict(
            passed=False,
            reason=f"insufficient_evidence: {len(clean_tasks)} tasks < min_tasks={min_tasks}",
            measured={"n_pairs": len(clean_tasks), "min_tasks": min_tasks},
        )

    pairs: list[PairResult] = []
    skill_costs, frontier_costs = [], []
    for task in clean_tasks:
        skill_out, skill_usage = _run_one(run_skill, task)
        frontier_out, frontier_usage = _run_one(run_frontier, task)
        if skill_usage["cost_usd"] is not None:
            skill_costs.append(float(skill_usage["cost_usd"]))
        if frontier_usage["cost_usd"] is not None:
            frontier_costs.append(float(frontier_usage["cost_usd"]))
        pair = judge.compare(task, skill_out, frontier_out)
        pairs.append(pair)
        _log_comparison({
            "task_id": pair.task_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prompt": _clip(task.get("prompt", ""), 2000),
            "context": _clip(task.get("context", ""), 4000),
            "frontier_output": _clip(frontier_out),
            "skill_output": _clip(skill_out),
            "quality_frontier": round(pair.frontier.quality_score, 4),
            "quality_skill": round(pair.skill.quality_score, 4),
            "cost_frontier_usd": frontier_usage["cost_usd"],
            "cost_skill_usd": skill_usage["cost_usd"],
            "winner": pair.winner,
            "judge_mode": pair.judge_mode,
            "rationale": _clip("; ".join(
                (pair.skill.rationales + pair.frontier.rationales)[:2]), 1000),
        })

    n = len(pairs)
    skill_mean = sum(p.skill.quality_score for p in pairs) / n
    frontier_mean = sum(p.frontier.quality_score for p in pairs) / n
    wins = sum(1 for p in pairs if p.winner == "skill")
    ties = sum(1 for p in pairs if p.winner == "tie")
    losses = n - wins - ties

    measured = {
        "n_pairs": n,
        "judge_mode": judge.mode,
        "skill_quality_mean": round(skill_mean, 4),
        "frontier_quality_mean": round(frontier_mean, 4),
        "quality_delta": round(skill_mean - frontier_mean, 4),
        "wins": wins, "ties": ties, "losses": losses,
        "skill_cost_usd_total": round(sum(skill_costs), 6) if skill_costs else None,
        "frontier_cost_usd_total": round(sum(frontier_costs), 6) if frontier_costs else None,
        "measured_savings_usd": measured_savings_usd,
    }

    if skill_mean < frontier_mean - quality_tolerance:
        return GateVerdict(
            passed=False,
            reason=(f"quality_regression: skill {skill_mean:.3f} < frontier "
                    f"{frontier_mean:.3f} - tolerance {quality_tolerance}"),
            measured=measured,
            pairs=[p.to_dict() for p in pairs],
        )
    if measured_savings_usd is not None and measured_savings_usd < min_savings_usd:
        return GateVerdict(
            passed=False,
            reason=(f"insufficient_savings: measured {measured_savings_usd:.4f} USD "
                    f"< min {min_savings_usd:.4f}"),
            measured=measured,
            pairs=[p.to_dict() for p in pairs],
        )

    savings_note = ("unmeasured (deferred to trace-based savings check)"
                    if measured_savings_usd is None
                    else f"{measured_savings_usd:.4f} USD measured")
    return GateVerdict(
        passed=True,
        reason=(f"quality held (delta {skill_mean - frontier_mean:+.3f} over {n} "
                f"measured pairs); savings {savings_note}"),
        measured=measured,
        pairs=[p.to_dict() for p in pairs],
    )


# Alias expected by src/discovery/gate.py's adaptive import.
JudgePanel = Judge
