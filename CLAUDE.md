# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Muscle Memory (CalHacks 13 project): routes agent tasks either to a frontier model or to a cheap "skill"
path, discovers repeated workflows from trace history, gates new skills with a judge panel plus measured
savings, and prunes skills that stop paying off. `INTERFACE.md` holds the **frozen** schemas (Task, Trace,
Skill file) and the rules. Read it before you change anything that reads or writes traces or skills.

## Commands

Run everything from the repo root (Python 3, `pip install -r requirements.txt`). There is no test suite,
linter, or build step.

```bash
# Fully offline: no keys, deterministic. Set both flags; different modules read different ones.
export MOCK_LLM=1 MUSCLE_MEMORY_MOCK=1

python3 scripts/seed_history.py --epochs 2 --reset   # frontier-only history; --reset wipes runs.jsonl
python3 scripts/run_batch.py --limit 25 [--type commit_message] [--quiet]   # route corpus tasks
python3 scripts/run_discovery --min-cluster-size 2 [--allow-partial-pass] [--dry-run] [--mock]
python3 -m src.prune.run [--dry-run]                 # lifecycle prune pass
python3 demo/demo.py --reset | --from batch2 | --totals-only   # demo orchestrator
bash demo/live_take.sh                               # the full live take (needs ANTHROPIC_API_KEY + GEMINI_API_KEY)
streamlit run src/dashboard/app.py                   # dashboard / Proof view
```

`python -m src.router.run` and `python -m src.discovery.run` are thin shims that load `scripts/run_batch.py`
and `scripts/run_discovery`. `scripts/run_discovery` has no `.py` extension.

## Architecture

**Online path (`src/router/graph.py` `DecisionGraph`)**: a named-node decision graph:
`embed_task -> match_skills -> (skill_hit -> load_parts -> run_skill | no_hit -> fallback_frontier) -> log_trace`.
Every run appends one trace to `src/traces/runs.jsonl` (`src/traces/logger.py` `make_trace`) and records
the exact node sequence in `decision_path`. The dashboard renders these node names, so renaming a node
breaks the dashboard. `load_parts` loads only the skill parts that match the task (`PART_MATCH_THRESHOLD`,
default 0.66) and logs `skill_parts_loaded` as `"k of n"`. Execution lives in `src/runner/executor.py`.

**Offline path (`src/discovery/discovery.py` `run_discovery`)**:
`cluster_history -> propose_skill -> overlap_check -> judge_gate -> promote|reject|merge`. Discovery
clusters prompt+context embeddings and must **never** read `Task.type`, which is a corpus-only
ground-truth label. `overlap.py` merges a candidate into an existing skill or rejects it as a duplicate
(`--merge-threshold`/`--reject-threshold`). `gate.py` promotes only on measured judge scores plus
measured savings. If the judge or the runner can't be imported, the candidate is filed as pending in
`src/skills/_candidates/`. It is never promoted on assumed numbers. `scripts/run_discovery` wires in
`src.judge.judge_pair` and `src.runner.run_frontier_path/run_skill_path`.

**Judge (`src/judge/`)**: a 3-member LLM panel with position-swap debiasing (`panel.py`), the rubric
and tie margin in `rubric.py`, and a deterministic `HeuristicJudge` for mock mode. Writes
`comparisons.jsonl`, which the dashboard's Proof view joins with `runs.jsonl`.

**Lifecycle (`src/lifecycle/prune.py`)**: prunes on low hit rate, quality decay, or negative
measured savings. It moves skill files to `src/skills/_pruned/` and logs to `lifecycle.jsonl`.

**Skill store**: one JSON file per active skill in `src/skills/<id>.json`. Skills are atomic (one
responsibility) and non-overlapping, with 2-4 parts. `src/skills/` is the live store that every stage
reads and writes.

**Traces**: `src/traces/{runs,comparisons,discovery,lifecycle,skill_events}.jsonl` are gitignored
runtime data. The dashboard loaders (`src/dashboard/data.py`, pure pandas) tolerate any of them being
missing.

**Corpus**: `src/corpus/generator.py` `build_corpus()` builds 40 tasks: 30 recurring tasks across 7
workflow types, plus 10 one-off control tasks. The control tasks must stay unskilled.

## Providers and configuration (gotchas)

There are **three independent LLM clients** (`src/runner/llm.py`, `src/discovery/llm.py`,
`src/judge/llm.py`) and **two embedding modules** (`src/router/embeddings.py`,
`src/discovery/embeddings.py`). Each resolves its provider differently:
- Mock flags: runner, router, and discovery read `MOCK_LLM=1`. The judge reads `MUSCLE_MEMORY_MOCK=1`.
  `demo/demo.py` keys off `MUSCLE_MEMORY_MOCK`.
- `LLM_PROVIDER=anthropic|openai|gemini|mock` pins the generation provider when several keys exist.
  `EMBED_BACKEND=gemini|openai|hash` pins embeddings, even in mock mode.
- Model overrides: `GEMINI_MODEL_FRONTIER` / `GEMINI_MODEL_CHEAP` (runner), `MUSCLE_MEMORY_MODEL`
  (discovery), `LLM_MODEL` / `JUDGE_MODEL` (judge).
- The router match threshold depends on the embedding backend (`DEFAULT_THRESHOLD_BY_BACKEND` in
  `graph.py`: hash 0.44, gemini 0.76, openai 0.78). `MATCH_THRESHOLD` overrides it, and the live take
  uses 0.68.
- The hash embedder can't separate workflows, so a mock run finds zero discovery clusters. Mock mode
  is for testing plumbing, not discovery results.
- The LLM clients call providers over stdlib `urllib`, with no SDK dependencies. Keep it that way.

## Project rules (from INTERFACE.md / README)

- Don't change the Task, Trace, or Skill schemas without integrator sign-off.
- Never bypass or fake the promotion gate. `--allow-partial-pass`, which gates on the mean judge
  score instead of requiring every pair to pass, must stay disclosed in run output.
- Every number in the README, the demo, or the pitch must come from a measured run. Don't add
  projected or mock numbers as evidence.
