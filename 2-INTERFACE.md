# Frozen interface contracts (v2 - supersedes v1)

Everything communicates through these schemas, the trace log, and the `src/skills/`
directory. Builder agents: do not change these without integrator sign-off.

## Task

```json
{
  "id": "task-0001",
  "type": "commit_message",          // workflow label used ONLY for corpus generation and ground-truth eval; never shown to discovery
  "prompt": "Write a commit message for this diff.",
  "context": "<diff text>",
  "expected": null                    // optional reference output for spot-checking
}
```

## Trace

One JSON object per line in `src/traces/runs.jsonl`:

```json
{
  "task_id": "task-0001",
  "ts": "2026-10-24T09:00:00-07:00",
  "route": "frontier",                // "frontier" | "skill"
  "skill_id": null,                   // skill id when route == "skill"
  "skill_parts_loaded": null,         // e.g. "2 of 4" - which parts of the skill web the run pulled (see Skill parts)
  "decision_path": ["embed_task", "match_skills", "no_hit", "fallback_frontier"],
                                      // DECISION MODEL: ordered list of decision-graph nodes that fired for this run
  "tokens_in": 4200,
  "tokens_out": 180,
  "latency_ms": 3100,
  "cost_usd": 0.0172,
  "quality_score": null               // filled by judge when scored
}
```

## Decision model (first-class)

The router is a decision GRAPH, not a single if/else. Named nodes (minimum set):
`embed_task` -> `match_skills` -> (`skill_hit` -> `load_parts` -> `run_skill`)
or (`no_hit` -> `fallback_frontier`) -> `log_trace`. Discovery adds offline nodes:
`cluster_history` -> `propose_skill` -> `overlap_check` -> `judge_gate` -> `promote|reject|merge`.
Every run records the exact node sequence in `decision_path`. The dashboard renders
the graph and highlights the path taken. This is a core demo element, not decoration.

## Skill file

One JSON file per skill in `src/skills/<id>.json`:

```json
{
  "id": "skill-commit-message",
  "name": "Commit message from diff",
  "responsibility": "Summarize a git diff as a conventional commit message",
  "trigger_description": "Task asks for a git commit message summarizing a diff",
  "parts": [
    {"id": "p1", "kind": "instructions", "content": "<what the cheap path should do>"},
    {"id": "p2", "kind": "script", "content": "commit_msg.py"}
  ],
  "created_at": "2026-10-24T10:00:00-07:00",
  "stats": { "hits": 0, "avg_quality": null, "savings_usd": 0.0 }
}
```

## Skill parts and the no-overlap rule (first-class)

- ATOMIC: each skill has ONE responsibility. If a candidate skill would do two things,
  split it before promotion.
- NO OVERLAP: the promotion gate runs `overlap_check` - embedding similarity between the
  candidate and every existing skill's responsibility + parts. Above threshold:
  MERGE into the existing skill (new parts) or REJECT as duplicate. No two skills in the
  web cover the same ground.
- SELECTIVE LOADING: a run pulls only the parts it needs, never the whole web and never
  the whole skill if a part suffices. The trace records `skill_parts_loaded` ("2 of 4")
  so selective loading is visible in the demo.

## Rules

- Discovery must never see `Task.type`; it clusters on prompt+context embeddings only.
- The promotion gate (judge + savings check + overlap check) is never bypassed or faked.
- The corpus control group (~25% one-off tasks that never repeat) must stay one-off:
  the router must not force them into skills.
- All reported numbers come from measured runs.
