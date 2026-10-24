"""CLI for the lifecycle prune pass: `python -m src.prune.run`.

Wraps src.lifecycle.prune.prune_skills (the learning slice's implementation)
so the demo orchestrator can invoke it as a module.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.lifecycle.prune import prune_skills  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skills", default="src/skills")
    ap.add_argument("--traces", default="src/traces/runs.jsonl")
    ap.add_argument("--min-hit-rate", type=float, default=0.05)
    ap.add_argument("--min-eligible-runs", type=int, default=50)
    ap.add_argument("--min-quality", type=float, default=0.6)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    report = prune_skills(
        args.skills,
        args.traces,
        min_hit_rate=args.min_hit_rate,
        min_eligible_runs=args.min_eligible_runs,
        min_quality=args.min_quality,
        dry_run=args.dry_run,
    )
    print(json.dumps({
        "evaluated": getattr(report, "evaluated", None),
        "pruned": getattr(report, "pruned", None),
        "kept": getattr(report, "kept", None),
        "dry_run": args.dry_run,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
