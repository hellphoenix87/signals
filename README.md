# Trading Signal Generation System

A Python trading signal system for MetaTrader 5 (MT5). It fetches candle and tick data, calculates technical indicators (SMA, MACD, RSI), combines them into buy/sell/hold signals, and executes and manages trades through a broker abstraction with tick-driven and candle-close exit strategies. Served via FastAPI with WebSocket support.

## Features

- Fetches live and historical market data from MetaTrader 5 for one or more symbols.
- Calculates technical indicators:
  - **Simple Moving Average (SMA) crossover**
  - **Relative Strength Index (RSI)**
  - **Moving Average Convergence Divergence (MACD)**
- Combines indicator outputs into a strong buy/sell/hold signal per symbol, with optional multi-timeframe confirmation and n-tick confirmation strategies.
- Executes trades through a broker abstraction, with live, demo (simulated), and backtest modes.
- Manages open positions tick-by-tick with configurable profit and loss exit strategies (trailing stop, break-even, stagnation exit, HTF-gated exits).
- Exposes a FastAPI HTTP/WebSocket API to start/stop trading, inspect live signals and ticks, and view/close positions.
- Structured logging for troubleshooting.

## Requirements

- Python 3.12
- A running MetaTrader 5 terminal with an account configured (the app calls `mt5.initialize()` on startup)
- [pipenv](https://pipenv.pypa.io/) for dependency management
- Key libraries (see [Pipfile](Pipfile)): `MetaTrader5`, `pandas`, `ta`, `numpy`, `matplotlib`, `fastapi`, `uvicorn`, `websockets`

## Setup

Install dependencies with pipenv:

```bash
pipenv install
```

## Running

This project uses [`just`](https://github.com/casey/just) to wrap common pipenv commands (see the [justfile](justfile)). Without `just`, run the underlying `pipenv run ...` commands directly.

```bash
just install   # pipenv install
just dev       # start the FastAPI server (uvicorn app.main:app --reload)
just format    # format code with black
just shell     # activate the pipenv shell
```

Once running, the API is available (by default) at `http://127.0.0.1:8000`, with endpoints including:

- `GET /status` — orchestrator/trading status per symbol
- `POST /trading/start` / `POST /trading/stop` — start/stop trading for all configured symbols
- `GET /signal/latest` / `GET /live_signal` — latest generated signal for a symbol
- `GET /tick` — latest tick for a symbol
- `GET /simulated_positions` — open positions when running in demo mode
- `POST /close_all` — close all open trades
- `GET /test_historical` — fetch historical candles

## Configuration

All tunable parameters (symbols, timeframes, risk percentages, exit thresholds, feature flags) live in [`app/config/settings.py`](app/config/settings.py) on the `Config` class.

## Project Structure

```
app/
  config/            # Config class with all tunable parameters
  data/              # Market data fetching, candle collection, tick collection
  signals/
    indicators/      # SMA, MACD, RSI calculations
    strategies/      # Strategies combining indicators into buy/sell/hold signals
  exit_strategies/   # Tick-driven and candle-close position exit management
  trade_execution/   # Broker abstraction and trade placement
  risk/              # Position sizing / risk management
  routes/            # FastAPI endpoints
  services/          # Signal orchestrator wiring data, signals, and execution together
  utils/             # Logging, MT5 connection, backtesting utilities
  factory.py         # Composition root wiring the above together
  main.py            # FastAPI app entrypoint
```

For a deeper architectural walkthrough (how the pieces connect at runtime), see [CLAUDE.md](CLAUDE.md).
