#!/usr/bin/env python3
"""Ablation: plain cheap model (no skill sheet) vs skill path vs frontier.

Answers the critique "your savings are just from using the cheap model":
every corpus task runs three ways -

  plain:    full prompt + full context on the SKILL-TIER model, NO skill
            sheet (run_frontier with tier="skill")
  skill:    the live router path: match -> load needed parts -> skill-tier
            model (only for tasks the router matches; controls stay frontier)
  frontier: full prompt + full context on the frontier model

Judged head-to-head (plain vs frontier, plain vs skill) by the 3-stance
panel with MUSCLE_MEMORY_PANEL_MODELS mixing tiers (one Sonnet stance), so
the cheap model never grades its own work alone. One JSON line per task in
src/traces/ablation.jsonl; re-running skips tasks already present.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.corpus.generator import build_corpus            # noqa: E402
from src.judge import Judge                              # noqa: E402
from src.router.embeddings import Embedder               # noqa: E402
from src.router.graph import DecisionGraph               # noqa: E402
from src.runner.executor import run_frontier             # noqa: E402
from src.runner.llm import LLMClient                     # noqa: E402

ABLATION_LOG = pathlib.Path("src/traces/ablation.jsonl")
ROUTE_TRACES = pathlib.Path("src/traces/ablation_routes.jsonl")  # router traces for this arm, NOT runs.jsonl


class CapturingGraph(DecisionGraph):
    """DecisionGraph that also returns the produced output text."""

    def _log_trace(self, ctx):
        super()._log_trace(ctx)
        ctx["trace"]["output"] = ctx["result"].text


def _usage(c) -> dict:
    return {"output": c.text, "tokens_in": c.tokens_in, "tokens_out": c.tokens_out,
            "latency_ms": c.latency_ms, "cost_usd": c.cost_usd, "model": c.model}


def _verdict(pair, side_a_label, side_b_label):
    w = {"skill": side_a_label, "frontier": side_b_label, "tie": "tie"}[pair.winner]
    return {
        f"quality_{side_a_label}": round(pair.skill.quality_score, 4),
        f"quality_{side_b_label}": round(pair.frontier.quality_score, 4),
        "winner": w,
        "per_stance_" + side_a_label: pair.skill.member_scores,
        "per_stance_" + side_b_label: pair.frontier.member_scores,
        "panel_models": pair.skill.member_models or None,
        "judge_mode": pair.judge_mode,
    }


def main() -> int:
    done = set()
    if ABLATION_LOG.exists():
        for line in ABLATION_LOG.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["task_id"])

    corpus = build_corpus()
    client = LLMClient()
    graph = CapturingGraph(client, embedder=Embedder(), traces_file=ROUTE_TRACES)
    judge = Judge()
    print(f"ablation: {len(corpus)} tasks, {len(done)} already done, "
          f"panel={__import__('os').environ.get('MUSCLE_MEMORY_PANEL_MODELS', 'default (single model)')}")

    with ABLATION_LOG.open("a", encoding="utf-8") as out:
        for task in corpus:
            if task.id in done:
                continue
            td = task.model_dump() if hasattr(task, "model_dump") else vars(task)
            # arm 1: plain cheap model, no skill sheet
            plain = run_frontier(client, task, tier="skill")
            # arm 2: live router path (skill for matches, frontier for controls)
            trace = graph.route(task)
            # arm 3: frontier model
            frontier = run_frontier(client, task)

            rec = {
                "task_id": task.id,
                "task_type": getattr(task, "type", None),
                "route": trace["route"],
                "skill_id": trace.get("skill_id"),
                "skill_parts_loaded": trace.get("skill_parts_loaded"),
                "plain": _usage(plain),
                "frontier": _usage(frontier),
                "skillpath": {"output": trace["output"], "tokens_in": trace["tokens_in"],
                              "tokens_out": trace["tokens_out"], "latency_ms": trace["latency_ms"],
                              "cost_usd": trace["cost_usd"], "model": trace.get("model")},
                "judge": {"plain_vs_frontier": _verdict(
                    judge.compare(td, plain.text, frontier.text), "plain", "frontier")},
            }
            if trace["route"] == "skill":
                rec["judge"]["plain_vs_skill"] = _verdict(
                    judge.compare(td, plain.text, trace["output"]), "plain", "skill")
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(f"{task.id}: route={trace['route']} "
                  f"plain=${rec['plain']['cost_usd']:.4f} skill=${rec['skillpath']['cost_usd']:.4f} "
                  f"frontier=${rec['frontier']['cost_usd']:.4f} "
                  f"p-v-f={rec['judge']['plain_vs_frontier']['winner']}"
                  + (f" p-v-s={rec['judge']['plain_vs_skill']['winner']}" if trace["route"] == "skill" else ""))

    # summary
    recs = [json.loads(l) for l in ABLATION_LOG.read_text().splitlines() if l.strip()]
    def avg(xs): return sum(xs) / len(xs) if xs else 0.0
    pvf = [r["judge"]["plain_vs_frontier"]["winner"] for r in recs]
    pvs = [r["judge"]["plain_vs_skill"]["winner"] for r in recs if "plain_vs_skill" in r["judge"]]
    print("\n===== ABLATION SUMMARY =====")
    print(f"tasks={len(recs)}  skill-routed={len(pvs)}")
    print(f"avg cost: plain=${avg([r['plain']['cost_usd'] for r in recs]):.4f} "
          f"skillpath=${avg([r['skillpath']['cost_usd'] for r in recs if r['route']=='skill']):.4f} "
          f"frontier=${avg([r['frontier']['cost_usd'] for r in recs]):.4f}")
    print(f"avg tokens_in: plain={avg([r['plain']['tokens_in'] for r in recs]):.0f} "
          f"skillpath={avg([r['skillpath']['tokens_in'] for r in recs if r['route']=='skill']):.0f} "
          f"frontier={avg([r['frontier']['tokens_in'] for r in recs]):.0f}")
    for label, wins in (("plain vs frontier", pvf), ("plain vs skill", pvs)):
        tally = {w: wins.count(w) for w in set(wins)}
        print(f"{label}: {tally}")
    print("avg quality plain vs frontier:",
          round(avg([r["judge"]["plain_vs_frontier"]["quality_plain"] for r in recs]), 3), "vs",
          round(avg([r["judge"]["plain_vs_frontier"]["quality_frontier"] for r in recs]), 3))
    if pvs:
        print("avg quality plain vs skill:",
              round(avg([r["judge"]["plain_vs_skill"]["quality_plain"] for r in recs if "plain_vs_skill" in r["judge"]]), 3), "vs",
              round(avg([r["judge"]["plain_vs_skill"]["quality_skill"] for r in recs if "plain_vs_skill" in r["judge"]]), 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
