#!/usr/bin/env python3
"""Demo orchestrator for Muscle Memory.

Drives the beats of the ~2.5 minute demo (see demo/RUNBOOK.md):

    reset -> batch 1 (empty store, all frontier) -> discovery (skills appear)
          -> batch 2 (skills take over, cost bends) -> prune -> measured totals

Each stage shells out to the owning slice's entry point. The commands below
are the conventional defaults; adjust STAGES here (or via env vars) to match
the final module names - each stage checks that its module exists and skips
with a loud warning instead of crashing the demo.

Usage (from the repo root):

    python demo/demo.py --reset          # full run, empty store first
    python demo/demo.py --from batch2    # re-run a later beat
    python demo/demo.py --totals-only    # just recompute measured totals

Set MUSCLE_MEMORY_MOCK=1 to run the whole thing without API keys.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "src" / "traces" / "runs.jsonl"
SKILLS = ROOT / "src" / "skills"
EVENTS = ROOT / "src" / "traces" / "skill_events.jsonl"
COMPARISONS = ROOT / "src" / "traces" / "comparisons.jsonl"

# --- Stage commands ---------------------------------------------------------
# (label, [command], required module for the existence check or None,
#  env var to override the command as a single shell-free string)
STAGES = [
    ("batch1",    [sys.executable, "-m", "src.router.run", "--limit", "25"],
     "src.router.run", "DEMO_CMD_BATCH1"),
    ("discovery", [sys.executable, "-m", "src.discovery.run"],
     "src.discovery.run", "DEMO_CMD_DISCOVERY"),
    ("batch2",    [sys.executable, "-m", "src.router.run", "--limit", "25"],
     "src.router.run", "DEMO_CMD_BATCH2"),
    ("batch3",    [sys.executable, "-m", "src.router.run", "--limit", "25"],
     "src.router.run", "DEMO_CMD_BATCH3"),
    ("prune",     [sys.executable, "-m", "src.prune.run"],
     "src.prune.run", "DEMO_CMD_PRUNE"),
]


def _module_exists(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except ModuleNotFoundError:
        return False


def reset() -> None:
    """Empty the skill store and trace log so the demo starts from zero."""
    TRACES.parent.mkdir(parents=True, exist_ok=True)
    TRACES.write_text("")
    if EVENTS.exists():
        EVENTS.write_text("")
    if COMPARISONS.exists():
        COMPARISONS.write_text("")
    if SKILLS.exists():
        for f in SKILLS.glob("*.json"):
            f.unlink()
    (SKILLS / "pruned").mkdir(parents=True, exist_ok=True)
    for f in (SKILLS / "pruned").glob("*.json"):
        f.unlink()
    print("[demo] reset: empty skill store, empty trace log")


def run_stage(label: str, cmd: list[str], module: str | None) -> bool:
    if module and not _module_exists(module):
        print(f"[demo] SKIP {label}: module {module!r} not found "
              f"(wire it up or override with the DEMO_CMD_* env vars)")
        return False
    print(f"[demo] {label}: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        print(f"[demo] WARN {label} exited {res.returncode}")
        return False
    return True


def measured_totals() -> dict:
    """Totals computed from the trace log alone. Measured, never projected."""
    rows = []
    if TRACES.exists():
        for line in TRACES.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    total_cost = sum(float(r.get("cost_usd", 0.0)) for r in rows)
    by_route: dict[str, list] = {}
    for r in rows:
        by_route.setdefault(r.get("route", "?"), []).append(r)

    def avg(xs, key):
        vals = [float(x[key]) for x in xs if x.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    skills = list(SKILLS.glob("*.json")) if SKILLS.exists() else []
    pruned = list((SKILLS / "pruned").glob("*.json")) if (SKILLS / "pruned").exists() else []
    store_savings = 0.0
    for f in skills:
        try:
            store_savings += float(json.loads(f.read_text()).get("stats", {}).get("savings_usd", 0.0))
        except (json.JSONDecodeError, ValueError):
            pass

    return {
        "runs": len(rows),
        "total_cost_usd": round(total_cost, 6),
        "avg_cost_frontier": (None if "frontier" not in by_route
                              else round(avg(by_route["frontier"], "cost_usd") or 0.0, 6)),
        "avg_cost_skill": (None if "skill" not in by_route
                           else round(avg(by_route["skill"], "cost_usd") or 0.0, 6)),
        "avg_quality_frontier": (None if "frontier" not in by_route
                                 else avg(by_route["frontier"], "quality_score")),
        "avg_quality_skill": (None if "skill" not in by_route
                              else avg(by_route["skill"], "quality_score")),
        "skills_active": len(skills),
        "skills_pruned": len(pruned),
        "skill_store_savings_usd": round(store_savings, 6),
        "judged_comparisons": (sum(1 for ln in COMPARISONS.read_text().splitlines() if ln.strip())
                               if COMPARISONS.exists() else 0),
    }


def print_totals() -> None:
    t = measured_totals()
    print("\n===== MEASURED TOTALS (from src/traces/runs.jsonl + skill store) =====")
    for k, v in t.items():
        print(f"  {k:26} {v}")
    print("======================================================================\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="empty store + traces first")
    ap.add_argument("--from", dest="from_stage", default=None,
                    choices=[s[0] for s in STAGES], help="start at a later beat")
    ap.add_argument("--totals-only", action="store_true")
    args = ap.parse_args()

    if args.totals_only:
        print_totals()
        return
    if args.reset:
        reset()

    started = args.from_stage is None
    for label, cmd, module, env in STAGES:
        if not started:
            started = label == args.from_stage
            continue
        override = os.getenv(env)
        stage_cmd = override.split() if override else cmd
        run_stage(label, stage_cmd, None if override else module)
    print_totals()
    print("[demo] open the dashboard: streamlit run src/dashboard/app.py")


if __name__ == "__main__":
    main()
