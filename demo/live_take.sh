#!/usr/bin/env bash
# live_take.sh - the fully-live recording take (Anthropic, $50 credits).
#
# Provider layout (everything measured, nothing simulated):
#   generation: Anthropic - frontier tier claude-sonnet-4-5, skill/judge/cheap
#               tier claude-haiku-4-5 (engine client; judge panel; discovery)
#   embeddings: Gemini gemini-embedding-001 (free tier, separate quota)
#
# The cost story rides on two measured differences: full-context frontier
# tokens vs loaded-skill-parts tokens, AND sonnet vs haiku per-token price.
# Disclosure for narration: "generation runs on Anthropic Claude, embeddings
# on Gemini" - and nothing else needs qualifying; every number is measured.
#
# Fallback (unchanged): if live calls fail, MOCK_LLM=1 MUSCLE_MEMORY_MOCK=1
# with EMBED_BACKEND=gemini re-runs the whole take offline (RUNBOOK backup).
set -euo pipefail
cd "$(dirname "$0")/.."

: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY first}"
: "${GEMINI_API_KEY:?set GEMINI_API_KEY first (embeddings)}"
export LLM_PROVIDER=anthropic
export EMBED_BACKEND=gemini
unset MOCK_LLM MUSCLE_MEMORY_MOCK || true

echo "=== Phase A: live history (70 frontier runs) ==="
python3 scripts/seed_history.py --epochs 2 --reset
python3 scripts/run_batch.py --limit 25 --quiet

echo "=== Phase B: live discovery + gate ==="
python3 scripts/run_discovery --min-cluster-size 2

echo "=== Phase C: live batch 2 ==="
python3 scripts/run_batch.py --limit 25 --quiet

echo "=== Phase D: prune + totals ==="
python3 -m src.prune.run
python3 demo/demo.py --totals-only
echo "TAKE COMPLETE - every number above is from a measured live run."
