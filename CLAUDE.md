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

Tests live under `tests/`, mirroring the `app/` module they cover (e.g. `app/signals/strategies/foo.py` → `tests/signals/strategies/test_foo.py`). `tests/e2e/` is the exception — it holds full-stack tests that drive the real, wired-together app (real FastAPI app, real `SignalOrchestrator`/`ExitTrade`/`Broker`, only MT5 itself mocked at the boundary) rather than mirroring one `app/` module; per the spec/TDD workflow below, it's owned and written by the `qa` agent, not the `developer` agent. Run a single test with `pipenv run pytest tests/path/to/test_foo.py::test_name -v`. Shared fixtures (`mock_mt5`, `mock_broker`, `make_tick`, `make_position`) live in `tests/conftest.py` — use them instead of calling MT5 or a broker for real. `scripts/mt5_smoke.py` is a separate manual MT5 connectivity smoke script, not a pytest test.

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

All strategies inherit from `BaseSignalStrategy` (`app/signals/strategies/base_signal_strategy.py`) and indicators live in `app/signals/indicators/` (`sma_crossover.py`, `macd.py`, `rsi.py`), returning simple values (floats/strings/dicts) that strategies combine into `"buy"`/`"sell"`/`"hold"`.

**Exit strategies** (`app/exit_strategies/`): `exit_shared.py` defines the `PosState` dataclass (per-position tick-tracking state) and position-field accessors — `pos_entry()`, `pos_side()`, `pos_symbol()`, `pos_ticket()`, `pos_volume()`, `pos_profit()`, `is_break_even()` — which must be used instead of touching raw position attributes, because a "position" may be an MT5 object *or* a dict depending on call site (`get_any()` handles both). `managers/profit.py` (`ProfitExitManager`) and `managers/loss.py` (`LossExitManager`) implement `check_exit_on_tick(position, tick, state)` → exit action dict or `None`; `exit_trade.py` (`ExitTrade`) composes these into `on_tick()` / `on_candle_close()` / `update_bias()` for the orchestrator.

**Trade execution** (`app/trade_execution/`): all MT5 order calls should go through `broker.py` (`Broker`, created via `create_broker(mode)`) — `mode.py` defines `TradingMode.LIVE`/`DEMO`/`BACKTEST` (demo mode simulates positions in `broker.open_positions_sim`). `trade_execution.py` (`TradeExecutor`) orchestrates placing trades using `RiskManager` for sizing; `helpers/prepare_trade.py` builds trade request dicts and provides `enter_trade`.

**Data layer** (`app/data/`): `market_data.py` (historical candles via MT5), `candles.py` (multi-timeframe candle collector used by the orchestrator), `tick_collector.py` (polls live ticks on a background thread/interval and invokes a callback).

**Configuration** (`app/config/settings.py::Config`): every tunable (symbols, timeframes, risk %, pip/price thresholds, exit tick/pip parameters, feature flags like `USE_MULTI_TIMEFRAME_SIGNALS`/`USE_N_TICK_CONFIRMATION`) lives on this single class and is read via `getattr(config, "NAME", default)` throughout — never hardcode thresholds, pip sizes, or symbol lists; add new tunables here.

**API layer** (`app/routes/endpoints.py`): thin FastAPI routes over the objects built in `app/factory.py` (`/status`, `/trading/start`, `/trading/stop`, `/signal/latest`, `/live_signal`, `/tick`, `/simulated_positions`, `/close_all`, `/test_historical`, `/stop_orchestrator`). `app/main.py` is the FastAPI entrypoint (initializes/shuts down MT5 via lifespan); `app/main3.py` was removed in this sweep and `app.main:app` is the sole entrypoint.

## Development workflow: spec/TDD multi-agent flow

**Opt-in only.** MVP/POC mode (below) is the default workflow for all work in this repo. Use this spec/TDD flow instead only when the user explicitly asks for it (e.g. "use the full spec/TDD flow for this," "I want tests and a pr-reviewer pass on this one").

Non-trivial work goes through a plan-driven, test-first flow using four project agents (`.claude/agents/`) and three skills (`.claude/skills/`). **The main session is the sole orchestrator** — it invokes agents via the Agent tool and moves plan files between folders; agents never call each other directly.

