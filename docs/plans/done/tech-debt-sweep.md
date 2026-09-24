# Tech Debt Sweep

Status: done

## Goal

Work through `docs/tech-debt.md` (captured 2026-09-24), items 1-4:

1. **Restore a working test suite.** `pipenv run pytest` is at 44 failed / 122 passed and has been
   for at least four merged PRs. There is no safety net today.
2. **Cover `TradeExecutor._spread_ok`**, which went live in PR #66/#70 with zero test coverage.
3. **Fix stale documentation** (`CLAUDE.md` references deleted modules).
4. **Make inert config read as inert** rather than as enabled.

## Out of scope

- **Item 5 (MTF backtest caching).** `MultiTimeframeStrongSignalStrategy` recomputes the M15/M5
  strategies on every M1 candle, and caching per HTF bar would give a 3-5x speedup — but MTF
  research was abandoned in favour of STF, so this optimizes a path nothing currently runs.
  Revisit only if MTF research restarts.
- **Item 6 (live smoke run).** Requires a running MT5 terminal; the main session cannot do it.
  Handed back to the user as a manual step once this plan lands.
- **`docs/plans/in-progress/project-refactor-sweep.md`.** That plan stalled after Subphase 5.3
  (5.4 and 5.5 never ran) and is itself a form of debt, but closing it out is a separate
  decision — several of the tests this plan deletes were produced by it.

## Diagnosis (measured on `c1c7d59`)

`pytest` aborts at *collection* on `tests/e2e/test_orchestrator_enter_trade_dispatch.py`. With that
file ignored: **44 failed, 122 passed**. Grouped by root cause:

| Root cause | Failures | Still-live code? |
|---|---|---|
| `TradingMode.DEMO` no longer exists (demo mode removed) | 16 | No — feature deleted |
| `SignalOrchestrator(broker=/trading_service=)` — old ctor, deleted fallback ladder | 11 | Partly |
| `MultiTimeframeStrongSignalStrategy(base=...)` — signature changed | 4 | Yes |
| `app.trade_execution.helpers.prepare_trade` deleted in PR #42 | whole file | No |
| `app.factory.signal_orchestrator` no longer exported | 2 | Yes (renamed) |
| `test_loss.py` arming-window fixture trips the pre-BE soft SL on tick 1 | 2 | Yes |
| Remaining assertion failures | ~4 | Investigate individually |

**Decision (user, 2026-09-24): delete tests for removed features; rewrite only tests covering code
that still exists.** In particular, the refactor sweep's "pre-fix" archaeology tests
(`TestPreFixEndpointsCannotBeExercisedThisWay`, `TestPreFixProfitExitManagerLacksCandleCloseMethod`,
and similar) pin historical SHAs to prove already-merged fixes. They have served their purpose and
are brittle against every subsequent refactor — delete them.

**Note on MVP/POC mode:** this repo's default workflow says "no tests." This plan is an explicit,
scoped exception — repairing the test suite *is* the deliverable of Phase 1, and Phase 2's whole
point is adding coverage.

## Outcome

All four phases landed. `pipenv run pytest`: **44 failed / 122 passed -> 150 passed, 0 failed.**

**Mid-plan direction change (user, during Phase 1): delete obsolete tests, do not repair unit or
integration tests.** Phases 1.2 and 1.3 as written below were therefore only partly executed:

- The three files already retargeted when that direction landed were kept, since they were green
  and cover live code: `tests/services/test_trade_services.py`,
  `tests/e2e/test_orchestrator_exit_close_failure_logged.py`,
  `tests/e2e/test_orchestrator_generate_signal_direct_call.py`.
- Everything still failing after that point was deleted instead: both DEMO-harnessed exit e2e
  files, the MTF entry test (its `pullback_completed` gate genuinely evaluates `False` now, so it
  asserted behavior the system no longer has), the two `test_loss.py` arming-window tests, two
  `test_profit.py` tests, `TestExitTradeOnCandleCloseWithHTF`, and the two "all ten routes" tests.

Coverage knowingly dropped as a result is recorded as item 7 in `docs/tech-debt.md`: `ExitTrade`'s
cooldown gate, `ProfitExitManager.check_exit_on_candle_close`'s HTF gating, and `LossExitManager`'s
BE arming window now have no tests at all. That is money-moving logic, and rebuilding it against
the current API is the natural follow-up to this plan.

Phases 2, 3 and 4 landed as specified.

## Phases

### Phase 1: Restore a green test suite

#### Subphase 1.1: Delete tests for features that no longer exist

- Change: delete `tests/e2e/test_orchestrator_enter_trade_dispatch.py` (imports the deleted
  `app.trade_execution.helpers.prepare_trade`; the `enter_trade` fallback ladder it covers was
  removed from `SignalOrchestrator`). Delete `tests/e2e/test_broker_simulated_positions.py` and
  the `TradingMode.DEMO` tests in `tests/trade_execution/test_broker.py` (demo mode and
  `broker.open_positions_sim` were removed). Delete every `TestPreFix*` class across `tests/e2e/`.
- Acceptance criteria: `pytest` collects without error; no remaining reference to
  `TradingMode.DEMO`, `prepare_trade`, `open_positions_sim`, or `trading_service` in `tests/`;
  the failure count drops by the corresponding amount with no *new* failures introduced.

#### Subphase 1.2: Re-target tests whose subject still exists but whose API moved

