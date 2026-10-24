"""Trace log: one JSON object per line in src/traces/runs.jsonl (frozen schema)."""
from __future__ import annotations

import json
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

TRACES_FILE = pathlib.Path(__file__).resolve().parent / "runs.jsonl"
DEMO_TZ = ZoneInfo("America/Los_Angeles")


def now_iso() -> str:
    return datetime.now(DEMO_TZ).isoformat(timespec="seconds")


def make_trace(task_id: str, route: str, decision_path: list[str],
               tokens_in: int, tokens_out: int, latency_ms: int,
               cost_usd: float, skill_id: str | None = None,
               skill_parts_loaded: str | None = None,
               quality_score: float | None = None) -> dict:
    """Build a trace dict matching the frozen INTERFACE.md schema exactly."""
    return {
        "task_id": task_id,
        "ts": now_iso(),
        "route": route,                      # "frontier" | "skill"
        "skill_id": skill_id,
        "skill_parts_loaded": skill_parts_loaded,  # e.g. "2 of 4"
        "decision_path": decision_path,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "quality_score": quality_score,      # filled by judge when scored
    }


def append_trace(trace: dict, traces_file: pathlib.Path | None = None) -> pathlib.Path:
    f = traces_file or TRACES_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a") as fh:
        fh.write(json.dumps(trace) + "\n")
    return f


def read_traces(traces_file: pathlib.Path | None = None) -> list[dict]:
    f = traces_file or TRACES_FILE
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


def reset_traces(traces_file: pathlib.Path | None = None) -> None:
    f = traces_file or TRACES_FILE
    if f.exists():
        f.unlink()