| Agent | Model | Job |
|---|---|---|
| `architect` | sonnet (opus when triage says elevated) | Turns a request into a phased plan under `docs/plans/`, using the `plan` skill. Never writes application code. |
| `developer` | haiku | Implements exactly one subphase at a time, test-first (unit/integration tests only), using the `tdd-subphase` skill. Done when its own unit/integration tests are green — nothing more. |
| `qa` | sonnet | Owns the quality gate, in two passes. **Author** (runs in parallel with `developer`, starts as soon as the subphase exists): writes/extends `tests/e2e/` straight from the plan's stated requirements/acceptance criteria alone — never reads `developer`'s in-progress diff. **Verify** (starts only once `developer` reports its unit/integration tests green): runs the full suite (its own e2e tests plus `developer`'s unit/integration tests) against the real implementation and checks both against the plan's acceptance criteria. Edits `tests/e2e/**` and the plan's QA section; never pushes, opens a PR, or merges. |
| `pr-reviewer` | sonnet (opus when triage says elevated) | Reviews one phase/subphase branch's diff before it merges, via the built-in `code-review` skill. Starts only after `qa`'s verify pass signs off. Never pushes, opens a PR, or merges. |

**Only the main session pushes, opens PRs, or merges branches — no exceptions.** None of the four agents above ever runs `git push`, `gh pr create`, or `gh pr merge`; that is the main session's job alone, everywhere in this workflow (plan-branch setup, every phase/subphase branch, and anywhere else). This is a hard governance rule, not just a convention — see the permission hardening below.

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

**`developer`, `qa`, and `pr-reviewer` are each spawned once per subphase, and resumed — not respawned — for any loop-back within that same subphase.** If `qa`'s verify pass or `pr-reviewer` rejects with a finding, the main session sends that finding to the *existing* `developer` agent (via `SendMessage`, same as `qa`'s own author→verify resumption) rather than spawning a fresh one — `developer` already has full context of what it just wrote and why, so it can apply a targeted fix directly instead of re-deriving the subphase from scratch. `qa` and `pr-reviewer` are resumed the same way for their own re-check after a fix, rather than being freshly re-invoked. None of the three carry over into the *next* subphase, though — that always gets brand-new instances of all three, since a reused agent's context only grows with no way to selectively clear it, and a resumed agent depends on the main session holding a live reference to it; that dependency is fine across one subphase's short fix-loop, but not worth stretching across an entire phase or plan.

**Every spawn below runs in the foreground (`run_in_background: false`).** This entire flow is strictly sequential — only one branch is ever in flight, and the main session has nothing else useful to do while any agent runs — which is exactly the condition under which the `Agent` tool's own guidance calls for foreground rather than its background default. Where two agents genuinely need to run at once (`developer` + `qa`'s author pass, step 4), that comes from issuing both `Agent` calls in the *same message*, not from backgrounding either of them — the harness dispatches every tool call in one message together and waits for all of them, so foreground calls in one message still run concurrently.

**Before invoking any of the three, extract just this subphase's `Change`/`Acceptance criteria` text from the plan file and paste it directly into that agent's prompt** — don't tell the agent to go open the plan file itself to find its own subphase. The plan file's accumulated QA history is tens of thousands of tokens by the middle of a multi-phase plan and only grows every subphase; a fresh spawn re-reading the whole file just to find its ~15-line section is pure waste. The agent still reads actual code files as needed — that's real work — it just doesn't need the plan file for its base instructions.

**Each of the three reports back a minimal status, not a narrative.** Happy path: `developer` says "Done" (its own unit/integration tests are green); `qa`'s author pass says "Done" (e2e test written), its verify pass says "Done" (ready for `pr-reviewer`, or ready for `docs/plans/done/` on the plan's last subphase); `pr-reviewer` says "Mergeable." Unhappy path: whichever one can't proceed says why — `developer`: "Blocked: `<specific reason>`" (missing dependency, contradicts existing code); `qa`: "Rejected: `<specific gap(s)>`"; `pr-reviewer`: "Rejected: `<specific blocking finding(s)>`." Nothing beyond that (no "here's what I changed," no test file paths, no restated acceptance criteria) is needed, because nothing downstream trusts a self-report anyway: `qa` re-derives everything from the plan's requirements and its own test run, `pr-reviewer` re-derives everything from the actual diff, and both are already required to independently catch plan-vs-implementation divergence rather than rely on `developer` disclosing it. The one thing a bare "Done" can't carry is the reason behind a rejection — the main session needs that reason verbatim to hand to whichever agent gets resumed to fix it. (`qa`'s verify pass still appends its full QA-section entry to the plan file regardless — that's the durable audit trail, a separate thing from the terse status it reports to the main session.)

For each phase/subphase, in order:

