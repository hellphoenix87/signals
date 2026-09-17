# Counterfactual: Pre-Breakeven Soft SL/Timeout Backtest

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

`pre-breakeven-exit-strategy-backtest.md` (previous plan) showed the pre-breakeven soft SL/timeout rarely fires (1.3%-1.7% of trades) but couldn't say whether cutting those specific trades early was actually the right call -- the simulation stopped the moment `soft_sl`/`timed_out` fired, with no visibility into what price did afterward. User asked to run the counterfactual: what would have happened to those same trades if only the broker-side wide SL existed?

## Phase 1: Build and run the counterfactual

- **`scripts/backtest_exit_strategy.py`**: new `--counterfactual` flag. For every trade whose real outcome is `soft_sl`/`timed_out`, continues replaying the *exact same* real tick stream (fetched once, up front, at an extended budget) from the point of the real exit onward, checking which happens first: price recovers to breakeven, or price reaches the broker-side wide SL distance (`Config.DEFAULT_SL_PIPS`). Same price path both legs see, not a separately-fetched one.
- **Result** (two independent 4-week windows): the majority of cut-short trades (67-71%) would have recovered to breakeven anyway, just slower (avg 364-1166 extra ticks); a minority (7-33%) really would have hit the wider stop. Net dollar effect across the affected trades is mildly negative for the early cuts in both windows (-$3.80, -$2.60) -- consistent in direction, but on a genuinely tiny combined sample (17 trades total), nowhere near enough to conclude the mechanism should change.
- Full detail: `docs/test-results/pre-breakeven-exit-strategy-backtest.md` (updated, not a new report -- this directly answers that report's own open caveat).

## Out of scope (explicit, not forgotten)

- Any config/behavior change based on this result -- the sample is explicitly flagged as too small to act on.
- Extending the counterfactual to the post-breakeven phase (a separate, larger simulation -- the trailing logic's own hardcoded values would need addressing first).
- No tests, per MVP/POC mode.
