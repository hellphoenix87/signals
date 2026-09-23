# Volatility-normalized exit thresholds

Status: done
Mode: MVP/POC (main session implements directly, no tests, single branch)

## Problem

Every exit threshold is a fixed dollar amount at a fixed 0.2 lots:

| Setting | Value |
|---|---|
| `EXIT_MAX_LOSS_MONEY` (pre-BE soft SL) | `$5.00` |
| `EXIT_POST_BE_LOSS_CAP_MONEY` | `$1.00` |
| `EXIT_TRAIL_GAP_FLOOR_MONEY` | `$2.00` |

Measured across 12 four-week EURUSD windows (Oct 2025 - Sep 2026, single-timeframe,
live `$1` cap, 10k ticks/trade), these fixed thresholds behave completely differently
depending on the volatility of the era:

| | Recent (W1-W3) | Year-old (W4-W12) |
|---|---|---|
| M1 ATR mean | 0.74-1.07 pips | 0.96-1.61 pips |
| Pre-BE soft SL fires | 0.6-0.9% of trades | 4.9-12.5% |
| Never reached BE | 2.4-7.8% | 7.9-28.5% |
| Net per window | -$625 to -$1,458 | -$3,156 to -$5,038 |
| Pre-BE share of net loss | ~49% | **~91%** (-$31,712 of -$34,974) |

Entry excursion (no exit rule, real ticks) shows the moves are *bigger* in the
bad windows, not weaker -- avg peak $5.30 (recent W3) vs $7.58 / $10.99 / $14.24
(W4 / W8 / W12), with drawdown-from-peak scaling the same way. Over 92% of trades
go favorable at some point in every window.

The entry signal's direction edge is real and survives the bad era: normal minus
inverted is positive in **9 of 9** out-of-sample windows, +$5,482 total (~+$0.17/trade).

Conclusion: the signal is not the problem and post-BE is roughly break-even
(-$0.14/trade). A `$5` stop is a routine wiggle when average peaks are $14 --
trades are cut on noise before the move develops. Thresholds need to be constant
in *volatility* units, not dollars. This requires predicting nothing (unlike the
four regime-prediction rules already ruled out); it is pure normalization.

## Phase 1: backtest tooling

### 1.1 Per-trade threshold overrides in full-lifecycle mode
- Add `--pre-be-loss-threshold FLOAT`: overrides `ExitTradeConfig.max_loss_money`
  (currently only `--post-be-loss-cap` exists, so the pre-BE stop can't be varied
  in full-lifecycle mode at all -- `--sweep-pre-be-threshold` measures the pre-BE
  phase in isolation and can't show the full-lifecycle effect).

### 1.2 ATR-normalized thresholds
- Add `--atr-normalize`: scale thresholds per trade by the entry ATR.
- Add `--atr-baseline-pips FLOAT` (default `1.0`): the ATR at which thresholds
  equal their configured dollar values.
- Scale factor `s = clamp(atr_pips / baseline, 0.5, 3.0)`. Clamp bounds are fixed
  here, in advance, and are not to be tuned against results.
- Scaled: `max_loss_money`, `post_be_loss_cap_money`, `trail_gap_floor_money`.
- `atr_value` is currently computed *after* the simulate call -- move it before,
  so the per-trade `ExitTrade` can be built from it. Memoize `ExitTrade` instances
  by rounded scale factor (~thousands of trades per window).
- Log per-trade `atr_scale` and the resolved thresholds to the CSV.

## Phase 2: pre-registered evaluation

Pass criteria are fixed BEFORE running (same discipline as the staircase/regime tests;
see scratchpad `preregistration.md`):

- Windows: the 9 out-of-sample ones (start-pos 86401, 115201, 144001, 172801,
  201601, 230401, 259201, 288001, 316801) plus W1-W3 (1, 28801, 57601) as the
  low-volatility control.
- Baseline: current fixed thresholds, pct trail, `$1` cap, 10k ticks.
- Variant: `--atr-normalize --atr-baseline-pips 1.0`, everything else identical.
- **PASS if** the variant beats the baseline in at least 9 of 12 windows AND on total,
  AND does not make the three low-volatility control windows materially worse
  (no more than -$100/window).
- If it passes, a second run checks the staircase trail stacked on top
  (`--staircase-trail --staircase-tier-width 2.0`), since that is already confirmed.

## Phase 3: outcome -- COMPLETE

**Phase 2 FAILED**: ATR-normalized thresholds won 2 of 12 windows, -$565 on total
(bar was >=9 of 12 and positive). Cause: scaling all three thresholds together scaled
the `$1` post-BE cap up to `$3`, costing -$914 on W8 -- more than the softer SL and
wider trail floor gained (+$673). Per the plan, no retuning of the clamp bounds was
attempted to rescue it.

The tooling built here (`--pre-be-loss-threshold`, `--be-arming-ticks`, `--atr-normalize`)
then found the session's best result by testing the *inverse* hypothesis -- tightening
rather than widening:

**`EXIT_BE_ARMING_TICKS` 90 -> 30 PASSES: 10 of 12 windows, +$2,571, +$0.066/trade.**

Full write-up, including eight other failed ideas and the entry-signal findings:
`docs/test-results/pre-be-phase-and-entry-signal-investigation.md`.

**No `Config` change made.** Both surviving changes (the 30-tick wall and the `$2`
staircase trail) are backtest-only evidence and need forward testing on the demo
account before they are trusted -- the wide-cap result looked more convincing than
either and turned out to be noise around a handful of tail trades.

## Notes

- No `Config` / live-behavior change in this plan. Backtest tooling only.
- No tests (MVP/POC mode); user tests manually.