- Change: update the surviving tests in `tests/services/test_trade_services.py`,
  `tests/e2e/test_orchestrator_exit_close_failure_logged.py`,
  `tests/e2e/test_orchestrator_generate_signal_direct_call.py`,
  `tests/e2e/test_endpoints_dependency_injection.py`, `tests/routes/test_endpoints.py`, and
  `tests/e2e/test_factory_singleton_getters.py` to the current APIs: `SignalOrchestrator`'s
  keyword-only `collector`/`signal_generator`/`trade_executor`/`tick_collector`/`exit_trade`/
  `logger` signature, `MultiTimeframeStrongSignalStrategy`'s current ctor, and `app.factory`'s
  current exports (`get_orchestrators`, `get_broker`, `get_market_data`, `get_trade_executor`).
  Where a test's *subject* was deleted alongside the API (e.g. the broker/trading-service fallback
  rungs), delete that test rather than inventing a new target for it.
- Acceptance criteria: all tests in those files pass, or are deleted with the reason recorded in
  the commit message. Where a demo-mode `Broker` was used purely as a harness for still-live exit
  logic (`test_exit_trade_cooldown_gate.py`, `test_exit_trade_candle_close_htf_gating.py`),
  re-harness onto `mock_broker` rather than deleting the coverage.

#### Subphase 1.3: Fix the exit-manager test fixtures

- Change: in `tests/exit_strategies/managers/test_loss.py`, fix the two arming-window tests — the
  fixture's tick (`bid=1.0990` against `price_open=1.1000`, i.e. 10 pips adverse) trips
  `_pre_be_soft_sl_hit` on tick 1, so they return `profit_drop` before ever reaching the arming
  logic under test. Use a tick inside the soft-SL tolerance, and drive the loop off the fixture's
  configured `be_arming_ticks` instead of a hardcoded `20`. Fix the remaining assertion failures in
  `tests/exit_strategies/managers/test_profit.py` and `tests/exit_strategies/test_exit_trade.py`
  the same way — as test bugs unless inspection shows a genuine code defect, in which case stop and
  report it rather than editing the assertion to match.
- Acceptance criteria: `pipenv run pytest` is fully green with no `--ignore` flags, and no test is
  made to pass by weakening an assertion about money-moving behavior.

### Phase 2: Cover `TradeExecutor._spread_ok`

- Change: new tests in `tests/trade_execution/test_trade_execution.py` for
  `TradeExecutor._spread_ok` (`app/trade_execution/trade_execution.py:147`).
- Acceptance criteria: covers all four documented behaviors — (a) `MAX_SPREAD_POINTS = 0` fails
  open and never fetches a tick, (b) a spread above the threshold returns `False`, (c) a spread at
  or below returns `True`, (d) fails open when `market_data.get_symbol_tick` returns `None` or
  `broker.get_point_size` is falsy. Plus one test through `execute()` proving a blocked symbol
  never reaches `place_buy`/`place_sell`. The boundary case (`spread_points == max`) is asserted
  explicitly, since `_spread_ok` uses `<=`.

### Phase 3: Stale documentation sweep

- Change: `CLAUDE.md` — remove the `app/trade_execution/helpers/prepare_trade.py` reference
  ("builds trade request dicts and provides `enter_trade`"); that module was deleted in PR #42 and
  only `__init__.py` remains in the package. Also correct the demo-mode description of
  `app/trade_execution/mode.py` (`TradingMode` is now `LIVE`/`BACKTEST` only — no `DEMO`, no
  `broker.open_positions_sim`), and update `docs/tech-debt.md` item 2's `MAX_SPREAD_POINTS = 15`
  to the current `10` (PR #70).
- Acceptance criteria: every module path named in `CLAUDE.md`'s Architecture section resolves to a
  file that exists, and every class/attribute it names exists on that class. Verified by grep, not
  by eye.

### Phase 4: Make inert config read as inert

- Change: `app/config/settings.py` — `USE_N_TICK_CONFIRMATION = True` alongside
  `N_TICK_CONFIRMATION = 1` reads as enabled but never engages, because
  `app/signals/signal_generation.py::strategy_factory` only wraps when `n_ticks > 1`. Set the flag
  to `False` to match reality, with a comment stating that `N_TICK_CONFIRMATION` must be `> 1` for
  the flag to do anything. Add the same kind of comment to `USE_ML_ENTRY_MODEL` and
  `ENTRY_ATR_PERIOD`/`ENTRY_ATR_MOVE_MULT = 0/0`.
- Acceptance criteria: **no behavior change** — the live bot has no tick confirmation today and
  still has none after this phase; this is a readability fix only. A test asserts
  `strategy_factory` returns an unwrapped strategy under the current config, so the "enabled but
  inert" state can't silently return. Suite stays green.

## Open questions

- Phase 1 deletes a substantial share of `tests/e2e/`. Once the suite is green, the remaining e2e
  coverage should be reviewed for whether it still proves anything about the *current* system —
  possibly a follow-up plan rather than more scope here.
- `docs/plans/in-progress/project-refactor-sweep.md` has two unstarted subphases (5.4, 5.5 — both
  pure test backfill on `ProfitExitManager`'s BE-arming path and `LossExitManager`'s
  recovered-after-unprofitable path). Decide whether to fold them into Phase 1 as genuinely useful
  coverage, or close that plan out as superseded.
