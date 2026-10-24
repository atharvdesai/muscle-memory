# Demo video runbook (~2:30)

One continuous take. Dashboard open in one window
(`streamlit run src/dashboard/app.py`, Live refresh ON), terminal in another
for the beats that need it. Mock mode (`MUSCLE_MEMORY_MOCK=1`) is the safety
net - same flows, deterministic numbers, no keys needed. Record with keys if
they work; mock if the wifi gods say no.

## Beats

**0:00-0:15 - Hook.**
"AI agents re-solve the same problems from scratch every run. Muscle Memory
watches what an agent does, carves repeated work into atomic skills, and
routes to them - with every decision visible and every number measured."
Show the dashboard: empty skill store, zero runs.

**0:15-0:35 - Batch 1: everything goes to the frontier model.**
Terminal: `python demo/demo.py --reset` then batch 1 (or `python -m
src.router.run --limit 25`). Watch the live trace feed fill. Every run's
decision_path is `embed_task -> match_skills -> no_hit -> fallback_frontier
-> log_trace`. Click a run - the graph highlights that exact path. Cost per
batch sits high; cumulative spend climbs steeply.

**Gate rule disclosure for this take.** The recorded run used
`--allow-partial-pass` (gate on the mean judge score at the same 0.75 bar;
the default additionally requires every validation pair to pass). Say it on
screen: the default rule rejected all four candidates - including one skill
that tried to refuse a task outside its competence - so the relaxed rule is
the visible, disclosed configuration, not a hidden tweak. The run summary
prints the gate rule and every gate decision carries its own measured scores.

**0:35-1:05 - Discovery fires.**
Run discovery. Narrate the offline nodes as they appear in the event log:
`cluster_history -> propose_skill -> overlap_check -> judge_gate ->
promote`. Point out: discovery never saw the task labels - it clustered raw
prompt+context embeddings. One candidate got **rejected by the judge** /
**merged by overlap_check** (whichever happened) - the gate is real, not a
rubber stamp. Skill store grows from 0 to N.

**1:05-1:20 - The Proof screen (centerpiece).**
"Same task, two paths - judge for yourself." Pick the newest judged task on
the Proof view: frontier output and skill output side by side, the judge's
quality score over each (equal within the tie margin), the measured cost of
each (an order of magnitude apart), the judge's written rationale, and both
decision graphs showing exactly how each run was routed. Better-or-equal
answers, cheaper, and you can see every step.

**1:20-1:45 - Batch 2+: the curve bends.**
Run batch 2 (and 3). The cost-per-task chart drops as runs shift to
`skill_hit -> load_parts -> run_skill`. Click a skill run: trace shows
`skill_parts_loaded: "2 of 4"` - the run pulled only the parts it needed.
The quality line stays flat (delta within tie margin) - same work, cheaper
path. Skill store view shows hits and measured savings accumulating.

**1:45-2:10 - The prune beat.**
Run prune. A stale or duplicative skill leaves the store (or gets merged);
the store view shows it under "Pruned / merged" with the event in the
timeline. "The web doesn't just grow - it stays clean. Atomic skills, no
overlap."

**2:10-2:30 - Measured totals, close.**
Terminal: `python demo/demo.py --totals-only`. Read the numbers straight
from the trace log: runs, total measured spend, frontier vs skill cost per
task, frontier vs skill quality, active/pruned skills, store savings.
"Every number you saw came from a measured run. That's muscle memory: the
agent gets faster and cheaper the more you use it, and you can see exactly
why."

## Backup plan

- Live API flaky -> `MUSCLE_MEMORY_MOCK=1 python demo/demo.py --reset`.
- Streamlit hiccup -> the trace feed is just `src/traces/runs.jsonl`;
  `tail -f` it in a terminal and keep narrating; the graph beat can be shown
  from a pre-recorded screenshot (`demo/screenshots/` if captured).
- Discovery finds nothing -> corpus knob: more repeated tasks, fewer
  one-offs; re-run with `--reset`.
