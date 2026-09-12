# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python trading signal system for MetaTrader 5 (MT5). It fetches candle/tick data, computes technical indicators (SMA, MACD, RSI), combines them into buy/sell/hold signals, executes trades through a broker abstraction, and manages open positions with tick-driven and candle-close exit strategies. Served via FastAPI with WebSocket support.

## Commands

Dependency management uses `pipenv`; a `justfile` wraps the common commands (run with `just <task>` if `just` is installed, otherwise call `pipenv run ...` directly):

```bash
just install   # pipenv install
just dev       # pipenv run uvicorn app.main:app --reload
just test      # pipenv run pytest
just format    # pipenv run black .
just shell     # pipenv shell
```

There is no `tests/` directory or test suite currently in the repo (`just test` / `pytest` will find nothing) — `test_mt5.py` at the repo root is a manual MT5 connectivity smoke script, not a pytest test.

Running the app requires a running/configured MetaTrader 5 terminal — `app/main.py` calls `mt5.initialize()` on startup and raises if it fails.

## Architecture

The app is wired together with plain factory functions (no DI framework). `app/factory.py` is the composition root: it builds `MarketData`, `Broker` (in `Mode.LIVE`), `RiskManager`, `TradeExecutor`, and per-symbol `CandleCollector`/`TickCollector`/`SignalOrchestrator` instances (one orchestrator per entry in `Config.SYMBOLS`), then `app/routes/endpoints.py` imports those already-constructed objects directly rather than using FastAPI dependency injection.

**Per-symbol orchestration (`app/services/trade_services.py::SignalOrchestrator`)** runs a hybrid loop:
- A background thread polls for newly *closed* entry-timeframe (`TF_ENTRY`, e.g. M1) candles per symbol. On each new closed candle: generate signals → update HTF bias on `exit_trade` → run candle-close profit exits → execute entries (via `trading_service.process_signal`, falling back to `enter_trade`, falling back to `broker`).
- Every tick (via `tick_collector`): run protective exits (`exit_trade.on_tick`), forward the tick to `signal_generator.on_new_tick` (for n-tick confirmation), and check for a confirmed signal to execute.
- The orchestrator calls generator/executor methods defensively (`getattr`/`callable` checks, multiple candidate method names, `TypeError` fallback signatures) to stay compatible with different strategy/executor implementations — follow this pattern when extending it rather than assuming one fixed interface.

**Signal generation** (`app/signals/signal_generation.py::strategy_factory`): builds a `StrongSignalStrategy` (or other `strategy_cls`) wired with indicator functions (default: MACD only — SMA/RSI are wired but currently commented out), then optionally wraps it:
- `MultiTimeframeStrongSignalStrategy` (`app/signals/strategies/multi_timeframe.py`) if `USE_MULTI_TIMEFRAME_SIGNALS` — gates the base signal using `TF_BIAS`/`TF_CONFIRM`/`TF_ENTRY`.
- `NTickConfirmedSignalStrategy` (`app/signals/strategies/ntick_confirmed_signal_strategy.py`) if `USE_N_TICK_CONFIRMATION` and `N_TICK_CONFIRMATION > 1` — requires the signal to hold across N ticks before confirming.

All strategies inherit from `BaseSignalStrategy` (`app/signals/strategies/base_signal_strategy.py`) and indicators live in `app/signals/indicators/` (`sma_crossover.py`, `macd.py`, `rsi.py`, `entry_filter.py`), returning simple values (floats/strings/dicts) that strategies combine into `"buy"`/`"sell"`/`"hold"`.

**Exit strategies** (`app/exit_strategies/`): `exit_shared.py` defines the `PosState` dataclass (per-position tick-tracking state) and position-field accessors — `pos_entry()`, `pos_side()`, `pos_symbol()`, `pos_ticket()`, `pos_volume()`, `pos_profit()`, `is_break_even()` — which must be used instead of touching raw position attributes, because a "position" may be an MT5 object *or* a dict depending on call site (`get_any()` handles both). `managers/profit.py` (`ProfitExitManager`) and `managers/loss.py` (`LossExitManager`) implement `check_exit_on_tick(position, tick, state)` → exit action dict or `None`; `exit_trade.py` (`ExitTrade`) composes these into `on_tick()` / `on_candle_close()` / `update_bias()` for the orchestrator. Note: `managers/loss copy.py`, `managers/profit copy.py`, and `indicators/sma_crossover copy.py` are stray backup files, not part of the active import graph.

**Trade execution** (`app/trade_execution/`): all MT5 order calls should go through `broker.py` (`Broker`, created via `create_broker(mode)`) — `mode.py` defines `TradingMode.LIVE`/`DEMO`/`BACKTEST` (demo mode simulates positions in `broker.open_positions_sim`). `trade_execution.py` (`TradeExecutor`) orchestrates placing trades using `RiskManager` for sizing; `helpers/prepare_trade.py` builds trade request dicts and provides `enter_trade`.

**Data layer** (`app/data/`): `market_data.py` (historical candles via MT5), `candles.py` (multi-timeframe candle collector used by the orchestrator), `tick_collector.py` (polls live ticks on a background thread/interval and invokes a callback).

**Configuration** (`app/config/settings.py::Config`): every tunable (symbols, timeframes, risk %, pip/price thresholds, exit tick/pip parameters, feature flags like `USE_MULTI_TIMEFRAME_SIGNALS`/`USE_N_TICK_CONFIRMATION`) lives on this single class and is read via `getattr(config, "NAME", default)` throughout — never hardcode thresholds, pip sizes, or symbol lists; add new tunables here.

**API layer** (`app/routes/endpoints.py`): thin FastAPI routes over the objects built in `app/factory.py` (`/status`, `/trading/start`, `/trading/stop`, `/signal/latest`, `/live_signal`, `/tick`, `/simulated_positions`, `/close_all`, `/test_historical`, `/stop_orchestrator`). `app/main.py` is the FastAPI entrypoint (initializes/shuts down MT5 via lifespan); `app/main3.py` is a separate/alternate entrypoint — check which one is current before assuming `app.main:app` is the only target.

## Conventions (from prior Copilot instructions)

- Use Python 3.10+ type hints on function signatures; Google-style docstrings on public classes/methods; PEP 8.
- Keep exit managers' dependencies (`config`, `broker`, callables) injected via `__init__`; avoid global mutable state.
- Use `get_tick_value(tick, key)` to read tick data (supports dict or object ticks); always `float()` prices before storing in state.
- Use `self._pips_to_price(symbol, pips)` for pip↔price conversion; use `self._exit_action(...)` to build exit actions with a descriptive `reason`.
- When adding fields to `PosState`, use `getattr` with a default for backward compatibility.
- Avoid blocking calls inside async FastAPI endpoints.
