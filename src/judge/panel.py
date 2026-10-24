"""The judge panel: three independent judges, position-swap debiasing.

Two implementations behind one interface:

- LLMPanelJudge: three LLM calls per pair with distinct judging stances;
  output presentation order alternates between members so position bias
  cancels in the aggregate. Any member that fails to return parseable JSON is
  dropped; if every member fails, the caller falls back to the heuristic
  judge and the result is marked as such (never silently faked).
- HeuristicJudge: fully deterministic, no network. Used in mock mode so the
  whole repo runs without API keys. Scores come from measurable features of
  the output (similarity to the task's reference answer when one exists,
  length fit, format cues), so mock numbers are stable run-to-run.

Both return per-dimension scores for each side; judge.py aggregates them into
PairResult.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field

from .llm import LLMClient
from .rubric import RUBRIC, rubric_prompt_text, weighted_quality

_DIM_NAMES = [d["name"] for d in RUBRIC]


@dataclass
class PanelScore:
    """One side's aggregate scores across the panel."""
    dim_scores: dict[str, float]          # dimension -> mean 1-5
    rationales: list[str] = field(default_factory=list)
    members_used: int = 0
    member_qualities: list[float] = field(default_factory=list)  # per-stance weighted quality
    member_models: list[str] = field(default_factory=list)       # model each stance ran on


_SYSTEM = (
    "You are one member of a judging panel for an AI-agent skill router. "
    "You score two candidate outputs for the same task, blind to which system "
    "produced which. Score each output on every rubric dimension from 1 "
    "(worst) to 5 (best). Reply with ONLY a JSON object, no prose."
)


def _member_prompt(stance: str, task_prompt: str, context: str,
                   expected: str | None, out_a: str, out_b: str) -> str:
    ref = f"\nReference answer (for grounding, not exact-match):\n{expected}\n" if expected else ""
    return (
        f"Judging stance: {stance}.\n\n"
        f"Rubric:\n{rubric_prompt_text()}\n\n"
        f"TASK PROMPT:\n{task_prompt}\n\n"
        f"TASK CONTEXT:\n{context}\n"
        f"{ref}\n"
        f"OUTPUT A:\n{out_a}\n\n"
        f"OUTPUT B:\n{out_b}\n\n"
        'Reply with exactly: {"A": {"correctness": n, "completeness": n, '
        '"instruction_following": n, "concision": n, "rationale": "..."}, '
        '"B": {same shape}}'
    )


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _clamp15(x) -> float:
    try:
        return max(1.0, min(5.0, float(x)))
    except (TypeError, ValueError):
        return 1.0


class LLMPanelJudge:
    """Three-member LLM panel with alternating A/B presentation order."""

    STANCES = ("strict and skeptical", "balanced", "generous but honest")

    def __init__(self, client: LLMClient):
        self.client = client

    def score_pair(self, task: dict, skill_output: str,
                   frontier_output: str) -> tuple[PanelScore, PanelScore]:
        prompt, context = task.get("prompt", ""), task.get("context", "")
        expected = task.get("expected")
        per_dim_skill = {d: [] for d in _DIM_NAMES}
        per_dim_frontier = {d: [] for d in _DIM_NAMES}
        rationales_skill, rationales_frontier = [], []
        members_used = 0
        member_models: list[str] = []
        # Optional per-stance model overrides so a panel can mix tiers (e.g.
        # one Sonnet stance) - set for ablation runs, empty in normal takes.
        overrides = [m.strip() for m in os.environ.get(
            "MUSCLE_MEMORY_PANEL_MODELS", "").split(",") if m.strip()]

        for i, stance in enumerate(self.STANCES):
            # Alternate which output is shown as "A" to cancel position bias.
            swap = i % 2 == 1
            out_a, out_b = (frontier_output, skill_output) if swap else (skill_output, frontier_output)
            member_client = self.client
            if i < len(overrides):
                member_client = LLMClient(model=overrides[i])
            resp = member_client.complete(
                _SYSTEM,
                _member_prompt(stance, prompt, context, expected, out_a, out_b),
                max_tokens=600,
                temperature=0.0,
            )
            member_models.append(getattr(resp, "model", overrides[i] if i < len(overrides) else "default"))
            parsed = _extract_json(resp.text)
            if not parsed or "A" not in parsed or "B" not in parsed:
                continue
            a, b = parsed["A"], parsed["B"]
            skill_side, frontier_side = (b, a) if swap else (a, b)
            for d in _DIM_NAMES:
                per_dim_skill[d].append(_clamp15(skill_side.get(d)))
                per_dim_frontier[d].append(_clamp15(frontier_side.get(d)))
            if skill_side.get("rationale"):
                rationales_skill.append(str(skill_side["rationale"]))
            if frontier_side.get("rationale"):
                rationales_frontier.append(str(frontier_side["rationale"]))
            members_used += 1

        def mean(xs):
            return sum(xs) / len(xs) if xs else 1.0

        def per_member(per_dim):
            return [round(weighted_quality({d: per_dim[d][i] for d in _DIM_NAMES}), 4)
                    for i in range(members_used)]

        skill = PanelScore({d: mean(per_dim_skill[d]) for d in _DIM_NAMES},
                           rationales_skill, members_used, per_member(per_dim_skill),
                           member_models)
        frontier = PanelScore({d: mean(per_dim_frontier[d]) for d in _DIM_NAMES},
                              rationales_frontier, members_used, per_member(per_dim_frontier),
                              member_models)
        return skill, frontier


