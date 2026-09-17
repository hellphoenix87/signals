# N-Tick Confirmation Backtest

**Question**: would raising `Config.N_TICK_CONFIRMATION` above its current `1` (which makes the n-tick confirmation wrapper a no-op) help or hurt, against the live MTF config (session filter on, target=8/stop=5, zero-spread-validated per `zero-spread-retune`)?

**Bug found and fixed along the way (ships regardless of this backtest's conclusion)**: `NTickConfirmedSignalStrategy.generate_signal` assumed `candles` was always a flat list; wrapping `MultiTimeframeStrongSignalStrategy` (the live default) actually passes it `candles_by_tf`, a `{timeframe: [...]}` dict, and `candles[-1]` on a dict raised `KeyError`. This was dormant only because `N_TICK_CONFIRMATION=1` never triggers the wrap -- it would have silently zeroed out every candle-close entry the moment someone raised it above 1 while MTF stayed on, with the orchestrator's own `try/except` swallowing the exception every single call. Fixed in `_last_entry_candle`, which resolves the right candle for either shape.

**Second bug found and fixed (also ships regardless of this backtest's conclusion)**: `SignalOrchestrator._on_tick` calls `signal_generator.get_confirmed_signal()` directly (see `app/services/trade_services.py`) to retrieve and immediately execute a tick-confirmed entry. `SessionFilteredSignalStrategy`'s hour-blocking logic only lived inside `generate_signal` -- `get_confirmed_signal()` was a distinct method that was never given the same check, so a signal that n-tick confirms via real ticks bypassed the session filter entirely, regardless of wrapper order. Measured directly on the first pass: **55-59% of every "confirmed" signal occurred during the 08:00-18:59 UTC hours the session filter exists specifically to block.** Session filtering is what got MTF from "roughly breakeven" to "comfortably profitable" (`session-filter-analysis.md`), so this was a real, live-relevant gap. **Fixed**: `SessionFilteredSignalStrategy.get_confirmed_signal(tick_time)` now vetoes a confirmation during blocked hours, using the confirming tick's own candle-frame time (same basis `get_broker_utc_offset_hours` uses -- not wall-clock `datetime.now()`); `SignalOrchestrator._on_tick` now passes that time, with a `TypeError` fallback for any `get_confirmed_signal` that doesn't accept it. The results below are from the re-run against this fix, not the manual post-hoc correction the first pass needed.

## Method

`scripts/backtest_signals.py --ntick N` (new): wraps the real, production `NTickConfirmedSignalStrategy` (via `strategy_factory`'s own `use_n_tick`/`n_ticks` overrides, reproducing the live wrapper order exactly) around the MTF strategy. For each M1 candle where it marks a new pending buy/sell, fetches real historical ticks (`mt5.copy_ticks_range`) for the ~60-second window between that candle's close and the next's -- exactly as long as the wrapper's own hard-reset-on-next-candle gives it live -- and feeds them through `on_new_tick`, checking `get_confirmed_signal(tick_time)` after each (the real, fixed interface). If/when confirmed, the outcome is evaluated from the actual confirmed tick price via a new `evaluate_signal_from_price`, not the candle close.

All runs: `--mtf --target-pips 8 --stop-pips 5` (the live config), `spread_pips=0` (default, matching the account's confirmed near-zero real spread per `zero-spread-retune`), `--weeks 4`.

## Results

All numbers below are from the real, fixed code path (session filter correctly applied to confirmed signals) -- no manual correction needed. A first pass (before the fix) measured the bypass directly: 55-59% of "confirmed" signals fell in blocked hours; re-running after the fix drops each `--ntick` run's total signal count by roughly that fraction, confirming the fix actually engages.

**Window 1** (most recent 4 weeks as of 2026-09-17):

| Config | Win rate | Decided trades |
|---|---|---|
| Baseline (no confirmation, `N_TICK_CONFIRMATION=1`) | 44.8% | 143 |
| `--ntick 2` | 46.2% | 132 |
| `--ntick 3` | **49.2%** | 122 |
| `--ntick 5` | 48.6% | 109 |

Breakeven at 8:5 is 38.46% (`stop/(target+stop)` = `5/13`; a since-corrected earlier version of this report stated 41.7%, the *7:5* ratio's breakeven from before the zero-spread retune -- unrelated to this backtest's conclusion, but worth getting right). In window 1, every n-tick configuration clears breakeven and beats the baseline, peaking at N=3.

**Window 2** (the preceding, non-overlapping 4 weeks -- same out-of-sample check every other investigation in this repo has used):

| Config | Win rate | Decided trades |
|---|---|---|
| Baseline | 39.1% | 110 |
| `--ntick 3` | 36.7% | 98 |

**Window 2 again contradicts window 1**: `--ntick 3` (36.7%) underperforms baseline (39.1%) and falls below the 38.46% breakeven, where window 1 showed a clear improvement. Baseline itself is only marginally above breakeven here (+0.6 pts) -- window 2 is a thin result for MTF generally, not just for n-tick -- but n-tick still makes it worse, not better. Same conclusion as the first pass (which used a manual post-hoc correction rather than the real fix) -- this isn't an artifact of that correction being imprecise; the real, fixed code reproduces the identical qualitative result. This is exactly the failure mode this project's methodology exists to catch (see `mtf-gate-sensitivity-sweep.md`'s own "gate-tuning conclusions are tied to the specific indicator behavior they were tuned against" lesson, and every ratio/weight sweep's insistence on a second window before trusting a result).

## Conclusion

**No recommendation to enable.** The promising window-1 result does not survive an independent second window, whether measured via the manual correction or the real fix -- window 1's apparent edge looks like that window's specific price action, not a reproducible property of n-tick confirmation. `Config.N_TICK_CONFIRMATION` stays at `1` (unchanged, still effectively disabled).

The session-filter bypass bug is fixed regardless (real, independent value -- would have been a live safety issue the moment this or anything else ever raised `N_TICK_CONFIRMATION`), even though the feature it protects stays off.

## Possible follow-ups (not pursued here)

- A third window, or a longer combined window, to see whether window 1 or window 2 is the outlier (two windows isn't enough to say which, only enough to say they disagree).
- Retest single-timeframe (not just MTF) with n-tick confirmation -- not done here since MTF is the live default and this already produced a clear (negative) answer.
- `min_pip_move` is `0.0` (the class's own default, no matching `Config` field exists to test other values) -- confirmation currently accepts any non-adverse tick, including flat ones, as "favorable". A stricter `min_pip_move` might filter out some of the noise the window-2 result suggests exists, but that's a new lever, not this question.
