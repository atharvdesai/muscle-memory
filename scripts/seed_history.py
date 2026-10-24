#!/usr/bin/env python3
"""Seed run history: the "before skills existed" traffic that discovery
mines to propose skills.

Runs the corpus through the normal decision graph (so traces are real and
measured, never fabricated) BEFORE any skills exist: every task takes the
frontier path. Recurring tasks run --epochs times (that repetition is what
discovery clusters on); one-off control tasks run once and never repeat.

Usage:
  python scripts/seed_history.py [--epochs N] [--reset]

  --epochs N  passes over the recurring tasks (default 2)
  --reset     delete src/traces/runs.jsonl first
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.corpus.generator import build_corpus, one_offs_only, recurring_only  # noqa: E402
from src.router.embeddings import Embedder                                    # noqa: E402
from src.router.graph import DecisionGraph                                    # noqa: E402
from src.runner.llm import LLMClient                                          # noqa: E402
from src.traces.logger import reset_traces                                    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if args.reset:
        reset_traces()

    corpus = build_corpus()
    recurring = recurring_only(corpus)
    one_offs = one_offs_only(corpus)

    client = LLMClient()
    graph = DecisionGraph(client, embedder=Embedder())

    # Guard the demo story: seeding only makes sense on an empty skill web.
    from src.router.skill_store import list_skills
    existing = list_skills()
    if existing:
        print(f"warning: {len(existing)} skill(s) already exist; seeded runs may "
              "route through them. Delete src/skills/*.json for clean history.")

    n = 0
    total_cost = 0.0
    for epoch in range(1, args.epochs + 1):
        for task in recurring:
            trace = graph.route(task)
            total_cost += trace["cost_usd"]
            n += 1
        print(f"epoch {epoch}: {len(recurring)} recurring tasks routed "
              f"(route={trace['route']})")
    for task in one_offs:
        trace = graph.route(task)
        total_cost += trace["cost_usd"]
        n += 1
    print(f"seeded {n} runs ({args.epochs} epochs x {len(recurring)} recurring "
          f"+ {len(one_offs)} one-offs once) | total cost ${total_cost:.4f}")
    print("traces -> src/traces/runs.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
