# Entry Config Wiring, DI Cleanup, and Streamlining

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

The trade-entry pipeline was audited and found to have several config values that are declared but never actually reach the code path they're meant to control, one dependency (`RiskManager`) that's wired in but never successfully invoked due to a signature mismatch, a dead alternate entry path (`EnterTrade`) that can never run, and a `SignalOrchestrator` full of defensive `getattr`/`callable`/`TypeError`-fallback dispatch against collaborators whose real, concrete implementations are fully known and fixed. This plan:

1. Makes every relevant `Config` value actually control the behavior it's meant to (no more hardcoded literals duplicating or overriding config).
2. Fixes `TradeExecutor`'s entry pipeline so position sizing genuinely goes through `RiskManager`, and every entry gets a live price plus config-driven SL/TP.
3. Replaces the orchestrator's defensive multi-path dispatch with direct, explicit dependency injection against each collaborator's one real interface, and removes the dead `EnterTrade` path entirely.
4. Fixes three API endpoints (`/signal/latest`, `/live_signal`, `/tick`) that call `SignalOrchestrator` methods which don't exist, and the `/status` endpoint's `is_running()` call, which also doesn't exist.
5. Applies this repo's "docstrings only, no comments" convention to every file this plan touches.

Runs against a demo MT5 account (the app's own `Mode.LIVE`, which places real orders — but against a demo account, not real money), so real-money risk is not the concern; correctness and maintainability are.

## Out of scope (deliberately deferred, not forgotten)

- **`Config.DAILY_TARGET_PROFIT` / `Config.DAILY_MAX_RISK_PERCENT`** — implementing a real daily loss/profit cap needs actual realized-P&L tracking on every exit (querying MT5 deal history, or computing it from close price vs. entry), which doesn't exist anywhere in this codebase today. Wiring these two config values against `TradeExecutor.daily_profit`/`RiskManager.daily_risk_used` without that tracking would just be a gate that never trips — worse than no gate, since it would look like a safety feature that silently isn't one. This needs its own plan once P&L tracking exists.
- **`Config.ENTRY_ATR_PERIOD` / `ENTRY_ATR_MOVE_MULT` / `MAX_SPREAD_POINTS` / `USE_CLOSED_CANDLES_ONLY` / `DROP_LAST_CANDLE_ALWAYS`** — these describe an ATR-based volatility gate and a spread filter that were never built. Wiring config for a feature that doesn't exist isn't a "fix"; building the feature is new scope. Deferred.
- **A full repo-wide docstring-only comment sweep.** `app/` currently has roughly 400 lines that are `#` comments (standalone or inline), spread across every module, most unrelated to this plan's actual work. Converting all of them is a large, separate mechanical task. This plan applies the docstring-only rule only to files it otherwise touches for a functional reason.
- **The existing test suite.** Per this plan's MVP/POC mode, no tests are written or run here. The constructor/signature changes in this plan (especially `SignalOrchestrator`) will likely break a meaningful number of existing unit/e2e tests. That's expected and left for manual verification/test updates later, not handled in this plan.

## Phase 1: Config-driven MT5 order parameters and risk sizing

- **`app/trade_execution/broker.py`**: `_mt5_place_order` hardcodes `"deviation": 5, "magic": 123456`; `close_position`'s live-mode branch hardcodes the same two values again. Replace both with `int(getattr(Config, "MAX_DEVIATION", 5))` / `int(getattr(Config, "MAGIC_NUMBER", 123456))`, matching the pattern `TradeExecutor.execute_exit` already uses correctly.
- **`app/risk/risk_manager.py`**: `calculate_lot_size` hardcodes a local `MIN_SL_PIPS = 5`, shadowing `Config.MIN_SL_PIPS` (which already exists and is unused here). Import `Config` and read `float(getattr(Config, "MIN_SL_PIPS", 5.0))` instead. Also remove `reset_daily_risk()` and the `self.daily_risk_used` attribute — dead code with no caller anywhere in the codebase (tracking daily risk usage is part of the deferred daily-cap feature above, and keeping an inert half-implementation around isn't useful scaffolding, just dead weight).

## Phase 2: Config-driven signal generation

- **`app/signals/signal_generation.py::strategy_factory`**: currently constructs `StrongSignalStrategy(indicators=..., logger=..., min_candles=min_candles, config=config)` — never passing `confidence_threshold`, so it silently stays at the class's own hardcoded default (`0.5`) regardless of `Config.CONFIDENCE_THRESHOLD`. Add `confidence_threshold=float(getattr(config, "CONFIDENCE_THRESHOLD", 0.5))` to that call.
- Same function: `min_candles` is only used if the caller explicitly passes it; `app/factory.py` never does, so `StrongSignalStrategy.min_candles` stays at its own internal default of `1`, even though `Config.MIN_CANDLES_FOR_INDICATORS = 202` already exists and is already used to size how much history the candle collector fetches. Default `min_candles` to `int(getattr(config, "MIN_CANDLES_FOR_INDICATORS", 1))` when the caller doesn't override it, so the strategy's own data-sufficiency gate actually matches what the collector provides instead of silently floating at `1`.

## Phase 3: Fix TradeExecutor's entry pipeline

`app/trade_execution/trade_execution.py::TradeExecutor` already receives `market_data` in its constructor but never uses it for entries. Replace the current flow (which computes `lot` before `price` even exists, guesses at `risk_manager` method names that don't match `RiskManager`'s real signature, and silently reduces to a fixed `Config.LOT_SIZE` or worse — a bare `1` full lot — every time) with:

1. Determine `direction` (unchanged).
2. Fetch a live tick via `self.market_data.get_symbol_tick(symbol)` (already exists, already injected) and take `tick.ask` for BUY / `tick.bid` for SELL as `price` — a signal-supplied `price`/`open_price` still wins if present (useful for backtest-style callers), but a live price is now always available as the default instead of silently staying `None`.
3. `sl_pips`/`tp_pips` from the signal if present, else `Config.DEFAULT_SL_PIPS`/`Config.DEFAULT_TP_PIPS` (already declared, currently only reached when a signal happens to carry a price, which none of the real strategies ever do).
4. `lot`: a signal-supplied `lot` still wins if present; otherwise call `self.risk_manager.calculate_lot_size(balance, sl_pips, symbol_price=price, symbol=symbol, risk_percent=Config.LOT_RISK_PERCENT)` directly — `balance` from `self.market_data.get_account_info().balance` (already exists, already injected; `0.0` if the account info call fails, which correctly aborts the trade via `RiskManager`'s own `lot <= 0` clamp path). Remove the old `_extract_lot` method's risk-manager-method-name-guessing loop and its dead final fallback (`return 1`, a full lot) entirely — they're replaced by this one direct, correct call.
5. `sl, tp = self.broker.calculate_sl_tp_prices(direction, price, sl_pips, tp_pips, symbol, units="pips")` — now always reached, since `price` is always available.
6. Place the order as before.

Net effect: every entry now gets real risk-percentage-based sizing and a real SL/TP attached at the broker level, both genuinely driven by `Config`, instead of a fixed lot and no stops.

## Phase 4: Proper DI in SignalOrchestrator; remove EnterTrade

`app/services/trade_services.py::SignalOrchestrator` currently accepts `trading_service`, `enter_trade`, and `broker` as three optional, defensively-dispatched alternate paths for both entries and exits, plus `getattr`/`callable`/`TypeError`-fallback dispatch against `tick_collector` and `collector`. In the real, wired-up app (`app/factory.py`), `trading_service` (a `TradeExecutor`) is always provided and always wins — `enter_trade` and the broker-direct fallback are unreachable dead code, and `EnterTrade` itself (`app/trade_execution/helpers/prepare_trade.py`) is never actually invoked in production. Fixing Phase 3 does not make `EnterTrade` any more reachable or useful, since `TradeExecutor` is the one real entry-execution service now that its own pipeline is correct.

- **Delete `app/trade_execution/helpers/prepare_trade.py`** (the `EnterTrade` class and `create_enter_trade` provider) — fully dead code once `TradeExecutor`'s own pipeline is fixed.
- **`app/factory.py`**: remove the `create_enter_trade` import and the `enter_trade` construction/wiring; stop passing `enter_trade`/`broker` into `create_orchestrator`.
- **`SignalOrchestrator.__init__`**: rename `trading_service` → `trade_executor` (that's what it concretely is) and make it a required parameter (not `Optional`). Drop `enter_trade` and `broker` as constructor parameters entirely — nothing in the class needs them once the fallback paths below are removed.
- **`_on_tick`**: replace the `trading_service` → `enter_trade` → `broker.place_market_order` three-way dispatch with a single direct call: `self.trade_executor.process_signal([sig], None)`.
- **`_run_entries`**: replace the `trading_service` call plus the entire `enter_trade`/`broker.place_*` fallback block (roughly lines 384-436 today) with a single direct call: `self.trade_executor.process_signal(signals, snapshot)`. The `pending_entries`/pullback-completed stash logic stays (it's real, reachable behavior, not fallback dispatch).
- **`_execute_exit_actions`**: replace the `trading_service.execute_exit` + broker-direct-close fallback with a single direct loop calling `self.trade_executor.execute_exit(a)` per action, keeping the existing per-action try/except logging.
- **`_wire_tick_callback`**: `TickCollector.start(cb=None)` already accepts the callback directly and always succeeds: replace the `hasattr`/`callable`/`TypeError`-fallback dance with `self.tick_collector.start(self._on_tick)` (guarded only by `if self.tick_collector:`, since it's the one real collaborator's one real method).
- **`start()`/`stop()`**: `_safe_call(self.collector, "start"/"stop")` exists only to guess whether `collector` supports these — both concrete collector classes (`LiveCandleCollector`, `MultiTimeframeCandleCollector`) always do. Call `self.collector.start()`/`self.collector.stop()` directly.
- **`_get_latest_candles`**: both concrete collector classes expose `get_latest_candles()` with no `symbol=` parameter (that guess always raises `TypeError` and falls through today) — replace with a direct `self.collector.get_latest_candles()` call.
- **`_symbols_to_process`/`_run`**: every real collector is single-symbol (`factory.py` builds one `SignalOrchestrator` per entry in `Config.SYMBOLS`, each with its own single-symbol collector), so the `Config.SYMBOLS`/`MAX_SYMBOLS` fallback branch in `_symbols_to_process` is unreachable dead code, and the `for symbol in symbols:` loop in `_run` always iterates exactly once. Collapse both: `_run` operates directly on `self.collector.symbol`, and `_symbols_to_process` is removed.
- **Add three small methods `SignalOrchestrator` is missing but that live API endpoints already call**, found while tracing this wiring: `is_running()` (returns `self._running`; fixes `/status`, which currently calls a method that doesn't exist), `get_latest_signal(symbol: str | None = None)` (returns the most recently generated signal, tracked via a new `self._last_signal` set at the end of `_run_entries`; fixes `/signal/latest` and `/live_signal`), and `get_tick()` (returns the most recent tick, tracked via a new `self._last_tick` set at the start of `_on_tick`; fixes `/tick`). All three endpoints currently 500 today — this isn't new scope, it's the same DI-correctness fix applied to the orchestrator's own public surface instead of just its collaborators.
- **`app/signals/strategies/multi_timeframe.py`**: remove the `s.get("reason") == "waiting_for_closed_candle"` branch — no strategy in this codebase ever sets that reason string (confirmed via repo-wide search), so it's unreachable.

## Phase 5: Docstrings only

Across every file touched in Phases 1-4 (and `app/config/settings.py`, touched here specifically for this): remove standalone `#`/inline comments and multi-line triple-quoted non-docstring blocks, replacing with a docstring wherever the explanation is still needed (module, class, or function/method docstring, whichever fits what the comment was actually explaining). `app/config/settings.py` currently uses `# === Section ===` comments purely as visual dividers between setting groups with no class docstring at all — replace those with one `Config` class docstring that lists the groups, and let blank lines do the visual separation the `#` headers were doing.

## Open questions

None — the two deferred items above (daily risk cap, ATR/spread gate) are deliberately out of scope, not blocking.
