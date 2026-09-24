# Tech debt inventory

Captured 2026-09-24. Items 1-4 and 6 were worked in `docs/plans/done/tech-debt-sweep.md`
(same day); what remains is items 5, 7 and 8.

## ~~1. The test suite has been broken for months~~ -- RESOLVED

`pipenv run pytest` was at **44 failed, 122 passed**, unchanged across at least four merged PRs.
Now **150 passed, 0 failed**.

Almost every failure was a test written against an API that was later deleted: `TradingMode.DEMO`
(demo mode removed), `SignalOrchestrator(broker=/trading_service=/enter_trade=)` (old ctor plus a
fallback ladder that no longer exists), `app.trade_execution.helpers.prepare_trade` (deleted in
PR #42, and aborting collection for the whole run).

Obsolete tests were deleted rather than repaired. Some coverage was knowingly dropped in the
process and is worth rebuilding -- see item 7.

## ~~2. `TradeExecutor._spread_ok` has zero test coverage -- and is now live~~ -- RESOLVED

Covered in `tests/trade_execution/test_trade_execution.py::TestSpreadGate`: disabled at 0 (fails
open without even fetching a tick), blocks above threshold, allows below, allows exactly at
threshold (the comparison is `<=`), and fails open when the tick or point size is unavailable.
Plus a paired block/allow run through `execute_signals` proving the gate is what stops the order.

## ~~3. Stale documentation~~ -- RESOLVED

`CLAUDE.md` referenced `helpers/prepare_trade.py`, `TradingMode.DEMO`, the deleted entry fallback
ladder, a nonexistent `CandleCollector` class, and described `endpoints.py` as importing factory
singletons directly (it uses `Depends()` now). All corrected and verified by grepping every
backticked module path and symbol against the tree.

## ~~4. Config that reads as enabled but is inert~~ -- RESOLVED

`USE_N_TICK_CONFIRMATION` is now `False`, matching what the bot actually does (the factory only
wraps at `N_TICK_CONFIRMATION > 1`). No behavior change. `USE_ML_ENTRY_MODEL` and
`ENTRY_ATR_PERIOD/MOVE_MULT` keep their defensible feature-flag defaults but now say in a comment
that they are built-but-inert. `tests/signals/test_signal_generation.py` pins it.

## 5. MTF backtest is needlessly slow

`MultiTimeframeStrongSignalStrategy` recomputes the M15 bias and M5 confirm strategies on
**every one of ~28,800 M1 candles**, though those only change every 15 and 5 minutes. Caching
per higher-timeframe bar should give a 3-5x speedup. Currently an MTF window costs the same
wall-clock time as an STF one while producing 21x fewer trades, which is why MTF research was
abandoned in favour of STF.

Deliberately not fixed: it optimizes a path nothing currently runs. Worth doing only if MTF
research restarts.

## ~~6. Unvalidated live changes~~ -- SMOKE RUN DONE 2026-09-24

Ran against the live terminal (account 112575722, MetaQuotes-Demo, `trade_mode=0`, $100k).
**No orders were placed and the balance is unchanged.**

What was confirmed:

- App boots: `mt5.initialize()` via lifespan succeeds, `Broker initialized in TradingMode.LIVE`,
  all of `/status`, `/tick`, `/signal/latest`, `/simulated_positions` respond. Trading does not
  auto-start -- `/trading/start` is explicit.
- Signals generate on every M1 close with MTF active (`m15_bias`, `m5_confirm`, `m1_entry`,
  `adx`). Two candle closes observed, both `hold`, so no entry was attempted.
- **`_spread_ok` verified on the real path** -- real `MarketData.get_symbol_tick`, real
  `Broker.get_point_size`, real `execute_signals`, with only `place_buy`/`place_sell`
  intercepted so a gate failure would be recorded rather than sent. 30 runs against live ticks
  with the threshold forced to 0.5 points: **30/30 correct** (8 blocked at 1.0 pts with the
  expected `Skipping signal (spread too wide)` log, 22 allowed at 0.0 pts).
- `get_broker_utc_offset_hours` resolves to 5 and the session filter compares true UTC
  correctly (candle-basis 00:32 - 5h = 19:32 UTC). Gating is not shifted.

**Finding worth acting on: `MAX_SPREAD_POINTS = 10` is effectively inert on this feed.**
Sampled live EURUSD spread is **0-1 points (0.0-0.1 pips)** -- 14 of 20 samples were exactly
0.0, the rest 1.0. PR #70 tuned 15 -> 10 as "~3x the 0.27-0.39 pip spread EURUSD normally
shows", i.e. it assumed 2.7-3.9 points; the live demo feed is roughly 4x tighter than that
assumption. At 10 points the gate will only ever fire during a rollover spike, which may well be
the intent -- but it is not doing anything the rest of the day, and the 15 -> 10 retune changed
nothing observable. Worth deciding whether the threshold should track the feed this account
actually quotes, and whether that feed is representative of the broker this would run against
for real.

`EXIT_BE_ARMING_TICKS = 30` was **not** exercised: it only engages once a position is open, and
no signal went actionable during the run. Still unvalidated live.

## 7. Exit-strategy coverage dropped during the item-1 cleanup

These had tests before the sweep and have none now. Each was deleted rather than repaired, per
the "delete obsolete, don't fix" call made during that work:

- `ExitTrade.on_tick`'s per-ticket cooldown gate (was `tests/e2e/test_exit_trade_cooldown_gate.py`,
  harnessed on the removed `TradingMode.DEMO`).
- `ProfitExitManager.check_exit_on_candle_close`'s HTF gating (was
  `tests/e2e/test_exit_trade_candle_close_htf_gating.py`, same reason, plus two unit tests).
- `LossExitManager`'s break-even arming window and `failed_to_reach_be` force-close. The two tests
  covering it were broken in a specific way worth knowing: their fixture tick sat 10 pips adverse
  to the entry, which trips `_pre_be_soft_sl_hit` on tick 1, so they returned `profit_drop` and
  never reached the arming logic at all. They also asserted `be_arming_ticks == 20` against a
  config that now says 30.

This is money-moving logic with no safety net. Rebuilding it against the current API is a small,
self-contained piece of work.

## 8. `docs/plans/in-progress/project-refactor-sweep.md` never finished

Stalled after Subphase 5.3 on 2026-09-13; 5.4 and 5.5 (both pure test backfill, on
`ProfitExitManager`'s BE-arming path and `LossExitManager`'s recovered-after-unprofitable path)
never ran. Several of the tests deleted under item 1 came from that plan. Either fold its
remaining two subphases into the item-7 work or close it out as superseded -- but it should not
sit in `in-progress/` indefinitely.
