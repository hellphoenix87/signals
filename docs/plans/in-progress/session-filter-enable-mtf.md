# Session Filter + Enable Multi-Timeframe Signals for Live Trading

Status: todo
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Two changes, bundled because the second depends on validating the first:

1. **Build a real session (hour-of-day) filter.** Backtest analysis (this session, not yet in `docs/test-results/` -- written up as part of this plan instead) found MTF's win rate differs sharply by UTC hour: signals during 08:00-18:59 UTC (London open through the NY session) badly underperform signals outside that window, across two independent 4-week windows (pooled: ~28% inside vs. 42.2% outside, against a 41.7% breakeven for MTF's own best ratio). Single-timeframe showed no such effect (pooled ~42.5% both blocks) -- this filter should help MTF and be a no-op for single-timeframe, not something that needs separate tuning per strategy.
2. **Turn `Config.USE_MULTI_TIMEFRAME_SIGNALS` on.** This has been deliberately `False` since it was built (`mtf-signal-folding` plan) pending backtest validation. That validation is now done: MTF at its own best ratio (target=7/stop=5) combined with the session filter reaches 42.2% win rate pooled across two independent windows, right at the 41.7% breakeven -- the strongest result in the whole investigation, though still a thin, not-clearly-profitable margin. Also update `Config.DEFAULT_TP_PIPS`/`DEFAULT_SL_PIPS` from the stale 50/2 to the validated 7/5 -- these are what `TradeExecutor` actually uses for every real order's SL/TP (confirmed via `app/trade_execution/trade_execution.py:82-83`), so leaving them unchanged would mean live trades ignore everything this investigation found.

## Out of scope (explicit, not forgotten)

- Applying the session filter to single-timeframe's *decision* to trade at all -- it's wired generically (works for either strategy) but evidence only supports it mattering for MTF. Single-timeframe stays exactly as validated (`ENTRY_RSI_WEIGHT=2.5`, crossover MACD), untouched by this plan.
- Any change to the exit strategy (`app/exit_strategies/`) -- still tick-driven, unrelated to this entry-side filter.
- The daily loss cap, ATR entry filter, and full exit-strategy-aware backtesting -- all still separately deferred per existing project history.
- News/event blackout filtering -- a different, not-yet-explored idea, discussed but not started.
- No tests, per MVP/POC mode. Verified via direct backtest runs against real MT5 historical data, reproducing the scratch-script findings from this session.

## Phase 1: Session filter mechanism

- **`app/config/settings.py`**: add `USE_SESSION_FILTER: bool = True`, `SESSION_FILTER_BLOCKED_HOURS_UTC: list[int] = list(range(8, 19))` (08:00-18:59 UTC), `SESSION_FILTER_UTC_OFFSET_HOURS: Optional[int] = None` (`None` = auto-detect live via a real MT5 tick vs. true UTC; pin an int to make offline/deterministic tests reproducible without an MT5 connection).
- **New helper** (in `app/signals/signal_generation.py`, alongside `build_indicator`): `get_broker_utc_offset_hours(default=5)` -- compares a live `mt5.symbol_info_tick` against true UTC now to determine the hour offset between candle timestamps (as `MarketData` constructs them, via `datetime.fromtimestamp` on the MT5 epoch) and true UTC. This offset depends on both the local machine's timezone and the broker server's own timezone, so it must be computed live, not hardcoded -- a hardcoded value would silently drift wrong across a DST change or a different deployment machine. Falls back to `default` if MT5 isn't reachable.
- **New class** (`app/signals/strategies/session_filtered_signal_strategy.py`): `SessionFilteredSignalStrategy`, wrapping any `BaseSignalStrategy` -- mirrors the existing `NTickConfirmedSignalStrategy` decorator pattern. Takes the wrapped strategy, a set of blocked UTC hours, a `time_extractor` callable (resolves "the current candle's time" from whatever `candles` argument the wrapped strategy receives -- a flat list for single-timeframe, a `{timeframe: [...]}` dict for MTF), and the UTC offset. `generate_signal` calls through to the wrapped strategy, then forces `final_signal`/`raw_signal` to `"hold"` (adding `session_filtered: True` to the result for visibility) if the resolved hour falls in the blocked set; passes through unchanged otherwise, including on `{"error": ...}` results.
- **`strategy_factory`**: when `getattr(config, "USE_SESSION_FILTER", False)`, wrap the final `strategy` (after the `use_multi`/`use_n_tick` wrapping already there) in `SessionFilteredSignalStrategy`, with the time-extractor closing over `tf_entry` for the MTF case (`candles_by_tf.get(tf_entry, [])`) or just `candles[-1]["time"]` for the single-timeframe case.