1. `git checkout master && git pull` — always branch from the latest merged state (this repo's trunk is `master`, not `main`).
2. Create `<plan-slug>-<phase[.subphase]>` off master.
3. First subphase of the plan: move the plan file `docs/plans/todo/<slug>.md` → `docs/plans/in-progress/<slug>.md` as part of this branch's commit.
4. Invoke `developer` and `qa` **at the same time**, in a single message with both Agent tool calls (a `developer` call followed sequentially by a `qa` call, in two separate turns, would run them one after another instead of concurrently) — as two independent agents sharing the same checkout:
   - `developer` (haiku, `tdd-subphase` skill) implements the subphase test-first — unit/integration tests only — and iterates on its own until those tests are green. That's `developer` done; it does not wait for or coordinate with `qa`.
   - `qa` (sonnet, `qa-verify` skill's author step) writes/extends `tests/e2e/` for this subphase using only the plan's stated requirements/acceptance criteria (handed to it directly per the note above) — it must not read `developer`'s in-progress diff while authoring, so the e2e test stays a black-box check against the spec rather than a mirror of whatever `developer` happened to implement.
   - Sharing one checkout is safe here without worktree isolation because of two things, both enforced in each agent's own instructions: their file sets never overlap (`developer` writes `app/` + `tests/` outside `tests/e2e/`; `qa`'s author pass writes only `tests/e2e/`), and **neither agent runs `git add`/`git commit`/`git push`/`git checkout -b` itself** — they only edit files in the working tree. The main session is the one that stages and commits everything once both are done, which is also what removes any risk of two agents racing on the same `.git` index.
5. Wait for **both** `developer` ("Done") and `qa`'s author pass ("Done") to finish — not just `developer` — then stage/commit the combined result. Resume the same `qa` agent for its **verify** pass: run the full suite — its `tests/e2e/` plus `developer`'s unit/integration tests — against the actual implementation, and check both against the plan's acceptance criteria. If it comes back "Rejected: `<gap(s)>`", resume the existing `developer` agent (not a fresh spawn) with that exact finding; once `developer` reports "Done" again, resume `qa` (not a fresh spawn) to re-run its verify pass. Repeat until `qa` says "Done." During this pass `qa` may use read-only/diagnostic git commands (`diff`, `log`, `show`, or a paired `stash`/`stash pop` to compare before/after behavior) to verify a fix — that's inspection, not committing the subphase's change, and remains fine.
6. Only once `qa`'s verify pass says "Done," invoke `pr-reviewer` (sonnet, or opus per the plan's triage) against the branch diff. If it comes back "Rejected: `<finding(s)>`", resume the existing `developer` agent (not a fresh spawn) with that exact finding; once `developer` reports "Done" again, resume `pr-reviewer` (not a fresh spawn) to re-check the finding was actually addressed, not just take developer's word for it. Repeat until `pr-reviewer` says "Mergeable."
7. Push the branch, open a PR against master, and **merge it automatically** — no user confirmation needed for this merge specifically. This step is always performed by the main session; none of the four agents does it themselves.
8. Last subphase of the plan: move the plan file `docs/plans/in-progress/<slug>.md` → `docs/plans/done/<slug>.md` as part of this final branch's commit, before opening its PR.
9. **Delete the branch, both remote and local**, right after it merges (e.g. `gh pr merge --squash --delete-branch`, or a separate `git push origin --delete <branch>` plus `git branch -d <branch>`) — don't let merged phase/subphase branches accumulate on `origin`.
10. `git checkout master && git pull` to pick up the merge, then proceed to the next phase/subphase's branch.

## Development workflow: MVP/POC mode

**This is the default workflow for all work in this repo, standing instruction from the user.** Use the spec/TDD multi-agent flow above only if the user explicitly asks for it instead.

**The main session does everything itself — no `architect`, `developer`, `qa`, or `pr-reviewer` subagents.** The main session thinks through the solution, writes the plan, and implements every phase/subphase directly, without spawning agents or delegating any part of the work.

**The plan file still goes through the same lifecycle** (`docs/plans/todo/<slug>.md` → `docs/plans/in-progress/<slug>.md` → `docs/plans/done/<slug>.md`), written by the main session itself instead of the `architect` agent, still using the `plan` skill's phase/subphase structure. A `Triage` line isn't needed here — there's no `architect`/`pr-reviewer` model gating to drive with it.

**One branch for the whole plan, not one per phase/subphase.** Create `<plan-slug>` off latest `master` before starting, and do all of that plan's work — the plan file itself and every phase/subphase's implementation — on that single branch. `git checkout master && git pull` first, same as the spec/TDD flow, since `master` is still the trunk and still has branch protection (PRs required — a direct push is rejected by the remote regardless of workflow).

**No tests.** The main session does not write or run tests in this mode — the user tests manually. This is an explicit, scoped exception to this repo's normal test-first convention; it doesn't apply outside MVP/POC-mode work.

**Commit and push after each phase/subphase**, onto that same branch, so progress is saved and visible incrementally — but don't open a PR yet.

**Only one PR, opened once the whole plan is complete** — move the plan file to `docs/plans/done/<slug>.md` as part of the final commit, push, and open the PR against `master`. **The main session never merges it.** Merge authority in this mode belongs to the user alone — open the PR and stop; wait for the user to merge (or ask for changes) rather than auto-merging the way the spec/TDD flow's docs-only plan-branch PR does.

## Conventions (from prior Copilot instructions)

- Use Python 3.10+ type hints on function signatures; Google-style docstrings on public classes/methods; PEP 8.
- Keep exit managers' dependencies (`config`, `broker`, callables) injected via `__init__`; avoid global mutable state.
- Use `get_tick_value(tick, key)` to read tick data (supports dict or object ticks); always `float()` prices before storing in state.
- Use `self._pips_to_price(symbol, pips)` for pip↔price conversion; use `self._exit_action(...)` to build exit actions with a descriptive `reason`.
- When adding fields to `PosState`, use `getattr` with a default for backward compatibility.
- Avoid blocking calls inside async FastAPI endpoints.
