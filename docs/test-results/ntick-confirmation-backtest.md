# N-Tick Confirmation Backtest

**Question**: would raising `Config.N_TICK_CONFIRMATION` above its current `1` (which makes the n-tick confirmation wrapper a no-op) help or hurt, against the live MTF config (session filter on, target=8/stop=5, zero-spread-validated per `zero-spread-retune`)?

**Bug found and fixed along the way (ships regardless of this backtest's conclusion)**: `NTickConfirmedSignalStrategy.generate_signal` assumed `candles` was always a flat list; wrapping `MultiTimeframeStrongSignalStrategy` (the live default) actually passes it `candles_by_tf`, a `{timeframe: [...]}` dict, and `candles[-1]` on a dict raised `KeyError`. This was dormant only because `N_TICK_CONFIRMATION=1` never triggers the wrap -- it would have silently zeroed out every candle-close entry the moment someone raised it above 1 while MTF stayed on, with the orchestrator's own `try/except` swallowing the exception every single call. Fixed in `_last_entry_candle`, which resolves the right candle for either shape.

**Second bug found, confirmed, NOT yet fixed -- blocking for production use**: `SignalOrchestrator._on_tick` calls `signal_generator.get_confirmed_signal()` directly (see `app/services/trade_services.py`) to retrieve and immediately execute a tick-confirmed entry. `SessionFilteredSignalStrategy`'s hour-blocking logic only lives inside `generate_signal` -- `get_confirmed_signal()` is a distinct method that was never given the same check, so a signal that n-tick confirms via real ticks bypasses the session filter entirely, regardless of wrapper order. Measured directly: **55-59% of every "confirmed" signal in this backtest occurred during the 08:00-18:59 UTC hours the session filter exists specifically to block.** Session filtering is what got MTF from "roughly breakeven" to "comfortably profitable" (`session-filter-analysis.md`) -- enabling n-tick confirmation today would silently undo that protection for every tick-confirmed trade. **This must be fixed before `N_TICK_CONFIRMATION` is ever raised above 1 in production**, independent of whatever this backtest concludes about confirmation's own value.

## Method

`scripts/backtest_signals.py --ntick N` (new): wraps the real, production `NTickConfirmedSignalStrategy` (via `strategy_factory`'s own `use_n_tick`/`n_ticks` overrides, reproducing the live wrapper order exactly) around the MTF strategy. For each M1 candle where it marks a new pending buy/sell, fetches real historical ticks (`mt5.copy_ticks_range`) for the ~60-second window between that candle's close and the next's -- exactly as long as the wrapper's own hard-reset-on-next-candle gives it live -- and feeds them through `on_new_tick`, checking `get_confirmed_signal()` after each. If/when confirmed, the outcome is evaluated from the actual confirmed tick price via a new `evaluate_signal_from_price`, not the candle close.

Because of the session-filter bypass bug above, the script's raw `--ntick` output faithfully reproduces what would actually happen live today, bug included -- inflated by confirmations during blocked hours the session filter is supposed to prevent. The baseline (no `--ntick`) run is not affected, since its signals only ever surface through `generate_signal`, which the session filter does correctly gate. To compare fairly, "corrected" numbers below exclude any confirmed signal whose UTC hour (via the confirming candle's time, broker offset +5h) falls in `Config.SESSION_FILTER_BLOCKED_HOURS_UTC` (8-18) -- simulating what the numbers would look like once the bypass bug is fixed.

All runs: `--mtf --target-pips 8 --stop-pips 5` (the live config), `spread_pips=0` (default, matching the account's confirmed near-zero real spread per `zero-spread-retune`), `--weeks 4`.

## Results

**Window 1** (most recent 4 weeks as of 2026-09-16):

| Config | Raw win rate (bug included, as coded today) | Confirmed signals in blocked hours | Corrected win rate (bug-fixed) | Corrected decided trades |
|---|---|---|---|---|
| Baseline (no confirmation, `N_TICK_CONFIRMATION=1`) | 47.8% | n/a (already filtered) | 47.8% | 134 |
| `--ntick 2` | 37.8% | 166/296 (56%) | **49.2%** | 124 |
| `--ntick 3` | 39.5% | 159/279 (57%) | **52.6%** | 114 |
| `--ntick 5` | 39.7% | 143/244 (59%) | **53.6%** | 97 |

Breakeven at 8:5 is 41.7%. In window 1, every corrected n-tick configuration clears breakeven comfortably and beats the baseline, improving monotonically with N (fewer, more selective trades).

**Window 2** (the preceding, non-overlapping 4 weeks -- same out-of-sample check every other investigation in this repo has used):

| Config | Raw win rate | Confirmed signals in blocked hours | Corrected win rate | Corrected decided trades |
|---|---|---|---|---|
| Baseline | 41.4% | n/a | 41.4% | 111 |
| `--ntick 3` | 38.6% | 128/233 (55%) | **39.8%** | 98 |

**Window 2 flatly contradicts window 1**: corrected `--ntick 3` (39.8%) underperforms baseline (41.4%) and falls *below* breakeven, where window 1 showed a clear, growing improvement. This is exactly the failure mode this project's methodology exists to catch (see `mtf-gate-sensitivity-sweep.md`'s own "gate-tuning conclusions are tied to the specific indicator behavior they were tuned against" lesson, and every ratio/weight sweep's insistence on a second window before trusting a result).

## Conclusion

**No recommendation to enable.** The promising window-1 result does not survive an independent second window -- window 1's apparent edge looks like this window's specific price action, not a reproducible property of n-tick confirmation. `Config.N_TICK_CONFIRMATION` stays at `1` (unchanged, still effectively disabled).

Separately, the session-filter bypass bug found here is real and unrelated to whether confirmation itself has value -- it should be fixed on its own merits before `N_TICK_CONFIRMATION` is ever considered again, so that a future backtest of this question doesn't need the manual correction step this one required.

## Possible follow-ups (not pursued here)

- Fix the session-filter bypass (`SessionFilteredSignalStrategy` needs to gate `get_confirmed_signal()`, not just `generate_signal()` -- needs a correctly-time-sourced check, not `datetime.now()` on the local machine, since the rest of this filter reasons in broker/candle time).
- A third window, or a longer combined window, to see whether window 1 or window 2 is the outlier (two windows isn't enough to say which, only enough to say they disagree).
- Retest single-timeframe (not just MTF) with n-tick confirmation -- not done here since MTF is the live default and this already produced a clear (negative) answer.
- `min_pip_move` is `0.0` (the class's own default, no matching `Config` field exists to test other values) -- confirmation currently accepts any non-adverse tick, including flat ones, as "favorable". A stricter `min_pip_move` might filter out some of the noise the window-2 result suggests exists, but that's a new lever, not this question.