class HeuristicJudge:
    """Deterministic mock judge. No network, stable across runs.

    Scores are derived from measurable features of each output relative to
    the task: reference similarity when `expected` is present, length fit
    against the task's demand, and format cues. Skill and frontier outputs
    are scored through the identical function, which is what makes the
    mock-mode quality line honestly flat instead of hard-coded.
    """

    def score_pair(self, task: dict, skill_output: str,
                   frontier_output: str) -> tuple[PanelScore, PanelScore]:
        skill = self._score_side(task, skill_output)
        frontier = self._score_side(task, frontier_output)
        return skill, frontier

    def _score_side(self, task: dict, output: str) -> PanelScore:
        output = output or ""
        expected = task.get("expected") or ""
        prompt = task.get("prompt", "")
        context = task.get("context", "")

        # --- correctness: similarity to reference when available, else
        # keyword coverage of prompt+context terms in the output.
        if expected.strip():
            correctness = 1.0 + 4.0 * difflib.SequenceMatcher(
                None, output.strip().lower(), expected.strip().lower()).ratio()
        else:
            terms = {t.strip(".,:;!?()[]\"'").lower()
                     for t in (prompt + " " + context).split() if len(t) > 5}
            if terms:
                hits = sum(1 for t in terms if t in output.lower())
                coverage = hits / len(terms)
            else:
                coverage = 0.5
            correctness = 2.5 + 2.5 * min(1.0, coverage * 3.0)

        # --- completeness: length fit. Target band scales with the
        # reference (or a default) and penalizes both truncation and bloat.
        target = max(60.0, float(len(expected) if expected.strip() else 240))
        ratio = len(output) / target if output else 0.0
        if ratio <= 0.0:
            completeness = 1.0
        elif ratio < 0.25:
            completeness = 2.0
        elif ratio < 0.5:
            completeness = 3.5
        elif ratio <= 3.0:
            completeness = 5.0
        elif ratio <= 5.0:
            completeness = 3.5
        else:
            completeness = 2.5

        # --- instruction_following: lightweight format cues from the prompt.
        p = prompt.lower()
        fmt = 3.5
        if "commit" in p:
            fmt = 5.0 if re.match(r"(?i)^(feat|fix|docs|style|refactor|test|chore|perf|ci|build)(\(.+\))?!?:",
                                  output.strip()) else 2.5
        elif "bullet" in p or "list" in p:
            fmt = 5.0 if re.search(r"(?m)^\s*[-*\u2022]", output) else 2.5
        elif "json" in p:
            try:
                json.loads(output)
                fmt = 5.0
            except ValueError:
                fmt = 2.0
        elif "one sentence" in p or "single sentence" in p:
            fmt = 5.0 if output.strip().count(".") <= 1 else 2.5

        # --- concision: penalize repetition and empties.
        words = output.split()
        if not words:
            concision = 1.0
        else:
            unique_ratio = len({w.lower() for w in words}) / len(words)
            concision = 2.0 + 3.0 * min(1.0, unique_ratio * 1.4)

        return PanelScore(
            dim_scores={
                "correctness": round(_clamp15(correctness), 3),
                "completeness": round(_clamp15(completeness), 3),
                "instruction_following": round(_clamp15(fmt), 3),
                "concision": round(_clamp15(concision), 3),
            },
            rationales=["heuristic mock judge (no API key)"],
            members_used=1,
        )
