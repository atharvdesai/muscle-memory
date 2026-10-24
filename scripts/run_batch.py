#!/usr/bin/env python3
"""Run the corpus through the router's decision graph, logging traces.

Usage:
  python scripts/run_batch.py [--limit N] [--type TYPE] [--quiet]

Every run appends one JSON line per task to src/traces/runs.jsonl with the
exact decision_path taken (frozen trace schema, INTERFACE.md). With no
skills in src/skills/, every task takes the frontier path; after discovery
promotes skills, matching tasks take the skill path and log
skill_parts_loaded.

Provider: LLM_PROVIDER / ANTHROPIC_API_KEY / OPENAI_API_KEY /
GEMINI_API_KEY; MOCK_LLM=1 forces the deterministic offline mock.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.corpus.generator import build_corpus            # noqa: E402
from src.router.embeddings import Embedder               # noqa: E402
from src.router.graph import DecisionGraph               # noqa: E402
from src.runner.llm import LLMClient                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="run only the first N tasks")
    ap.add_argument("--type", default=None, help="run only tasks whose corpus type matches")
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    args = ap.parse_args()

    corpus = build_corpus()
    if args.type:
        corpus = [t for t in corpus if t.type == args.type or t.type.startswith(args.type)]
    if args.limit:
        corpus = corpus[: args.limit]

    client = LLMClient()
    embedder = Embedder()
    graph = DecisionGraph(client, embedder=embedder)

    if not args.quiet:
        print(f"provider={client.provider} embed_backend={embedder.backend} "
              f"threshold={graph.threshold} tasks={len(corpus)}\n")

    total_cost = 0.0
    routes = {"frontier": 0, "skill": 0}
    for task in corpus:
        trace = graph.route(task)
        routes[trace["route"]] += 1
        total_cost += trace["cost_usd"]
        if not args.quiet:
            path = " -> ".join(trace["decision_path"])
            extra = f" [{trace['skill_id']} parts {trace['skill_parts_loaded']}]" \
                if trace["route"] == "skill" else ""
            print(f"{task.id} {trace['route']:8s}{extra} "
                  f"${trace['cost_usd']:.5f} {trace['latency_ms']:>5}ms  {path}")

    print(f"\n{len(corpus)} runs | frontier {routes['frontier']} | skill {routes['skill']} "
          f"| total cost ${total_cost:.4f}")
    print("traces -> src/traces/runs.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
