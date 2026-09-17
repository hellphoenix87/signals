# Counterfactual: Pre-Breakeven Soft SL/Timeout Backtest

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

`pre-breakeven-exit-strategy-backtest.md` (previous plan) showed the pre-breakeven soft SL/timeout rarely fires (1.3%-1.7% of trades) but couldn't say whether cutting those specific trades early was actually the right call -- the simulation stopped the moment `soft_sl`/`timed_out` fired, with no visibility into what price did afterward. User asked to run the counterfactual: what would have happened to those same trades if only the broker-side wide SL existed?

## Phase 1: Build and run the counterfactual (EURUSD only)

- **`scripts/backtest_exit_strategy.py`**: new `--counterfactual` flag. For every trade whose real outcome is `soft_sl`/`timed_out`, continues replaying the *exact same* real tick stream (fetched once, up front, at an extended budget) from the point of the real exit onward, checking which happens first: price recovers to breakeven, or price reaches the broker-side wide SL distance (`Config.DEFAULT_SL_PIPS`). Same price path both legs see, not a separately-fetched one.
- **Result** (EURUSD, two independent 4-week windows): net dollar effect across the affected trades mildly negative for the early cuts (-$3.80, -$2.60) on a genuinely tiny combined sample (17 trades total) -- flagged explicitly as too small to trust.

## Phase 2: Scale up to 7 symbols x 3 windows, fix a real bug found along the way

- User asked how to get a larger sample; agreed on two symbols x windows combined (7 majors this account has available, plus a 3rd historical window -- candle history caps at ~12 weeks).
- Ran the full 21-combination sweep. **Found a real bug**: `compute_profit` assumed price-diff-times-contract-size is always USD-denominated -- wrong for USDJPY/USDCHF/USDCAD (indirect pairs, USD is the base currency, so the raw formula computes profit in JPY/CHF/CAD instead). Caught because USDJPY's numbers were absurd (91.2% hitting the "$5" soft SL, P&L in the tens of thousands) -- and confirmed the bug affected the *simulated behavior* itself (the soft SL compares against `position["profit"]` directly), not just reporting.
- **Fixed**: `profit_needs_conversion` checks MT5's own `symbol_info.currency_profit` against the account's actual currency; `compute_profit` divides by the live tick price when they differ, matching MT5's own continuous re-conversion for indirect pairs. Re-ran the 3 affected pairs (9 runs) after the fix; the 4 direct-quote pairs were unaffected and not re-run.
- **Corrected pooled result** (3,386 trades, 585 cut-short): would-have-recovered 68.9%, would-have-hit-broker-SL 23.8%, still-unresolved 7.4%. Real P&L -$1,532.67 vs counterfactual -$1,616.89 -- **difference +$84.23, early cuts HELPED**, reversing the original small-sample finding. 5 of 7 pairs individually lean the same direction.
- Full detail: `docs/test-results/pre-breakeven-exit-strategy-backtest.md` (updated, not a new report).

## Out of scope (explicit, not forgotten)

- Any config/behavior change based on this result -- a modest net positive on this sample isn't strong enough alone to justify retuning `EXIT_MAX_LOSS_MONEY`/`EXIT_BE_ARMING_TICKS`, just enough to say the mechanism looks directionally justified rather than harmful.
- Extending the counterfactual to the post-breakeven phase (a separate, larger simulation -- the trailing logic's own hardcoded values would need addressing first).
- Per-entry-strategy exit config (MTF vs single-timeframe) -- discussed at length in this session, agreed as a reasonable next step once a similar single-tf counterfactual exists, but not built here.
- No tests, per MVP/POC mode.
