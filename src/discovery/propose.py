"""propose_skill: turn one repeated-workflow cluster into an atomic skill candidate."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .llm import PROPOSE_MARKER, BaseLLM
from .models import Cluster, Skill, SkillPart, Task

SYSTEM_PROPOSE = """You generate skill candidates for an agent skill web.

You will see samples of one repeated workflow found in an agent's run history.
Propose ONE skill that handles that workflow cheaply.

Rules:
- ATOMIC: exactly ONE responsibility - a single verb phrase. If the samples
  mix two different jobs, pick the dominant one. Never propose a skill that
  does two things.
- parts: 2 to 4 ATOMIC parts, each covering exactly one aspect of the
  skill, so a run can load only the parts it needs. Split along natural
  seams, e.g. core procedure / output format spec / edge cases / worked
  example. Parts of kind "instructions" carry the procedure; add at most
  one part of kind "script" (runnable Python helper source in `content`)
  only when a deterministic helper genuinely replaces model work. NEVER
  collapse the skill into a single part.
- trigger_description: one sentence a router can match new tasks against.
- Respond with STRICT JSON only, no prose, no code fences:
  {"name": str, "responsibility": str, "trigger_description": str,
   "parts": [{"id": "p1", "kind": "instructions", "content": str},
             {"id": "p2", "kind": "instructions", "content": str}, ...]}
"""

_MAX_SAMPLES = 8
_SAMPLE_PROMPT_CHARS = 300
_SAMPLE_CONTEXT_CHARS = 400


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "unnamed"


def _unique_skill_id(name: str, taken: set[str]) -> str:
    base = f"skill-{_slugify(name)[:48]}"
    candidate, i = base, 2
    while candidate in taken:
        candidate = f"{base}-{i}"
        i += 1
    return candidate


def _parse_candidate_json(raw: str) -> dict:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON object in proposal: {raw[:200]!r}")
        text = text[start : end + 1]
    return json.loads(text)


def _is_atomic(responsibility: str) -> bool:
    """Heuristic guard for the one-responsibility rule.

    Flags compound responsibilities ("X and then Y", "X; Y", "X and also Y").
    A simple "and" inside one activity ("read and summarize the diff") is fine.
    """
    compound_markers = (" and then ", "; ", " and also ", " as well as ", " plus ")
    lowered = f" {responsibility.lower()} "
    return not any(marker in lowered for marker in compound_markers)


def propose_skill(
    cluster: Cluster,
    tasks_by_id: dict[str, Task],
    llm: BaseLLM,
    *,
    taken_skill_ids: set[str] | None = None,
    existing_skill_names: list[str] | None = None,
) -> Skill:
    """Generate an atomic skill candidate for one cluster.

    Uses the configured LLM (deterministic MockLLM in no-key mode). Retries
    once with a stricter instruction if the first response is not parseable
    JSON or fails the atomicity check.
    """
    taken = set(taken_skill_ids or set())
    sample_ids = cluster.task_ids[:_MAX_SAMPLES]
    blocks = []
    for i, tid in enumerate(sample_ids, 1):
        t = tasks_by_id[tid]
        blocks.append(
            f"SAMPLE {i} PROMPT: {t.prompt[:_SAMPLE_PROMPT_CHARS]}\n"
            f"SAMPLE {i} CONTEXT: {t.context[:_SAMPLE_CONTEXT_CHARS]}"
        )
    names = ", ".join(existing_skill_names or []) or "(none yet)"
    user = (
        f"{PROPOSE_MARKER}\n"
        f"Cluster of {cluster.size} repeated tasks. Measured frontier spend so "
        f"far: ${cluster.total_frontier_cost_usd:.4f} total, "
        f"${cluster.mean_frontier_cost_usd:.4f} per task.\n\n"
        + "\n\n".join(blocks)
        + f"\n\nExisting skills (do NOT duplicate these): {names}\n"
    )

    last_error: Exception | None = None
    data: dict | None = None
    for attempt in range(2):
        prompt = user
        if attempt == 1:
            prompt += (
                "\nYour previous answer was rejected: "
                f"{last_error}. Return ONE strict JSON object for ONE atomic "
                "responsibility, nothing else."
            )
        raw = llm.complete(SYSTEM_PROPOSE, prompt)
        try:
            data = _parse_candidate_json(raw)
            if not _is_atomic(str(data.get("responsibility", ""))):
                raise ValueError("responsibility is not atomic")
            parts = [SkillPart(**p) for p in data["parts"]]
            if not any(p.kind == "instructions" for p in parts):
                raise ValueError("candidate has no instructions part")
            if not (2 <= len(parts) <= 4):
                raise ValueError(f"candidate must have 2-4 atomic parts, got {len(parts)}")
            # normalize part ids to p1..pn
            for i, p in enumerate(parts, 1):
                p.id = f"p{i}"
            break
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            last_error = e
            data = None
    if data is None:
        raise ValueError(f"propose_skill failed after retry: {last_error}")

    return Skill(
        id=_unique_skill_id(str(data["name"]), taken),
        name=str(data["name"]),
        responsibility=str(data["responsibility"]).strip(),
        trigger_description=str(data["trigger_description"]).strip(),
        parts=parts,
        created_at=datetime.now(timezone.utc).astimezone().isoformat(),
    )
