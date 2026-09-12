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

Tests live under `tests/`, mirroring the `app/` module they cover (e.g. `app/signals/strategies/foo.py` → `tests/signals/strategies/test_foo.py`). Run a single test with `pipenv run pytest tests/path/to/test_foo.py::test_name -v`. Shared fixtures (`mock_mt5`, `mock_broker`, `make_tick`, `make_position`) live in `tests/conftest.py` — use them instead of calling MT5 or a broker for real. `scripts/mt5_smoke.py` is a separate manual MT5 connectivity smoke script, not a pytest test.

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

**Exit strategies** (`app/exit_strategies/`): `exit_shared.py` defines the `PosState` dataclass (per-position tick-tracking state) and position-field accessors — `pos_entry()`, `pos_side()`, `pos_symbol()`, `pos_ticket()`, `pos_volume()`, `pos_profit()`, `is_break_even()` — which must be used instead of touching raw position attributes, because a "position" may be an MT5 object *or* a dict depending on call site (`get_any()` handles both). `managers/profit.py` (`ProfitExitManager`) and `managers/loss.py` (`LossExitManager`) implement `check_exit_on_tick(position, tick, state)` → exit action dict or `None`; `exit_trade.py` (`ExitTrade`) composes these into `on_tick()` / `on_candle_close()` / `update_bias()` for the orchestrator.

**Trade execution** (`app/trade_execution/`): all MT5 order calls should go through `broker.py` (`Broker`, created via `create_broker(mode)`) — `mode.py` defines `TradingMode.LIVE`/`DEMO`/`BACKTEST` (demo mode simulates positions in `broker.open_positions_sim`). `trade_execution.py` (`TradeExecutor`) orchestrates placing trades using `RiskManager` for sizing; `helpers/prepare_trade.py` builds trade request dicts and provides `enter_trade`.

**Data layer** (`app/data/`): `market_data.py` (historical candles via MT5), `candles.py` (multi-timeframe candle collector used by the orchestrator), `tick_collector.py` (polls live ticks on a background thread/interval and invokes a callback).

**Configuration** (`app/config/settings.py::Config`): every tunable (symbols, timeframes, risk %, pip/price thresholds, exit tick/pip parameters, feature flags like `USE_MULTI_TIMEFRAME_SIGNALS`/`USE_N_TICK_CONFIRMATION`) lives on this single class and is read via `getattr(config, "NAME", default)` throughout — never hardcode thresholds, pip sizes, or symbol lists; add new tunables here.

**API layer** (`app/routes/endpoints.py`): thin FastAPI routes over the objects built in `app/factory.py` (`/status`, `/trading/start`, `/trading/stop`, `/signal/latest`, `/live_signal`, `/tick`, `/simulated_positions`, `/close_all`, `/test_historical`, `/stop_orchestrator`). `app/main.py` is the FastAPI entrypoint (initializes/shuts down MT5 via lifespan); `app/main3.py` is a separate/alternate entrypoint — check which one is current before assuming `app.main:app` is the only target.

## Development workflow: spec/TDD multi-agent flow

Non-trivial work goes through a plan-driven, test-first flow using four project agents (`.claude/agents/`) and three skills (`.claude/skills/`). **The main session is the sole orchestrator** — it invokes agents via the Agent tool and moves plan files between folders; agents never call each other directly.

| Agent | Model | Job |
|---|---|---|
| `architect` | sonnet (opus when triage says elevated) | Turns a request into a phased plan under `docs/plans/`, using the `plan` skill. Never writes application code. |
| `developer` | haiku | Implements exactly one subphase at a time, test-first, using the `tdd-subphase` skill. |
| `qa` | sonnet | Verifies a finished subphase/phase against the plan's acceptance criteria using the `qa-verify` skill; only edits the plan's QA section. |
| `pr-reviewer` | sonnet (opus when triage says elevated) | Reviews one phase/subphase branch's diff before it merges, via the built-in `code-review` skill. |

**Plan lifecycle** — one markdown file per feature/bugfix, physically moved as its status changes:

