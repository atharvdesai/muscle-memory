# src/judge - LLM-as-judge panel (integration contract)

The judge is the quality half of the promotion gate. Discovery's `judge_gate`
decision-graph node should call `judge_gate()` from this package; the router
fills `Trace.quality_score` with `Judge.score()`. Everything runs without API
keys via a deterministic heuristic mock (auto-selected when neither
`ANTHROPIC_API_KEY` nor `OPENAI_API_KEY` is set, or forced with
`MUSCLE_MEMORY_MOCK=1`).

## Interface

```python
from src.judge import Judge, judge_gate, judge_pair, JudgePanel

judge = Judge()                       # mode: "llm-panel" or "heuristic-mock"

# 1) Single-output scoring -> fills Trace.quality_score
q = judge.score(task, output)         # float in [0, 1]

# 2) Pairwise comparison (same task, both paths, same panel)
pair = judge.compare(task, skill_output, frontier_output)
pair.skill.quality_score              # float in [0, 1]
pair.frontier.quality_score
pair.winner                           # "skill" | "frontier" | "tie"
pair.skill.dim_scores                 # per-dimension means (1-5)

# 3) The promotion gate - the ONLY judge-side entry point for discovery
verdict = judge_gate(
    judge,
    tasks,                            # held-out cluster tasks, `type` stripped
    run_skill,                        # callable(task) -> output str, or
    run_frontier,                     #   {"output": str, "tokens_in": int,
                                      #    "tokens_out": int, "cost_usd": float}
    measured_savings_usd=...,         # discovery's trace-measured savings, or None
    min_tasks=3,
    quality_tolerance=0.05,
    min_savings_usd=0.0,
)
verdict.passed                        # bool
verdict.reason                        # human-readable, for the demo narration
verdict.measured                      # every number measured by this call
verdict.pairs                         # per-task PairResult dicts
```

## Discovery-gate adapter (src/discovery/gate.py imports this)

```python
from src.judge import judge_pair          # or JudgePanel (alias of Judge)

verdict = judge_pair(task, frontier_output, skill_output)
# -> {"score": float 0..1,   # the SKILL path's quality score
#     "passed": bool,        # skill did not regress beyond the tie margin
#     "rationale": str}
```

Every pairwise scoring - `judge_pair` or `judge_gate` - appends both outputs
and both quality scores to the evidence log `src/traces/comparisons.jsonl`
(override with `MUSCLE_MEMORY_COMPARISONS_LOG`, empty string disables). The
dashboard's Proof view renders from this file. Logging failures never break
the gate.

## Guarantees (these are rules, not conventions)

- **Never bypassed or faked.** There is no pass-override flag. Below
  `min_tasks` measured pairs the gate fails closed
  (`reason="insufficient_evidence: ..."`). If the LLM panel returns
  unparseable output for every member, the run falls back to the heuristic
  judge and `judge_mode` says so - nothing is silently invented.
- **Blind to labels.** The judge never reads `Task.type`; `judge_gate`
  strips the key defensively even if a caller forgets.
- **Measured numbers only.** Quality scores come from scoring runs the gate
  executed itself. Cost totals appear in `verdict.measured` only when the
  runners returned measured usage; otherwise they are `None`, not zero.
- **Position debiased.** The LLM panel runs 3 members (strict / balanced /
  generous stances) and alternates which output is shown as "A" so position
  bias cancels in the aggregate.
- **Deterministic mock.** `HeuristicJudge` scores from measurable output
  features (reference similarity, length fit, format cues) with no
  randomness, so mock demo runs are reproducible and the flat-quality beat
  is earned, not hard-coded.

## Rubric

`correctness` (0.40), `completeness` (0.25), `instruction_following` (0.20),
`concision` (0.15); each 1-5, weighted mean normalized to [0, 1].
Pairwise deltas under 0.05 are a tie, and a tie passes the quality bar -
that is the "flat quality line" property the dashboard shows.

## Provider config

| Env var | Effect |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic Messages API (default model `claude-3-5-haiku-20241022`) |
| `OPENAI_API_KEY` | OpenAI Chat Completions (default `gpt-4o-mini`) |
| `GEMINI_API_KEY` | Gemini generateContent (default `gemini-3.6-flash`; override with `GEMINI_MODEL_CHEAP`) |
| `LLM_MODEL` / `JUDGE_MODEL` | Model override |
| `MUSCLE_MEMORY_MOCK=1` | Force heuristic mock even with keys set |

No SDK dependencies - both providers are called over stdlib HTTP, so
requirements.txt does not change for this package.
