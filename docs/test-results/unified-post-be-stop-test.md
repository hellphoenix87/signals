# Unified Post-BE Stop Test — Removing the Trail's Positivity Gate

Date: 2026-09-21

## Setup

- **User's question**: "shouldn't the trailing stop start at BE?" Investigation of `ProfitExitManager.check_exit_on_tick` found it technically does — `best_profit` tracking begins the moment breakeven arms. But the exit *check* is gated behind `if 0.00 < profit < state.best_profit`. If a fast tick-to-tick move jumps straight from just-above-trigger to already non-positive in a single step (easy when the trigger window is only a few cents wide for a small peak), that whole check is skipped for that tick — profit is no longer strictly positive — and only the separate, much looser `LossExitManager` post-BE cap (-$1) can catch it from there. This is the specific, concrete mechanism behind why tightening the trail's percentage (`tight-trail-test.md`) never touched `profit_drop_after_be` at all.
- **Test**: `simulate_full_lifecycle_unified_stop()`, a new function replacing the real post-BE two-manager sequence with a single combined check — `trigger = peak - max(floor, pct × peak)` — evaluated every tick **regardless of sign**. Pre-BE phase (arming, soft-SL, timeout) is untouched, using the real `LossExitManager`. Backtest-only; no production code or live Config touched.
- Same window/gate, same tight-trail parameters (10% of peak, no floor) already validated as the better trail shape, for both indicators.

## Results

| Entry | Config | Total P&L | Win rate | Dominant outcome |
|---|---|---|---|---|
| SMA | Original (60%/$2, $1 cap) | -$91.60 | 28.8% | `profit_drop_after_be` 250 (62.0%), avg -$1.14, avg 183.8 ticks |
| SMA | Tight trail only (10%/$0, $1 cap) | -$37.00 | 28.8% | `profit_drop_after_be` 250 (62.0%), avg -$1.14 (unchanged) |
| SMA | + $0 BE-floor cap | -$109.00 | 4.0% | `profit_drop_after_be` 350 (86.6%), avg -$0.22, avg 21.1 ticks |
| SMA | **Unified stop (no positivity gate)** | **-$105.20** | **6.6%** | `unified_stop` 369 (90.7%), avg **-$0.07**, avg **7.6 ticks** |
| MACD | Original (60%/$2, $1 cap) | -$21.60 | 31.5% | `profit_drop_after_be` 73 (58.9%), avg -$1.13, avg 222.9 ticks |
| MACD | Tight trail only (10%/$0, $1 cap) | -$19.20 | 31.5% | `profit_drop_after_be` 73 (58.9%), avg -$1.13 (unchanged) |
| MACD | + $0 BE-floor cap | -$32.20 | 6.5% | `profit_drop_after_be` 104 (83.9%), avg -$0.22, avg 14.4 ticks |
| MACD | **Unified stop (no positivity gate)** | **-$32.20** | **5.6%** | `unified_stop` 112 (90.3%), avg **-$0.09**, avg **6.8 ticks** |

## Findings

1. **The underlying diagnosis was correct — the positivity gate is real, and removing it does exactly what it should**: average loss per capped trade shrank to nearly nothing (-$0.07/-$0.09, vs -$1.13/-$1.14 originally). The mechanism works as designed.
2. **But it backfires just as badly as the earlier $0-cap test, for a related but distinct reason.** Without the positivity requirement, the combined check now evaluates from the very first tick after arming — and since breakeven itself arms on a median of 1 tick (`be_arm_ticks`, prior finding), the peak at that point is often just a few cents. A 10%-of-peak trigger on a $0.05 peak is $0.005 — smaller than ordinary bid/ask noise — so the very next tick trips it. Average resolution time collapsed to **6.8-7.6 ticks**, even faster than the $0-cap test's 21.1/14.4 ticks. 98-99% of trades that reach breakeven never exceed a $1 peak under this mechanism — essentially none get the chance to develop into anything.
3. **Two distinct "loosen the gate" attempts, two distinct failure modes, same underlying problem**: the $0-cap test failed because $0 is a low absolute bar that ordinary post-arming noise reaches quickly; this test fails because a percentage-of-peak trigger with no floor and no positivity requirement is *smaller than noise itself* when peak is still tiny, so it fires before peak has any chance to grow. Both remove a "gate" that was, however clumsily, buying trades survival time.
4. **This meaningfully updates the picture**: it's not that the trail's specific mechanics (percentage vs. floor, positivity gate or not) are the lever — every variant tested that tightens the *effective* response to a small, early peak makes things worse, not better, because the problem isn't precision, it's that these trades haven't developed into anything yet when any of these mechanisms engage.

## What this rules out, and what's still untried

Across this whole exit-strategy investigation:
- **Entry-signal quality**: real (`entry-excursion-trace.md` — substantial favorable peaks 90%+ of the time, regardless of indicator).
- **Simple trail tightening** (60%→10% of peak, keeping the $1 cap): helps meaningfully, doesn't fix it (`tight-trail-test.md`).
- **Flooring the loss cap at exactly $0**: backfires badly (`be-floor-trail-test.md`).
- **Removing the trail's positivity gate**: backfires just as badly, for a related reason (this test).

**Two things not yet tried**: (1) an intermediate cap value between $0 and $1 (e.g. -$0.30 to -$0.70) combined with the tight trail — smaller than today's $1 but not so tight it fires on pure noise; (2) a minimum-peak-size or minimum-survival-time gate before *any* tight exit mechanism engages at all — so a trade gets some initial room to develop past the earliest noise regardless of which stop design is layered on top of that room. Both of the failed attempts here removed a gate without replacing it with a *different* gate suited to the actual noise floor near entry — that noise floor, not the stop's shape, looks like the real constraint.

## Caveats

- Single 4-week window, same as everything else this session.
- No live Config change — every configuration in this test is backtest-only.
