# Remove Demo Mode, Fix Exit Config Wiring, Backtest Signal Generation

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Three independent fixes/additions, bundled into one plan since they're all small:

1. **Remove `TradingMode.DEMO`.** This app runs real trades against a real MT5 demo account (`Mode.LIVE` in `app/factory.py`, which is hardcoded and never changes) — the account being risk-free is a property of *which account MT5 is pointed at*, not something this app's own code needs to simulate separately. `TradingMode.DEMO`'s local position-simulator is already dead code in production (`factory.py` never constructs it), and keeping it means every broker-level fix (see the recent `MAGIC_NUMBER`/`MAX_DEVIATION` config-wiring fix) has to be applied to a mode nothing uses.
2. **Fix the `EXIT_BE_ARMING_TICKS` config-wiring bug**, flagged during the earlier `project-refactor-sweep` plan: `LossExitManager` reads `getattr(self.config, "EXIT_BE_ARMING_TICKS", 20)`, but the `config` object it actually receives in production is `ExitTradeConfig`, which has no such field (or any field by that name) — so the hardcoded fallback of `20` always wins over whatever `Config.EXIT_BE_ARMING_TICKS` (`= 90`) says. Also remove `ProfitExitManager`'s `be_distance_pips`/`be_price` computation — on inspection this is a different, smaller problem: it's not a wiring bug, it's dead code (the computed `be_price` is never read anywhere in the method; break-even arming is decided entirely by `is_break_even()`).
3. **A standalone backtest script for signal generation** (not exits). Exit-strategy backtesting (mark-to-market P&L simulation, `TradingMode.BACKTEST`'s actual position simulation) is deliberately deferred — the exit strategy itself isn't finalized yet, so simulating it against history isn't useful yet. Signal generation, on the other hand, is in place today (even if it needs tuning), so this plan adds a script that replays historical candles through the real, currently-configured signal generator and reports whether each signal would have been favorable against this system's own configured `DEFAULT_SL_PIPS`/`DEFAULT_TP_PIPS` — a simple, exit-strategy-agnostic proxy for signal quality, not a full trade simulation.

## Out of scope (explicit, not forgotten)

- **Full exit-strategy-aware backtesting.** `TradingMode.BACKTEST`'s existing stub (`Broker._backtest_trade`, hardcoded `profit: 0.0`) is left untouched. Once the exit strategy itself is settled, a future plan should implement real mark-to-market P&L simulation and extend (or replace) this plan's signal-only backtest script to simulate full trade lifecycles (entry -> exit management -> realized P&L).
- **Multi-timeframe backtesting.** The new script only replays the single-timeframe path (`StrongSignalStrategy`, what's actually live by default). Backtesting `MultiTimeframeStrongSignalStrategy` needs synchronized M1/M5/M15 historical data and is materially more work — noted here as a future enhancement, not attempted.
- **No tests, per MVP/POC mode.** Verified via direct manual runs against real MT5 historical data (this repo's demo account), same approach as prior plans on this lineage.

## Phase 1: Remove TradingMode.DEMO

- **`app/trade_execution/mode.py`**: remove `DEMO = "demo"` from the enum.
- **`app/trade_execution/broker.py`**: remove `_simulate_trade` entirely; `place_buy`/`place_sell` drop the `elif self.mode == "demo":` branch (now just `if self.mode == "backtest": ... else: ...` for live); `get_open_positions`/`close_position`'s `if self.mode in ("demo", "backtest"):` checks become `if self.mode == "backtest":`. Update the class docstring accordingly.
- **`app/routes/endpoints.py`**: `/simulated_positions`'s demo-mode print-guard (`if getattr(br, "mode", None) == br.mode.DEMO:`) becomes a check against `TradingMode.BACKTEST` instead (the only remaining mode that populates `open_positions_sim`).

## Phase 2: Fix exit-strategy config wiring

- **`app/exit_strategies/exit_trade.py::ExitTradeConfig`**: add `be_arming_ticks: int = int(getattr(Config, "EXIT_BE_ARMING_TICKS", 20) or 20)` as a real dataclass field (it didn't exist before under any name).
- **`app/exit_strategies/managers/loss.py::LossExitManager.check_exit_on_tick`**: read `be_arming_ticks = int(getattr(self.config, "be_arming_ticks", 20))` (the real field, not the nonexistent uppercase name) instead of the current lookup. Add the guard flagged when this bug was originally found: if the configured value is `<= 0`, treat it as "no forced-timeout" (the "window expired without reaching break-even -> force close" branch never triggers; the existing `-$5` drop-exit and normal break-even arming still apply) rather than force-closing every position on tick 1, which is what `0` would do under the current unguarded comparison.
- **`app/exit_strategies/managers/profit.py::ProfitExitManager`**: remove the `be_distance`/`pip_value`/`be_price` computation from both `check_exit_on_tick` and `check_exit_on_candle_close` — dead code, `be_price` is never read after being computed in either copy.

## Phase 3: Backtest script for signal generation

- New `scripts/backtest_signals.py`. Not part of `app/` (matches this repo's existing `scripts/mt5_smoke.py` convention for standalone manual tools).
- Fetches `N` historical M1 candles for a symbol (default `Config.SYMBOLS[0]`, default count large enough for `Config.MIN_CANDLES_FOR_INDICATORS` plus a meaningful backtest window) via `MarketData.get_historical_candles`.
- Builds the real signal generator via `strategy_factory(config=Config)` — whatever's actually configured today (MACD-only single-timeframe, since `USE_MULTI_TIMEFRAME_SIGNALS` defaults `False`); if multi-timeframe is on, the script reports that it only supports the single-timeframe path and exits.
- Replays forward through the candle history: for each bar once `Config.MIN_CANDLES_FOR_INDICATORS` bars of prior history exist, calls `generate_signal(candles[:i+1])` exactly as the live orchestrator would per closed candle, and records every non-hold `final_signal` with its entry price/timestamp/direction.
- For each recorded signal, looks forward up to a configurable number of bars (default a few hundred, comfortably more than `DEFAULT_TP_PIPS`/`DEFAULT_SL_PIPS` would typically take to resolve intraday on M1) and determines whether price moved `Config.DEFAULT_TP_PIPS` in the signal's favor before moving `Config.DEFAULT_SL_PIPS` against it (a "win"), the reverse (a "loss"), or neither within the window ("undecided") — using `Broker.get_pip_size`/a constructed `Broker(TradingMode.BACKTEST)` purely for its pip-size/symbol-info helpers, never placing an order.
- Prints a summary: total signals (buy/sell breakdown), win/loss/undecided counts and rates, average bars-to-resolution. This evaluates signal quality against this system's own real configured SL/TP distances, without needing exit-strategy simulation.

## Verification (manual, per MVP/POC mode)

- Confirm `Broker(TradingMode.LIVE)` still places real orders exactly as before (Phase 1 doesn't touch the live-order path at all, only removes the demo branch).
- Confirm `LossExitManager` genuinely picks up `Config.EXIT_BE_ARMING_TICKS = 90` after the fix (construct a real `ExitTradeConfig`, check `.be_arming_ticks == 90`, not `20`), and that setting it to `0` no longer force-closes a fresh position on its very first tick.
- Run `scripts/backtest_signals.py` against real historical EURUSD data and confirm it produces a sensible-looking summary (nonzero signal count, plausible win/loss split) without placing any real order or touching `open_positions_sim`.
