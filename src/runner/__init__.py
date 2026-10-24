"""Runner package: execution paths + discovery-gate adapters.

The gate (src/discovery/gate.py) imports:
    run_frontier_path(task: dict) -> dict(output, tokens_in, tokens_out, latency_ms, cost_usd)
    run_skill_path(task: dict, skill: dict) -> dict(same shape)

These wrap executor.run_frontier / run_skill (which take a client and typed
objects) so the offline discovery batch can validate candidates without
knowing about LLMClient. Skill validation loads ALL parts: the gate measures
the candidate as proposed; selective part loading is a router-time concern.
"""
from __future__ import annotations

from types import SimpleNamespace

from .executor import run_frontier, run_skill
from .llm import LLMClient

__all__ = ["LLMClient", "run_frontier", "run_skill", "run_frontier_path", "run_skill_path"]


def _task(task: dict) -> SimpleNamespace:
    return SimpleNamespace(prompt=task.get("prompt", ""), context=task.get("context", ""))


def _result(c) -> dict:
    return {
        "output": c.text,
        "tokens_in": c.tokens_in,
        "tokens_out": c.tokens_out,
        "latency_ms": c.latency_ms,
        "cost_usd": c.cost_usd,
    }


def run_frontier_path(task: dict) -> dict:
    return _result(run_frontier(LLMClient(), _task(task)))


def run_skill_path(task: dict, skill: dict) -> dict:
    return _result(run_skill(LLMClient(), _task(task), skill, skill.get("parts", [])))
