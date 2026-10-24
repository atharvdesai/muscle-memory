"""Muscle Memory - one-screen demo dashboard.

Run from the repo root:

    streamlit run src/dashboard/app.py

Everything on screen is parsed from measured records: the trace log
(src/traces/runs.jsonl), the skill store (src/skills/), and the optional
skill-event log (src/traces/skill_events.jsonl). No baselines, no
projections.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

# Make `src` importable when launched as `streamlit run src/dashboard/app.py`
# from the repo root.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import html as _html_mod                             # noqa: E402

from src.dashboard import data                       # noqa: E402
from src.dashboard.graph import render_graph         # noqa: E402


def _html_escape(text) -> str:
    return _html_mod.escape(str(text or ""))

TRACES_PATH = _ROOT / "src" / "traces" / "runs.jsonl"
COMPARISONS_PATH = _ROOT / "src" / "traces" / "comparisons.jsonl"
SKILLS_DIR = _ROOT / "src" / "skills"

st.set_page_config(page_title="Muscle Memory", layout="wide", page_icon="\U0001f9e0")

with st.sidebar:
    st.header("View")
    batch_size = st.slider("Batch size (traces per batch)", 5, 50, 20)
    feed_rows = st.slider("Trace feed rows", 5, 50, 12)
    live = st.toggle("Live refresh (2s)", value=False)
    st.caption("Turn live refresh on for the demo.")
    st.caption(f"traces: `{TRACES_PATH.relative_to(_ROOT)}`")

traces = data.load_traces(TRACES_PATH)
comparisons = data.load_comparisons(COMPARISONS_PATH)
active, pruned = data.load_skill_store(SKILLS_DIR)
events = data.load_skill_events([
    _ROOT / "src" / "traces" / "lifecycle.jsonl",
    _ROOT / "src" / "traces" / "discovery.jsonl",
    _ROOT / "src" / "traces" / "skill_events.jsonl",
])
kpi = data.summary(traces)
batches = data.batch_frame(traces, batch_size)

st.title("Muscle Memory for AI Agents")
st.caption("Every number on this page is measured from the trace log. Nothing is projected.")

# --- KPI row ---------------------------------------------------------------
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Measured spend", f"${kpi['total_cost_usd']:.4f}")
c2.metric("Runs", kpi["total_runs"])
c3.metric("Skill-routed", f"{kpi['skill_share'] * 100:.0f}%")
qs = kpi["avg_quality_skill"]
qf = kpi["avg_quality_frontier"]
c4.metric("Quality - skill path", "n/a" if qs is None else f"{qs:.3f}")
c5.metric("Quality - frontier path", "n/a" if qf is None else f"{qf:.3f}",
          delta=None if (qs is None or qf is None) else f"{qs - qf:+.3f}")

# --- PROOF: same task, two paths, side by side (the centerpiece) ------------
st.subheader("Proof - same task, two paths")
if comparisons.empty:
    st.info("No side-by-side comparisons yet. They land in "
            "`src/traces/comparisons.jsonl` the first time the judge gate scores "
            "a candidate skill against the frontier path on identical tasks.")
else:
    opts = [f"{r.task_id}  ({r.ts:%H:%M:%S})" for r in comparisons.itertuples()]
    pick_i = st.selectbox("Pick a judged task", range(len(opts)),
                          index=len(opts) - 1,
                          format_func=lambda i: opts[i])
    comp = comparisons.iloc[pick_i]
    joined = data.costs_from_traces(traces, str(comp["task_id"]))

    def _num(rec_val, joined_val):
        return rec_val if pd_notna(rec_val) else joined_val

    import pandas as _pd
    pd_notna = _pd.notna
    q_s = comp["quality_skill"]
    q_f = comp["quality_frontier"]
    c_s = _num(comp["cost_skill_usd"], joined.get("cost_skill_usd"))
    c_f = _num(comp["cost_frontier_usd"], joined.get("cost_frontier_usd"))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Quality - skill path", "n/a" if not pd_notna(q_s) else f"{q_s:.3f}",
              delta=None if not (pd_notna(q_s) and pd_notna(q_f)) else f"{q_s - q_f:+.3f} vs frontier")
    m2.metric("Quality - frontier path", "n/a" if not pd_notna(q_f) else f"{q_f:.3f}")
    m3.metric("Cost - skill path", "n/a" if c_s is None else f"${c_s:.4f}",
              delta=None if not (c_s and c_f) else f"-${c_f - c_s:.4f} saved",
              delta_color="inverse")
    m4.metric("Cost - frontier path", "n/a" if c_f is None else f"${c_f:.4f}")

    verdict = comp["winner"] if pd_notna(comp["winner"]) else "tie"
    st.caption(f"Judge verdict: **{verdict}** ({comp['judge_mode'] or 'unknown mode'})"
               + (f" - {comp['rationale']}" if pd_notna(comp["rationale"]) else ""))

    out_l, out_r = st.columns(2)
    with out_l:
        st.markdown("**Frontier path output**")
        st.markdown(
            f'<div style="border:1px solid #3a3f55;border-radius:8px;padding:12px;'
            f'white-space:pre-wrap;background:#1a1d2b">{_html_escape(comp["frontier_output"])}</div>',
            unsafe_allow_html=True)
    with out_r:
        st.markdown("**Skill path output**")
        st.markdown(
            f'<div style="border:1px solid #f59e0b;border-radius:8px;padding:12px;'
            f'white-space:pre-wrap;background:#1a1d2b">{_html_escape(comp["skill_output"])}</div>',
            unsafe_allow_html=True)

    with st.expander("Task prompt + context", expanded=False):
        st.markdown(f"**Prompt:** {comp['prompt']}")
        st.markdown(f"**Context:**\n```\n{comp['context']}\n```")

    st.markdown("**How each path was decided**")
    dg_l, dg_r = st.columns(2)
    path_f = joined.get("path_frontier") or ["embed_task", "match_skills", "no_hit",
                                             "fallback_frontier", "log_trace"]
    path_s = joined.get("path_skill") or ["embed_task", "match_skills", "skill_hit",
                                          "load_parts", "run_skill", "log_trace"]
    with dg_l:
        st.html(render_graph(path_f, "frontier path"))
    with dg_r:
        st.html(render_graph(path_s, "skill path"))

# --- Charts: cost curve bends, quality line stays flat ----------------------
left, right = st.columns(2)
with left:
    st.subheader("Cost per task across batches")
    if batches.empty:
        st.info("No traces yet - run the first batch.")
    else:
        st.line_chart(batches.set_index("batch")[["avg_cost_usd"]],
                      y_label="avg cost per task (USD)", x_label="batch")
        st.line_chart(batches.set_index("batch")[["cumulative_cost_usd"]],
                      y_label="cumulative measured spend (USD)", x_label="batch")
with right:
    st.subheader("Quality across batches")
    q = batches.dropna(subset=["avg_quality"])
    if q.empty:
        st.info("No judged runs yet.")
    else:
        st.line_chart(q.set_index("batch")[["avg_quality"]],
                      y_label="avg quality score (0-1)", x_label="batch")
    st.subheader("Skill mix across batches")
    if not batches.empty:
        st.line_chart(batches.set_index("batch")[["skill_share"]],
                      y_label="share of runs routed to skills", x_label="batch")

# --- Decision graph + run inspector ----------------------------------------
st.subheader("Decision graph - how each run was decided")
gcol, icol = st.columns([3, 2])
recent = traces.tail(50)
options = ["(none)"] + [
    f"#{i} {r.task_id} via {r.route}" for i, r in recent.iterrows()
]
with icol:
    pick = st.selectbox("Highlight a run's decision_path", options,
                        index=len(options) - 1 if len(options) > 1 else 0)
path, title = [], ""
if pick != "(none)":
    row = recent.iloc[options.index(pick) - 1]
    raw = row.get("decision_path")
    if isinstance(raw, str):
        import json as _json
        try:
            raw = _json.loads(raw)
        except ValueError:
            raw = []
    path = list(raw) if isinstance(raw, (list, tuple)) else []
    title = (f"{row['task_id']} -> {row['route']}"
             + (f" ({row['skill_id']}, parts {row['skill_parts_loaded']})"
                if row.get("route") == "skill" else ""))
with gcol:
    st.html(render_graph(path, title))

# --- Skill store: growth AND pruning ---------------------------------------
st.subheader("Skill store")
scol, ecol = st.columns([3, 2])
with scol:
    if active.empty and pruned.empty:
        st.info("Skill store is empty - this is where the demo starts.")
    else:
        if not active.empty:
            st.markdown(f"**Active skills ({len(active)})**")
            st.dataframe(active, width="stretch", hide_index=True)
        if not pruned.empty:
            st.markdown(f"**Pruned / merged ({len(pruned)})**")
            st.dataframe(pruned, width="stretch", hide_index=True)
with ecol:
    st.markdown("**Store events**")
    if events.empty:
        st.caption("No lifecycle/discovery event logs yet (promotions, merges, prunes land here).")
    else:
        st.dataframe(events.sort_values("ts", ascending=False),
                     width="stretch", hide_index=True)

# --- Live trace feed --------------------------------------------------------
st.subheader("Live trace feed")
if traces.empty:
    st.info("No traces yet.")
else:
    feed = traces.tail(feed_rows).iloc[::-1].copy()
    feed["decision_path"] = feed["decision_path"].astype(str)
    st.dataframe(feed, width="stretch", hide_index=True)

if live:
    time.sleep(2)
    st.rerun()
