"""Executor: the two ways a task can run.

- run_frontier: full prompt + full context to the frontier model (the
  expensive default path).
- run_skill:    only the loaded skill parts + the task to the cheap
    skill-tier model. Selective part loading is decided by the router's
    load_parts node; the executor just consumes what it is given.
"""
from __future__ import annotations

from .llm import Completion, LLMClient

FRONTIER_SYSTEM = (
    "You are an expert software engineer. Answer the task directly and "
    "completely, using the provided context."
)

SKILL_SYSTEM_TEMPLATE = (
    "You are executing a learned skill.\n"
    "Skill: {name}\n"
    "Responsibility (do exactly this one thing): {responsibility}\n\n"
    "{parts_block}\n"
    "Follow the loaded skill parts precisely. Do not improvise beyond them."
)


def run_frontier(client: LLMClient, task) -> Completion:
    user = f"{task.prompt}\n\nContext:\n{task.context}"
    return client.complete(FRONTIER_SYSTEM, user, tier="frontier")


def run_skill(client: LLMClient, task, skill: dict, loaded_parts: list[dict]) -> Completion:
    blocks = []
    for part in loaded_parts:
        if part.get("kind") == "instructions":
            blocks.append(f"Instructions ({part['id']}):\n{part['content']}")
        else:
            blocks.append(
                f"Helper script ({part['id']}) - use this approach:\n{part['content']}"
            )
    parts_block = "\n\n".join(blocks) if blocks else "(no parts loaded)"
    system = SKILL_SYSTEM_TEMPLATE.format(
        name=skill.get("name", skill.get("id", "skill")),
        responsibility=skill.get("responsibility", ""),
        parts_block=parts_block,
    )
    user = f"{task.prompt}\n\nContext:\n{task.context}"
    return client.complete(system, user, tier="skill")
