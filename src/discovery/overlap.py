"""overlap_check: no two skills in the web cover the same ground.

Embedding similarity between the candidate and every existing skill's
responsibility + parts (per the frozen contract):
  similarity >= reject_threshold -> REJECT as duplicate
  similarity >= merge_threshold  -> MERGE candidate's parts into that skill
  otherwise                      -> OK, candidate covers new ground
"""
from __future__ import annotations

from dataclasses import dataclass

from .embeddings import cosine_similarity_matrix
from .models import Skill


@dataclass
class OverlapResult:
    decision: str  # "ok" | "merge" | "reject"
    target_id: str | None
    max_similarity: float


def overlap_check(
    candidate: Skill,
    existing_skills: list[Skill],
    embedder,
    *,
    merge_threshold: float = 0.80,
    reject_threshold: float = 0.92,
) -> OverlapResult:
    if not existing_skills:
        return OverlapResult("ok", None, 0.0)

    vecs = embedder.embed(
        [candidate.embedding_text()] + [s.embedding_text() for s in existing_skills]
    )
    sims = cosine_similarity_matrix(vecs[:1], vecs[1:])[0]
    best_idx = int(sims.argmax())
    best = float(sims[best_idx])
    target = existing_skills[best_idx].id

    if best >= reject_threshold:
        return OverlapResult("reject", target, round(best, 4))
    if best >= merge_threshold:
        return OverlapResult("merge", target, round(best, 4))
    return OverlapResult("ok", None, round(best, 4))


def merge_skills(target: Skill, candidate: Skill) -> Skill:
    """Merge a candidate into an existing skill: new parts only.

    The target keeps its identity, responsibility, trigger and stats; the
    candidate's parts are appended with fresh ids. This is how the web grows
    richer skills instead of duplicates.
    """
    offset = len(target.parts)
    for i, part in enumerate(candidate.parts, 1):
        target.parts.append(
            type(part)(id=f"p{offset + i}", kind=part.kind, content=part.content)
        )
    return target
