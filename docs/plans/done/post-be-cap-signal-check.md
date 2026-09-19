# Post-BE Cap: Does a Recovery-Predicting Signal Exist?

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Follow-up to the full-lifecycle cap retune (`-$1`, docs/plans/done/full-lifecycle-backtest.md): a proposal came up to couple live ticks/position status with indicators to distinguish "this post-BE reversal will recover" from "this one won't," instead of a flat cap. Before building anything, cheaply check whether any such signal actually exists -- reusing data already on disk, then (if warranted) a small follow-up sweep, rather than committing to a dynamic-rule build on a hunch.

## What was done

1. **Cheap check on existing data (zero new MT5 calls)**: cross-referenced the `-$1`-cut trades against the looser `-$3` candidate's real outcome (same tick stream, both already captured in the prior sweep) to label "would have recovered" vs. "genuinely bad." Correlated entry-time indicators (confidence, ADX, m15_bias, m5_confirm, m1_entry) against that label. **Result: no usable signal at `-$1`** -- none of the indicators showed a strong or consistent effect, and even the previously-known ticks-to-cap signal (strong at the old `-$5`) had washed out to near-nothing (203 vs 210 ticks). Diagnosis: `-$1` is so tight that most trades resolve in ~200 ticks regardless of fate -- too short a window for anything to differentiate on.
2. **Added `$7.0`/`$15.0` to `FULL_LIFECYCLE_CAP_CANDIDATES`** (`scripts/backtest_exit_strategy.py`) to retest with real decision room -- `$7` as the value under test, `$15` as a looser reference to label real recovery, mirroring the `-$1`-vs-`-$3` technique. Ran the full 7-pair x 3-window sweep.
3. **Result: the signal reappears, clearly and consistently.** Ticks-to-cap: real wins avg 822 ticks vs. genuinely-bad avg 1,154 ticks, and the direction holds in **7 of 7 pairs** individually (not just pooled). Entry-time confidence and m1_entry also show a real gap in the same direction as this project's recurring "less textbook-clean entries recover better" theme.

## Decision: confirmed real, not yet actionable

- `$7` flat is a worse production cap than `$1` (-$2,737 vs -$1,439.81 pooled) -- the opportunity is a *dynamic* rule (tight baseline, extend the leash on a fast-vs-slow read), not a looser flat number. Not built here.
- **EURUSD itself only had 2 "real recovery" trades** in the `$7`/`$15` sample -- far too few to confirm the signal for the one pair actually live in `Config.SYMBOLS`. Pooling in the other 6 pairs to compensate isn't justified without knowing whether their session-driven volatility is comparable to EURUSD's -- which is exactly Thread 1 (session filtering per pair, docs/exit-strategy-open-threads.md), still open.
- **Explicit decision, per user instruction**: do not build the dynamic cap now. Do Thread 1 first (next session), then revisit this with EURUSD-specific confidence.

Full detail folded into `docs/exit-strategy-open-threads.md` (Thread 1's priority raised, Thread 4's section updated with this finding).

## Out of scope (explicit, not forgotten)

- Building any dynamic/time-aware post-BE cap rule -- blocked on Thread 1, next session's starting point.
- Thread 1 itself (session filtering per pair) -- explicitly deferred to a new session by the user.
- Thread 2 (dynamic pre-BE via signal confidence) -- unrelated, still open, not touched here.
- No tests, per MVP/POC mode.
