"""Rubric for the judge panel.

Task-agnostic on purpose: the router handles arbitrary repeated workflows, so
the judge scores generic output quality rather than any one task family. Each
dimension is scored 1-5 by every panel member; the weighted mean, normalized
to [0, 1], is the trace's `quality_score`.
"""

from __future__ import annotations

RUBRIC = [
    {
        "name": "correctness",
        "weight": 0.40,
        "description": "The output is correct for the task: no factual, logical, or transformation errors given the prompt and context.",
    },
    {
        "name": "completeness",
        "weight": 0.25,
        "description": "The output covers everything the prompt asked for; nothing required is missing or truncated.",
    },
    {
        "name": "instruction_following",
        "weight": 0.20,
        "description": "The output respects the prompt's format, length, tone, and structural constraints.",
    },
    {
        "name": "concision",
        "weight": 0.15,
        "description": "The output is as short as it can be without losing required content; no padding or repetition.",
    },
]

# Pairwise verdicts closer than this are a tie - the gate treats a tie as
# "skill does not regress quality", which is the property the demo shows as
# the flat quality line.
TIE_MARGIN = 0.05


def weighted_quality(dim_scores: dict[str, float]) -> float:
    """Weighted mean of 1-5 dimension scores, normalized to [0, 1]."""
    total = 0.0
    for dim in RUBRIC:
        total += dim["weight"] * float(dim_scores.get(dim["name"], 1.0))
    return max(0.0, min(1.0, total / 5.0))


def rubric_prompt_text() -> str:
    lines = []
    for dim in RUBRIC:
        lines.append(f'- "{dim["name"]}" (weight {dim["weight"]}): {dim["description"]}')
    return "\n".join(lines)