## Phase 2: Backtest tooling + verification

- **`scripts/backtest_signals.py`**: add `--session-filter` (store_true), threading a `use_session_filter` flag through to `strategy_factory(..., config=config)` for both `run_backtest` and `run_mtf_backtest` (config already flows through from the sensitivity-sweep work earlier -- just needs `USE_SESSION_FILTER` set on the per-run `Config` subclass override when the flag is passed, same pattern as `--mtf-score-threshold`).
- **Verify**: run `scripts/backtest_signals.py --mtf --session-filter --target-pips 7 --stop-pips 5 --spread-pips 1` on both the current window and the second window (`--start-pos 28801`) used throughout this investigation, and confirm the pooled result matches the session-filter finding from this session's scratch analysis (~42.2% pooled, vs. 41.7% breakeven) -- this is the sanity check that the production-wired filter behaves identically to the validated one-off script, not a new discovery.
- Write up the session-filter finding (hour-by-hour tables, both windows, the MTF-vs-single-tf contrast, the pooled combined result) as `docs/test-results/session-filter-analysis.md`, continuing the established report format for this repo, since the analysis itself was done via scratch scripts this session and isn't documented anywhere yet.

## Phase 3: Enable MTF for live trading

- **`app/config/settings.py`**: flip `USE_MULTI_TIMEFRAME_SIGNALS` from `False` to `True`. Update `DEFAULT_TP_PIPS` from `50.0` to `7.0` and `DEFAULT_SL_PIPS` from `2.0` to `5.0` (already comfortably above `MIN_SL_PIPS=5.0`, no floor-clamping surprise).
- Update the `docs/plans/done/mtf-backtest-validation.md`/project memory framing -- this plan's own final commit documents the decision reversal (`USE_MULTI_TIMEFRAME_SIGNALS` was "deliberately left `False` ... since none of it is backtested yet" -- it now is, with real caveats).

## Verification (manual, per MVP/POC mode)

- `strategy_factory(config=Config)` (single-timeframe, default) still produces `final_signal` unaffected by the session filter outside blocked hours, and forced to `"hold"` with `session_filtered: True` inside them, confirmed via direct construction with a few sample candle timestamps spanning both blocked and allowed hours.
- `strategy_factory(config=Config, use_multi=True)` (now the live default) behaves the same way using the MTF entry timeframe's candle time.
- `scripts/backtest_signals.py --mtf --session-filter` reproduces the scratch-script's pooled ~42.2% finding on both windows.
- Confirm `TradeExecutor` would now use `sl_pips=5.0`/`tp_pips=50.0` -- wait, `tp_pips=7.0`/`sl_pips=5.0` -- for any signal that doesn't carry its own `sl_pips`/`tp_pips` (neither strategy currently sets those), by constructing a `Config`-driven call and checking the resolved values directly.

**Honest caveat carried forward into this plan's final commit and PR description**: the validated result (42.2% pooled vs. 41.7% breakeven) is a thin margin on a modest sample (256 decided trades across two windows) with realistic spread modeled on this demo account's own historical data, not a live-verified real-broker spread. This is "no longer clearly losing," not "confirmed profitable" -- flagged explicitly so this isn't overstated when handed to the user for the merge decision.
