# N-Tick Confirmation Backtest

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

`Config.N_TICK_CONFIRMATION=1` means the n-tick confirmation wrapper (`NTickConfirmedSignalStrategy`) never actually gets applied today (`strategy_factory` only wraps when `n_ticks>1`) -- it's a flag that's "on" but inert. User asked whether it affects entries (no, currently), then asked to backtest it -- i.e. answer whether raising `N_TICK_CONFIRMATION` above 1 would help or hurt, the same way MTF/session-filter/RSI-weight/ML-entry were each backtested before any live decision was made about them (`docs/test-results/`).

`scripts/backtest_signals.py`'s own docstring says n-tick confirmation is explicitly **not supported** -- it replays bar (candle) history only, and n-tick confirmation needs real tick-by-tick price movement between candle closes to ever confirm. This plan builds that support using real historical tick data (`mt5.copy_ticks_range`, confirmed available at least 4+ weeks back, ~100-250 ticks/minute on this account) rather than approximating it from candles.

## Bug found while scoping this (fixed here, ships regardless of the backtest's outcome)

`NTickConfirmedSignalStrategy.generate_signal` assumes `candles` is a flat list (`last_candle = candles[-1] if candles else None`) to find the latest close/time. But when it wraps the **MTF** strategy -- the live default (`USE_MULTI_TIMEFRAME_SIGNALS=True`) -- the live factory order wraps `NTickConfirmedSignalStrategy` *outside* `MultiTimeframeStrongSignalStrategy`, so `candles` there is actually `candles_by_tf`, a `{timeframe: [...]}` dict. `candles[-1]` on a dict raises `KeyError: -1`. Confirmed directly: a stub MTF-shaped strategy wrapped in `NTickConfirmedSignalStrategy` raises exactly this on `generate_signal(candles_by_tf)`.

This is currently dormant (only reachable if `N_TICK_CONFIRMATION>1`, which it isn't), but would have completely broken candle-close entry generation the moment someone raised it above 1 while MTF stayed on -- the orchestrator's `try/except` around `generate_signal` would swallow the `KeyError` every single call and silently produce zero entries, forever, with no crash to notice. Fixed as a prerequisite: `generate_signal` now resolves the latest entry-timeframe candle correctly whether `candles` is a list (single-tf) or a `candles_by_tf` dict (MTF), using `config.TF_ENTRY` to pick the right key in the dict case (mirrors the lookup `MultiTimeframeStrongSignalStrategy._get` already does).

## Design: real-tick simulation

For each M1 candle in the backtest window, feed it through a *real* `NTickConfirmedSignalStrategy` instance (reusing production code, not reimplementing its logic) wrapping the already-selected base/MTF strategy. When that call marks the wrapper `_waiting` (a new pending buy/sell), fetch real historical ticks for the ~60-second window between this candle's close and the next candle's close (`mt5.copy_ticks_range`) and feed them one at a time into `on_new_tick`, checking `get_confirmed_signal()` after each -- exactly mirroring what the orchestrator's tick loop does live, and exactly matching the wrapper's own hard-reset-on-next-candle behavior (a pending signal only gets that one candle's worth of real ticks to confirm, same as live). If/when confirmed, record the actual confirmed tick price and index for outcome evaluation (via a new `evaluate_signal_from_price` -- same win/loss/undecided target-stop logic as `evaluate_signal`, parameterized on an explicit entry price/start index instead of assuming `candles[entry_index]["close"]`, since the real confirmed price is a live tick price, not the candle close).

One `copy_ticks_range` call per pending signal (not per candle) -- bounded by how many buy/sell raw signals actually occur (hundreds, not tens of thousands), keeping runtime reasonable.

## Out of scope

- Deciding to actually raise `Config.N_TICK_CONFIRMATION` -- this plan answers the backtest question, same as every prior investigation in this repo; the config flip (if any) is a separate decision made from the result.
- Any other latent bugs the dict-vs-list candles shape might cause elsewhere -- only the one hit while building this is fixed.
- No tests, per MVP/POC mode.

## Phase 1: Fix the dict-candles bug + build tick-based n-tick backtest support

- **`app/signals/strategies/ntick_confirmed_signal_strategy.py`**: fix `generate_signal` to resolve the latest entry-timeframe candle for both list and dict-shaped `candles` (see bug section above).
- **`scripts/backtest_signals.py`**: add `--ntick N` (and `--ntick-min-pip-move`, mirroring `Config`'s own `min_pip_move` default of `0.0`) to both the single-tf and `--mtf` paths. Add `evaluate_signal_from_price` (explicit entry price/start index variant of `evaluate_signal`) and the real-tick confirmation loop described above. Update the module docstring (the "not supported" note becomes stale).

## Phase 2: Run the sweep, report results -- done

- Ran `--ntick 2/3/5` against the live shape (`--mtf`, session filter on, `target=8/stop=5`, zero spread) over the same 4-week window as baseline.
- **Second bug found while running the sweep (not part of the original plan, confirmed and documented, not yet fixed)**: `SessionFilteredSignalStrategy` only gates `generate_signal`, not `get_confirmed_signal` -- which `SignalOrchestrator._on_tick` calls directly to execute confirmed entries. Measured: 55-59% of every "confirmed" signal in this backtest fell inside the session filter's blocked hours. This bypasses the exact protection that got MTF from breakeven to profitable, and must be fixed before `N_TICK_CONFIRMATION` is ever raised in production, independent of the result below.
- Applied a corrected (session-hours-respected) recompute to compare fairly against baseline (which is already correctly filtered).
- **Window 1**: every corrected n-tick config (2/3/5) beat baseline and cleared breakeven, improving with N. **Window 2 (independent, non-overlapping)**: `--ntick 3` corrected *underperformed* baseline and fell below breakeven -- flatly contradicting window 1.
- **Conclusion: no recommendation to enable.** The window-1 result doesn't survive an out-of-sample check -- looks like that window's specific price action, not a reproducible edge. `N_TICK_CONFIRMATION` stays at `1`.
- Full tables and detail: `docs/test-results/ntick-confirmation-backtest.md`.
- Single-timeframe comparison (originally planned) skipped -- MTF is the live default and already gave a clear (negative) answer; not worth the extra real-tick-fetch runtime for a strategy that isn't live anyway.
- `--ntick-min-pip-move` (originally planned) dropped -- there's no matching `Config` field for it, so a CLI-only knob wouldn't reflect anything the live system can actually be configured to do; used the class's own `min_pip_move=0.0` default throughout instead.
