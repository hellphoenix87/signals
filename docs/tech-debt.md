# Tech debt inventory

Captured 2026-09-24. Items 1-4 were worked in `docs/plans/done/tech-debt-sweep.md`
(same day); what remains is items 5 and 6.

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

## 6. Unvalidated live changes

Two `Config` changes shipped on backtest evidence alone, with no live verification:

- `MAX_SPREAD_POINTS` (PR #66, retuned to 10 in PR #70)
- `EXIT_BE_ARMING_TICKS = 30` (PR #68)

The app has not been run since either change. A single smoke run against MT5 -- confirming it
starts, generates signals, and logs `Skipping signal (spread too wide)` when spread exceeds the
configured points -- would close this. Needs a running MT5 terminal, so it is a manual step.

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
