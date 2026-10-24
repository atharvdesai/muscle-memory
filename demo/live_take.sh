#!/usr/bin/env bash
# live_take.sh - the budgeted live recording script (post-quota-reset take).
#
# Free tier on this key: 20 generateContent requests/day (verified exhausted
# 2026-10-24; resets ~midnight PT). Embeddings (embedContent) are a separate
# quota and not counted here. This script spends <= 17 generateContent calls:
#
#   Phase A (0 calls, MOCK_LLM=1): simulated traffic - seed history, batch 1.
#     Narration: "simulated runs, priced at the real rate card." These traces
#     give discovery its history and the dashboard its batch-1 baseline.
#   Phase B (live): discovery proposes 2 skills from real embeddings
#     (~2 calls) -> gate validates on 1 task/candidate: 2 runners x 2 = 4 calls
#     -> judge panel scores 2 pairs: 3 members x 2 = 6 calls.
#     THE PROOF SCREEN'S NUMBERS ARE REAL: real outputs, real judge, real cost.
#   Phase C (live): batch 2, --limit 5 with skills live (<=3 calls) - real
#     skill-hit traces for the curve-bend beat.
#   Phase D (0 calls): prune + totals.
#
# If any live call 429s (quota not reset), STOP, set MUSCLE_MEMORY_MOCK=1
# MOCK_LLM=1 and re-run the whole take in mock mode (RUNBOOK backup plan).
set -euo pipefail
cd "$(dirname "$0")/.."

: "${GEMINI_API_KEY:?set GEMINI_API_KEY first}"
export GEMINI_MODEL_CHEAP="${GEMINI_MODEL_CHEAP:-gemini-3-flash-preview}"
export GEMINI_MODEL_FRONTIER="${GEMINI_MODEL_FRONTIER:-gemini-3.1-pro-preview}"
export EMBED_BACKEND=gemini

echo "=== Phase A: simulated traffic (0 API calls) ==="
MOCK_LLM=1 MUSCLE_MEMORY_MOCK=1 python3 scripts/seed_history.py --epochs 2 --reset
MOCK_LLM=1 MUSCLE_MEMORY_MOCK=1 python3 scripts/run_batch.py --limit 25 --quiet

echo "=== Phase B: live discovery + gate (~12 calls) ==="
unset MOCK_LLM || true
python3 scripts/run_discovery --min-cluster-size 2 --max-candidates 2 --validation-tasks 1

echo "=== Phase C: live batch 2 (<=3 calls) ==="
python3 scripts/run_batch.py --limit 3 --quiet

echo "=== Phase D: prune + totals (0 calls) ==="
MOCK_LLM=1 python3 -m src.prune.run
python3 demo/demo.py --totals-only
echo "TAKE COMPLETE - every Proof-screen number above came from live calls."
