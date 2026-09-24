# Tech debt inventory

Captured 2026-09-24. Everything here predates the spread-gate/arming-wall work unless noted.

## 1. The test suite has been broken for months

`pipenv run pytest` reports **44 failed, 122 passed**, and that count is identical at
`9effdbe`, `25991ef`, `931d831` and `bed1410` -- i.e. unchanged across at least four merged
PRs. There is currently **no working safety net**: a regression from any change would not be
caught.

- `tests/e2e/test_orchestrator_enter_trade_dispatch.py` fails at *collection* with
  `ModuleNotFoundError: app.trade_execution.helpers.prepare_trade`. That module was deleted in
  PR #42 ("Entry config wiring, DI cleanup, and streamlining"); the test was never updated.
  One collection error aborts the whole run unless `--ignore`d.
- Remaining failures cluster in `tests/services/test_trade_services.py` and
  `tests/trade_execution/test_broker.py`.
- `tests/exit_strategies/managers/test_loss.py` has two arming-window tests that assert
  `be_arming_ticks is 20` and whose fixture trips the `$5` pre-BE soft SL on tick 1, so they
  fail before reaching the arming logic they mean to test. Unrelated to
  `EXIT_BE_ARMING_TICKS 90 -> 30` (they failed identically before that change).

**Do not treat a green-ish run as meaningful until this is fixed.** Fix or delete, but don't
leave 44 failures as ambient noise.

## 2. `TradeExecutor._spread_ok` has zero test coverage -- and is now live

`MAX_SPREAD_POINTS = 15` shipped in PR #66, enabling a gate that **no test has ever executed**.
`grep -rl "_spread_ok\|MAX_SPREAD_POINTS" tests/` returns nothing.

The backtest does NOT cover it either: it uses its own `--max-entry-spread-pips` check against
the entry tick, while production calls `_spread_ok`, which fetches a live tick via
`market_data.get_symbol_tick` and divides by `broker.get_point_size`. Same intent, different
code path. Its only verification so far is a hand-run units check.

Worth covering: gate disabled at 0 (fails open), blocks above threshold, allows below, and
fails open when the tick or point size is unavailable.

## 3. Stale documentation

- `CLAUDE.md` documents `app/trade_execution/helpers/prepare_trade.py` ("builds trade request
  dicts and provides `enter_trade`"). The file does not exist; only `__init__.py` remains in
  that package.
- Worth a sweep for other references to deleted modules while in there.

## 4. Config that reads as enabled but is inert

- `USE_N_TICK_CONFIRMATION = True` with `N_TICK_CONFIRMATION = 1`: the factory only wraps when
  `n_ticks > 1`, so n-tick confirmation **never engages** while appearing switched on.
- `USE_ML_ENTRY_MODEL = False` and `ENTRY_ATR_PERIOD/MOVE_MULT = 0/0`: `MLSignalStrategy` and
  `AtrMomentumFilteredSignalStrategy` are built and wired but no-ops.

These are defensible as feature flags, but the n-tick one is actively misleading -- it took
reading the factory to discover the live bot has no tick confirmation at all.

## 5. MTF backtest is needlessly slow

`MultiTimeframeStrongSignalStrategy` recomputes the M15 bias and M5 confirm strategies on
**every one of ~28,800 M1 candles**, though those only change every 15 and 5 minutes. Caching
per higher-timeframe bar should give a 3-5x speedup. Currently an MTF window costs the same
wall-clock time as an STF one while producing 21x fewer trades, which is why MTF research was
abandoned in favour of STF.

## 6. Unvalidated live changes

Two `Config` changes shipped on backtest evidence alone, with no live verification:

- `MAX_SPREAD_POINTS = 15` (PR #66)
- `EXIT_BE_ARMING_TICKS = 30` (PR #68)

The app has not been run since either change. A single smoke run against MT5 -- confirming it
starts, generates signals, and logs `Skipping signal (spread too wide)` when spread exceeds 15
points -- would close this.