```
docs/plans/todo/<slug>.md        # architect has written it, work hasn't started
docs/plans/in-progress/<slug>.md # developer/qa are actively working it
docs/plans/done/<slug>.md        # pr-reviewer approved it, merged
```

**Triage → model gating**: the architect records `Triage: low | elevated` at the top of every plan, weighing complexity and blast radius together — elevated when the change touches money-moving logic (trade execution, exit strategies, risk/position sizing), spans multiple subsystems at once, changes the composition root (`app/factory.py`) or the orchestrator's control flow, or is explicitly flagged high-risk. The main session reads this line and passes `model: opus` on the Agent tool call for `architect`/`pr-reviewer` work on that plan when elevated; otherwise both run on their sonnet default. `developer` always runs on haiku regardless of triage — which is why every subphase the architect writes must be small and unambiguous enough for a weaker model to execute without needing judgment calls.

**Nothing lands on `master` outside a branch+PR — including the plan file itself.** `master` has GitHub branch protection enabled (pull requests required, force-pushes and deletions blocked, enforced even for admins) — a direct `git push origin master` is rejected by the remote, so this isn't just a convention to follow, it's not physically possible to bypass. Before invoking `architect`, the main session creates a `<plan-slug>-plan` branch off latest `master`; the architect writes `docs/plans/todo/<slug>.md` there, and the main session pushes/PRs/merges it (auto-merge — it's a doc-only change) before returning to `master` and starting Phase 1. Never let the architect write directly onto whatever branch happens to be checked out.

### Plan execution: branching and merge automation

When the user asks to **implement** a plan (as opposed to just design one), the main session drives the whole plan to completion — every phase and subphase, in order — without pausing between steps for confirmation, except where the architect left an open question or an agent reports a genuine blocker. This is a standing, explicit exception to the normal "confirm before pushing/merging" rule, scoped *only* to the phase/subphase branches produced by this flow — it is not blanket authorization for unrelated git operations.

**One branch per phase/subphase**, named `<plan-slug>-<phase>` or `<plan-slug>-<phase>.<subphase>` (matching the plan file's own `Phase N` / `Subphase N.M` numbering) — e.g. plan `ntick-macd-confirmation` → branches `ntick-macd-confirmation-1.1`, `ntick-macd-confirmation-1.2`, `ntick-macd-confirmation-2`. Only one such branch is ever in flight at a time; the next phase/subphase does not start until the current one is merged.

For each phase/subphase, in order:

1. `git checkout master && git pull` — always branch from the latest merged state (this repo's trunk is `master`, not `main`).
2. Create `<plan-slug>-<phase[.subphase]>` off master.
3. First subphase of the plan: move the plan file `docs/plans/todo/<slug>.md` → `docs/plans/in-progress/<slug>.md` as part of this branch's commit.
4. Invoke `developer` (haiku) to implement it test-first (`tdd-subphase` skill).
5. Invoke `qa` (sonnet) to verify against the plan's acceptance criteria (`qa-verify` skill); loop back to `developer` on the same branch until it passes.
6. Invoke `pr-reviewer` (sonnet, or opus per the plan's triage) against the branch diff; loop back to `developer` on the same branch until there are no blocking findings.
7. Push the branch, open a PR against master, and **merge it automatically** — no user confirmation needed for this merge specifically.
8. Last subphase of the plan: move the plan file `docs/plans/in-progress/<slug>.md` → `docs/plans/done/<slug>.md` as part of this final branch's commit, before opening its PR.
9. `git checkout master && git pull` to pick up the merge, then proceed to the next phase/subphase's branch.

## Conventions (from prior Copilot instructions)

- Use Python 3.10+ type hints on function signatures; Google-style docstrings on public classes/methods; PEP 8.
- Keep exit managers' dependencies (`config`, `broker`, callables) injected via `__init__`; avoid global mutable state.
- Use `get_tick_value(tick, key)` to read tick data (supports dict or object ticks); always `float()` prices before storing in state.
- Use `self._pips_to_price(symbol, pips)` for pip↔price conversion; use `self._exit_action(...)` to build exit actions with a descriptive `reason`.
- When adding fields to `PosState`, use `getattr` with a default for backward compatibility.
- Avoid blocking calls inside async FastAPI endpoints.
