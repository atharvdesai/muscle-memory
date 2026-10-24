# Muscle Memory for AI Agents

Your coding agent repeats itself. It writes the same kinds of commit messages, explains the same
kinds of stack traces, triages the same kinds of issues - and pays frontier-model prices for
every single one.

**Muscle Memory** watches what your agent actually does, discovers the workflows it repeats
(without being told), compiles them into cheap reusable skills, and prunes the ones that stop
paying off. The longer it runs, the cheaper it gets - and a judge panel proves it doesn't get dumber.

Built at CalHacks 13 (Oct 24-25, 2026).

## The loop

1. **Runner** executes each task two possible ways: the frontier-model path (expensive, general)
   or the skill path (cheap: instructions + optional script, run on a small model or deterministically).
2. **Router** embeds every incoming task and matches it against active skills.
   Hit = cheap path. Miss = frontier path, logged for later.
3. **Discovery** (batch, autonomous): clusters the trace history, finds repeated workflows nobody
   labeled, generates a candidate skill, and promotes it only if it passes the judge AND saves money.
4. **Judge panel**: LLM-as-judge scores skill-path output against frontier-path output on the same
   tasks. Gates promotion and powers the "quality held flat" proof.
5. **Lifecycle**: skills with low hit rate, quality decay, or negative savings get pruned.
   The skill store grows - and shrinks - on its own.

## Layout

    src/corpus/      task generator + fixtures (the demo workload)
    src/runner/      executor: frontier + skill paths
    src/router/      embeddings + skill matching
    src/discovery/   clustering, skill proposal, promotion gate
    src/judge/       rubric + scoring
    src/lifecycle/   prune logic
    src/traces/      logged runs (jsonl)
    src/skills/      compiled skills (starts empty - the growth is the demo)
    src/dashboard/   one-screen Streamlit app
    scripts/         run_batch, run_discovery, seed_history
    demo/            demo script + video assets

Interface contracts are frozen in INTERFACE.md. All savings/quality claims in the README,
pitch, and Devpost must come from measured runs - no projections.
