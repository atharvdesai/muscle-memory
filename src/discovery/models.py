"""Shared models for the discovery + lifecycle slices.

These mirror the frozen contracts in INTERFACE.md. Deliberately, the Task
model here has NO `type` field: discovery must never see the corpus label.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Task(BaseModel):
    """A corpus task as discovery is allowed to see it.

    The frozen contract's `type` label is intentionally absent: loaders strip
    it before this model is built, and discovery code must never read it.
    """

    id: str
    prompt: str
    context: str = ""
    expected: Optional[str] = None


class Trace(BaseModel):
    """One line of src/traces/runs.jsonl (frozen contract)."""

    task_id: str
    ts: str
    route: Literal["frontier", "skill"]
    skill_id: Optional[str] = None
    skill_parts_loaded: Optional[str] = None
    decision_path: list[str] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    quality_score: Optional[float] = None


class SkillPart(BaseModel):
    id: str
    kind: Literal["instructions", "script"]
    content: str


class SkillStats(BaseModel):
    hits: int = 0
    avg_quality: Optional[float] = None
    savings_usd: float = 0.0


class Skill(BaseModel):
    """Frozen skill-file contract (src/skills/<id>.json)."""

    id: str
    name: str
    responsibility: str
    trigger_description: str
    parts: list[SkillPart]
    created_at: str
    stats: SkillStats = Field(default_factory=SkillStats)

    def embedding_text(self) -> str:
        """Text used by overlap_check: responsibility + parts, per contract."""
        body = "\n".join(p.content for p in self.parts)
        return f"{self.responsibility}\n{self.trigger_description}\n{body}"


class Cluster(BaseModel):
    """A repeated workflow found by cluster_history."""

    id: str
    task_ids: list[str]
    size: int
    cohesion: float  # mean pairwise cosine similarity (measured)
    mean_frontier_cost_usd: float = 0.0
    total_frontier_cost_usd: float = 0.0


class JudgeVerdict(BaseModel):
    """What the judge panel returns for one (frontier, skill) output pair.

    This is the callable contract the proof agent implements:
        judge_pair(task: dict, frontier_output: str, skill_output: str) -> dict
    with keys score / passed / rationale.
    """

    score: float  # 0.0 - 1.0
    passed: bool
    rationale: str = ""


class RunResult(BaseModel):
    """What a runner validation call returns:

        run_frontier_path(task: dict) -> dict
        run_skill_path(task: dict, skill: dict) -> dict
    """

    output: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0


class GateDecision(BaseModel):
    """What the promotion pipeline decided about one candidate skill.

    `pending_*` decisions mean a required measured input (judge, runner) was
    unavailable. The gate is never bypassed or faked: nothing is promoted on
    projected numbers.
    """

    cluster_id: str
    decision: Literal["promote", "merge", "reject", "pending_judge", "pending_runner"]
    reasons: list[str] = Field(default_factory=list)
    skill_id: Optional[str] = None
    merge_target_id: Optional[str] = None
    # Measured validation numbers (null when the gate could not run).
    n_validation_tasks: int = 0
    mean_frontier_cost_usd: Optional[float] = None
    mean_skill_cost_usd: Optional[float] = None
    mean_judge_score: Optional[float] = None
    savings_usd_per_task: Optional[float] = None
    decision_path: list[str] = Field(
        default_factory=lambda: [
            "cluster_history",
            "propose_skill",
            "overlap_check",
            "judge_gate",
        ]
    )
