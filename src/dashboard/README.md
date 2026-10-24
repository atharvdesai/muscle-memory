# src/dashboard - one-screen Streamlit dashboard

    streamlit run src/dashboard/app.py        # from the repo root

Sections (top to bottom, one screen):

1. **KPIs** - measured spend, runs, skill-routed %, quality per route.
1.5. **Proof view (centerpiece)** - pick any judged task: frontier output and
   skill output side by side, the judge's quality score and measured cost for
   each path, the judge's verdict and rationale, the task prompt/context, and
   both decision graphs with each path's exact `decision_path` highlighted.
   Evidence comes from `src/traces/comparisons.jsonl` (written by the judge)
   joined with `runs.jsonl` for measured costs and paths.
2. **Charts** - avg cost per task per batch (the curve that bends),
   cumulative measured spend, avg quality per batch (the flat line),
   skill mix per batch. Batches are consecutive chunks of traces in file
   order; size adjustable in the sidebar.
3. **Decision graph** - SVG render of the router's decision graph plus the
   offline discovery nodes. Pick any recent run and its exact
   `decision_path` is highlighted, including `skill_parts_loaded` for skill
   runs.
4. **Skill store** - active skills from `src/skills/*.json`, pruned/merged
   from `src/skills/_pruned/*.json` (legacy `pruned/` also scanned), plus the
   merged event timeline from `src/traces/lifecycle.jsonl`,
   `src/traces/discovery.jsonl`, and `src/traces/skill_events.jsonl`
   (whichever exist - the dashboard degrades gracefully).
5. **Live trace feed** - newest traces first, auto-refreshing every 2s
   (toggle in the sidebar).

Data loaders live in `data.py` (pure pandas, no streamlit import) so the
demo script and tests can reuse them. Every number shown is parsed from the
trace log or skill files - nothing is projected.
