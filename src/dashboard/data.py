"""Data loading for the dashboard. Pure pandas - no streamlit import here so
the loaders are unit-testable and reusable by the demo script.

Sources (all paths relative to the repo root):
- src/traces/runs.jsonl          Trace rows, one JSON object per line.
- src/skills/*.json              Active skills (Skill file contract).
- src/skills/_pruned/*.json      Pruned/merged skills (lifecycle slice
                                 convention; legacy "pruned/" also scanned).
- src/skills/_candidates/*.json  Pending candidates awaiting the gate.
- src/traces/lifecycle.jsonl     Prune/merge events from the lifecycle slice.
- src/traces/discovery.jsonl     Gate decisions from the discovery slice.
- src/traces/skill_events.jsonl  Optional generic event log. All three are
                                 merged into the store event timeline; absence
                                 of any is fine.

Every number surfaced here is parsed from those measured records. Nothing is
projected or extrapolated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

TRACE_COLUMNS = [
    "task_id", "ts", "route", "skill_id", "skill_parts_loaded",
    "decision_path", "tokens_in", "tokens_out", "latency_ms", "cost_usd",
    "quality_score",
]


def load_traces(path: str | Path = "src/traces/runs.jsonl") -> pd.DataFrame:
    """Parse the trace log, tolerating blank/corrupt lines. Empty -> empty df."""
    rows = []
    p = Path(path)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    df = pd.DataFrame(rows, columns=TRACE_COLUMNS if not rows else None)
    if df.empty:
        return pd.DataFrame(columns=TRACE_COLUMNS)
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
    for col in ("tokens_in", "tokens_out", "latency_ms"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["cost_usd"] = pd.to_numeric(df["cost_usd"], errors="coerce").fillna(0.0)
    df["quality_score"] = pd.to_numeric(df["quality_score"], errors="coerce")
    return df.reset_index(drop=True)


def batch_frame(traces: pd.DataFrame, batch_size: int = 20) -> pd.DataFrame:
    """Measured per-batch aggregates, in trace order (file order = run order).

    Batches are consecutive chunks of `batch_size` traces. All values are
    measured means/sums over the traces in that batch - no baselines, no
    projections.
    """
    if traces.empty or batch_size < 1:
        return pd.DataFrame(columns=[
            "batch", "runs", "avg_cost_usd", "total_cost_usd",
            "cumulative_cost_usd", "avg_quality", "skill_share",
            "avg_tokens_in", "avg_latency_ms",
        ])
    df = traces.reset_index(drop=True).copy()
    df["batch"] = df.index // batch_size
    agg = df.groupby("batch").agg(
        runs=("task_id", "count"),
        avg_cost_usd=("cost_usd", "mean"),
        total_cost_usd=("cost_usd", "sum"),
        avg_quality=("quality_score", "mean"),
        skill_share=("route", lambda s: float((s == "skill").mean())),
        avg_tokens_in=("tokens_in", "mean"),
        avg_latency_ms=("latency_ms", "mean"),
    ).reset_index()
    agg["cumulative_cost_usd"] = agg["total_cost_usd"].cumsum()
    return agg


def quality_by_route(traces: pd.DataFrame) -> pd.DataFrame:
    """Measured mean quality per route (frontier vs skill) per batch-order."""
    scored = traces.dropna(subset=["quality_score"])
    if scored.empty:
        return pd.DataFrame(columns=["route", "avg_quality", "n"])
    return (scored.groupby("route")["quality_score"]
            .agg(avg_quality="mean", n="count").reset_index())


def _load_skill_dir(d: Path) -> list[dict]:
    skills = []
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                skills.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
    return skills


def load_skill_store(skills_dir: str | Path = "src/skills") -> tuple[pd.DataFrame, pd.DataFrame]:
    """(active, pruned) skill tables from the store directory."""
    base = Path(skills_dir)

    def frame(skills: list[dict], status: str) -> pd.DataFrame:
        rows = []
        for s in skills:
            stats = s.get("stats", {}) or {}
            rows.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "responsibility": s.get("responsibility"),
                "parts": len(s.get("parts", []) or []),
                "hits": stats.get("hits", 0),
                "avg_quality": stats.get("avg_quality"),
                "savings_usd": stats.get("savings_usd", 0.0),
                "created_at": s.get("created_at"),
                "status": status,
            })
        cols = ["id", "name", "responsibility", "parts", "hits",
                "avg_quality", "savings_usd", "created_at", "status"]
        return pd.DataFrame(rows, columns=cols)

    active = frame(_load_skill_dir(base), "active")
    # Pruned/merged: lifecycle slice writes _pruned/; scan legacy pruned/ too.
    pruned = frame(_load_skill_dir(base / "_pruned")
                   + _load_skill_dir(base / "pruned"), "pruned")
    # Pending candidates are part of the growth story - report them as a
    # third table via the pruned slot? No: keep the two-slot contract and
    # expose candidates through the events view instead.
    return active, pruned


EVENT_LOG_CANDIDATES = (
    "src/traces/lifecycle.jsonl",      # lifecycle slice: prune/merge events
    "src/traces/discovery.jsonl",      # discovery slice: gate decisions
    "src/traces/skill_events.jsonl",   # generic optional log
)


def load_skill_events(paths=None) -> pd.DataFrame:
    """Merged promotion/merge/prune event timeline from every event log that
    exists. Rows keep whatever fields the writer used; `event`/`skill_id`/
    `reason` are normalized when recognizable."""
    rows = []
    for path in (paths or EVENT_LOG_CANDIDATES):
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec = dict(rec)
            rec.setdefault("event", rec.get("decision", rec.get("action")))
            rec.setdefault("skill_id", rec.get("id", rec.get("candidate_id")))
            rec.setdefault("reason", rec.get("rationale", rec.get("verdict")))
            rec["_source"] = p.name
            rows.append(rec)
    if not rows:
        return pd.DataFrame(columns=["ts", "event", "skill_id", "reason", "_source"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df.get("ts"), errors="coerce", utc=True)
    keep = [c for c in ("ts", "event", "skill_id", "reason", "_source") if c in df.columns]
    return df[keep]


def summary(traces: pd.DataFrame) -> dict:
    """Headline KPIs, all measured."""
    if traces.empty:
        return {"total_runs": 0, "total_cost_usd": 0.0, "skill_share": 0.0,
                "avg_quality_skill": None, "avg_quality_frontier": None,
                "total_tokens": 0}
    scored = traces.dropna(subset=["quality_score"])
    q = scored.groupby("route")["quality_score"].mean().to_dict()
    return {
        "total_runs": int(len(traces)),
        "total_cost_usd": float(traces["cost_usd"].sum()),
        "skill_share": float((traces["route"] == "skill").mean()),
        "avg_quality_skill": (None if "skill" not in q else float(q["skill"])),
        "avg_quality_frontier": (None if "frontier" not in q else float(q["frontier"])),
        "total_tokens": int(traces[["tokens_in", "tokens_out"]].sum().sum()),
    }

COMPARISON_COLUMNS = [
    "task_id", "ts", "prompt", "context", "frontier_output", "skill_output",
    "quality_frontier", "quality_skill", "cost_frontier_usd", "cost_skill_usd",
    "winner", "passed", "judge_mode", "rationale",
]


def load_comparisons(path: str | Path = "src/traces/comparisons.jsonl") -> pd.DataFrame:
    """Side-by-side judge evidence written by src.judge (judge_pair/judge_gate).

    One JSON object per line: both outputs for the same task, both quality
    scores, optional measured costs, and the verdict. Powers the Proof view.
    """
    rows = []
    p = Path(path)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        return pd.DataFrame(columns=COMPARISON_COLUMNS)
    df = pd.DataFrame(rows)
    for col in COMPARISON_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
    for col in ("quality_frontier", "quality_skill",
                "cost_frontier_usd", "cost_skill_usd"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("ts").reset_index(drop=True)


def costs_from_traces(traces: pd.DataFrame, task_id: str) -> dict:
    """Measured per-route cost + decision_path for a task, from the trace log."""
    out: dict = {}
    if traces.empty:
        return out
    hits = traces[traces["task_id"] == task_id]
    for route in ("frontier", "skill"):
        sub = hits[hits["route"] == route]
        if not sub.empty:
            row = sub.iloc[-1]
            out[f"cost_{route}_usd"] = float(row["cost_usd"])
            out[f"latency_{route}_ms"] = (None if pd.isna(row["latency_ms"])
                                          else int(row["latency_ms"]))
            out[f"skill_id"] = row.get("skill_id") if route == "skill" else out.get("skill_id")
            raw = row.get("decision_path")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = None
            if isinstance(raw, (list, tuple)):
                out[f"path_{route}"] = list(raw)
    return out
