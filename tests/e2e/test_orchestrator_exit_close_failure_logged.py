"""E2E coverage for Subphase 3.3 (project-refactor-sweep):

`SignalOrchestrator._execute_exit_actions` (app/services/trade_services.py)
tries several candidate close methods on the broker (`close_position` /
`close_trade` / `close_order`) to close a position for a given exit action.
Pre-fix, two `except Exception: pass` blocks around the actual `closer(...)`
call silently discard whatever error the broker's close call raises -- so a
real broker failure while trying to close a losing/exiting position produces
no log, no alert, nothing; it just silently fails.

The fix replaces both bare `except Exception: pass` blocks with
`self._log_exception(f"[Orchestrator] broker close failed for ticket={ticket}
symbol={symbol}: {exc!r}")`, reusing the `_log_exception` helper already on
`SignalOrchestrator`.

This drives a real `SignalOrchestrator`, wired to a real `ExitTrade` (with its
real `LossExitManager` -- app/exit_strategies/exit_trade.py,
app/exit_strategies/managers/loss.py) and a real `Broker`
(`create_broker(TradingMode.LIVE)`, app/trade_execution/broker.py) -- nothing
about the exit-decision or close-attempt logic is faked. Only the MT5 boundary
itself is mocked: the `mock_mt5` fixture from tests/conftest.py, plus a local
`mt5.positions_get` override that raises specifically for the
`positions_get(ticket=...)` call `Broker.close_position` makes in LIVE mode
(simulating a genuine MT5-level close failure -- e.g. a stale connection or an
already-closed/unknown ticket) while still answering the *unfiltered*
`positions_get()` call `Broker.get_open_positions()` (via `ExitTrade.on_tick`)
makes to discover the still-open, losing position in the first place.

A position with `profit=-5.0` triggers `LossExitManager`'s real "profit_drop"
protective exit on the very first tick (no arming/pending state needed) -- a
genuine exit decision, not a hand-crafted exit action -- which then flows into
`SignalOrchestrator._execute_exit_actions`, which calls the real
`Broker.close_position(ticket=..., ...)`, which raises for the simulated MT5
reason above.

Authoring-time verification of the regression this closes: the author of this
test extracted the git-HEAD-committed (pre-fix) `app/services/trade_services.py`
via `git show HEAD:app/services/trade_services.py` into an isolated module
(loaded under a private module name -- never touching the real working-tree
file, which `developer` may be concurrently editing) and reran this exact
scenario against it. Result: the real `Broker.close_position` genuinely raised,
and 0 log records were captured -- confirming the failure was genuinely
invisible before this subphase's fix. A copy with the described fix manually
applied then produced exactly 1 log record reading
"[Orchestrator] broker close failed for ticket=999 symbol=EURUSD:
RuntimeError('simulated MT5 failure: positions_get(ticket=...) failed')" at
ERROR level with exception info attached -- matching what this test asserts
against the real (developer-implemented) module below.

`TestOrchestratorLogsTradingServiceExitFailures` (added after pr-review found
the `trading_service.execute_exit` branch -- the one `_execute_exit_actions`
actually takes in production, since `app/factory.py` wires
`trading_service=trade_executor` -- had no error handling at all) covers that
same real-callchain requirement for the *actually-production-relevant*
branch: a real `TradeExecutor.execute_exit` (app/trade_execution/trade_execution.py),
wrapped only with a `MagicMock(wraps=...)` spy (so the real implementation
still runs), driven by two real losing positions/exit actions in a single
tick. Proves both that a genuine failure closing the first position is
logged, and that the second position's exit is still attempted afterward --
i.e. one failure doesn't abort the rest of the batch.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock

from app.exit_strategies.exit_trade import ExitTrade
from app.risk.risk_manager import create_risk_manager
from app.services.trade_services import SignalOrchestrator, create_orchestrator
from app.trade_execution.broker import create_broker
from app.trade_execution.mode import TradingMode
from app.trade_execution.trade_execution import create_trade_executor

_LOGGER_NAME = "e2e-orchestrator-exit-close-failure"


def _wire_real_broker_with_one_losing_position(monkeypatch, make_position):
    """Real Broker (LIVE mode) whose close_position() call for a specific
    ticket genuinely raises -- simulating an MT5-level close failure -- while
    still answering the unfiltered positions_get() call used to discover the
    open (losing) position in the first place."""
    import MetaTrader5 as mt5

    losing_position = make_position(
        ticket=999,
        symbol="EURUSD",
        type=0,  # buy
        volume=0.01,
        price_open=1.1000,
        profit=-5.0,  # <= LossExitManager's drop_profit threshold: instant exit
    )

    def _fake_positions_get(*args, **kwargs):
        if "ticket" in kwargs:
            raise RuntimeError(
                "simulated MT5 failure: positions_get(ticket=...) failed"
            )
        return (losing_position,)

    monkeypatch.setattr(mt5, "positions_get", _fake_positions_get)

    return create_broker(TradingMode.LIVE)


class TestOrchestratorLogsExitCloseFailures:
    def test_broker_close_failure_during_tick_driven_exit_is_logged(
        self, mock_mt5, make_tick, make_position, monkeypatch, caplog
    ):
        """The real chain (ExitTrade -> real 'profit_drop' exit action ->
        Broker.close_position raising) must be logged via the orchestrator's
        logger, not silently disappear."""
        broker = _wire_real_broker_with_one_losing_position(monkeypatch, make_position)
        risk_manager = create_risk_manager(broker)
        exit_trade = ExitTrade(broker=broker, risk_manager=risk_manager)

        logger = logging.getLogger(_LOGGER_NAME)

        orchestrator = create_orchestrator(
            collector=None,
            signal_generator=None,
            broker=broker,
            trading_service=None,
            tick_collector=None,
            exit_trade=exit_trade,
            enter_trade=None,
            logger=logger,
        )

        tick = make_tick()

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            orchestrator._on_tick(tick)

        matching = [
            r
            for r in caplog.records
            if r.name == _LOGGER_NAME and "broker close failed" in r.getMessage()
        ]
        assert matching, (
            "expected SignalOrchestrator to log the real Broker.close_position "
            "failure (ticket=999, symbol=EURUSD) instead of silently "
            f"swallowing it; captured records: "
            f"{[r.getMessage() for r in caplog.records]!r}"
        )

        record = matching[0]
        message = record.getMessage()
        assert "ticket=999" in message
        assert "EURUSD" in message
        assert "RuntimeError" in message
        assert record.levelno == logging.ERROR
        # `_log_exception` must call `logger.exception(...)` (not just
        # `logger.error(...)` with a hand-built string) so the real live
        # exception/traceback -- raised for real inside the real
        # Broker.close_position call -- is what's captured.
        assert record.exc_info is not None
        assert record.exc_info[1] is not None
        assert isinstance(record.exc_info[1], RuntimeError)

    def test_no_bare_except_pass_remains_around_the_close_call(self):
        """Documentation/regression-lock: pins the exact anti-pattern this
        subphase's requirements describe (a bare `except Exception: pass`
        immediately around the `closer(...)` call in
        `_execute_exit_actions`), so a future change can't silently
        reintroduce it without this test catching it, independent of the
        exception-raising scenario exercised above.
        """
        import inspect

        source = inspect.getsource(SignalOrchestrator._execute_exit_actions)
        assert not re.search(r"except\s+Exception\s*:\s*pass\b", source), (
            "found a bare `except Exception: pass` in "
            "_execute_exit_actions -- broker close failures would be "
            "silently swallowed again; this subphase requires "
            "`self._log_exception(...)` instead"
        )

    def test_typeerror_fallback_branch_is_unreachable_for_the_real_broker(
        self, mock_mt5
    ):
        """`_execute_exit_actions` has *two* bare `except Exception: pass`
        blocks: one around the primary keyword-argument close call, and one
        around a positional-argument fallback that's only reached if the
        primary call raises `TypeError`. The behavioral test above only
        exercises the first (the primary call raising a non-TypeError
        error) because that's the realistic, reachable failure mode for the
        real `Broker.close_position`.

        Mirroring Subphase 3.2's e2e style for documenting unreachable
        fallback branches: `Broker.close_position`'s real signature
        (`close_position(self, ticket=None, symbol=None, side=None,
        volume=None)`) always accepts the keyword-argument call
        `_execute_exit_actions` tries first, so the TypeError-triggered
        fallback branch (and its own bare except-pass block) is dead code
        for real usage in this codebase -- there is no full-stack
        before/after distinguishing scenario to construct for it. Both bare
        except blocks are still statically pinned together by
        `test_no_bare_except_pass_remains_around_the_close_call` above.
        """
        broker = create_broker(TradingMode.DEMO)

        # Must not raise TypeError for the exact call shape
        # _execute_exit_actions always tries first.
        broker.close_position(ticket=123, symbol="EURUSD", side="sell", volume=0.01)


class TestOrchestratorLogsTradingServiceExitFailures:
    """Covers the `trading_service.execute_exit` branch of
    `_execute_exit_actions` -- the branch actually taken in production,
    since `app/factory.py` wires `trading_service=trade_executor` (a real
    `TradeExecutor`), not the `broker`-fallback branch the tests above
    exercise. pr-review found this branch had *zero* error handling prior to
    the fix in commit `ae6636d`: a single failed exit both went unlogged and
    silently aborted every remaining action in the batch.
    """

    def test_trading_service_execute_exit_failure_is_logged_and_batch_continues(
        self, mock_mt5, make_tick, make_position, monkeypatch, caplog
    ):
        import MetaTrader5 as mt5

        losing_eurusd = make_position(
            ticket=999,
            symbol="EURUSD",
            type=0,  # buy
            volume=0.01,
            price_open=1.1000,
            profit=-5.0,  # instant real "profit_drop" exit, same as the tests above
        )
        losing_gbpusd = make_position(
            ticket=888,
            symbol="GBPUSD",
            type=0,  # buy
            volume=0.02,
            price_open=1.2500,
            profit=-5.0,
        )

        def _fake_positions_get(*args, **kwargs):
            symbol = kwargs.get("symbol")
            if symbol is None:
                # ExitTrade.on_tick's unfiltered discovery call: both real
                # losing positions are genuinely open.
                return (losing_eurusd, losing_gbpusd)
            if symbol == "EURUSD":
                # TradeExecutor.execute_exit's per-symbol lookup for the
                # first action: simulate a genuine MT5-level failure.
                raise RuntimeError(
                    "simulated MT5 failure: positions_get(symbol=EURUSD) failed"
                )
            if symbol == "GBPUSD":
                # Second action's lookup succeeds -- proving the batch
                # continues past the first (failed) action. mock_mt5's
                # symbol_info_tick stub returns None, so execute_exit stops
                # gracefully right after (no order actually placed) --
                # that's fine, this test only needs to prove the second
                # action was genuinely *attempted*, not that it fully fills.
                return (losing_gbpusd,)
            return ()

        monkeypatch.setattr(mt5, "positions_get", _fake_positions_get)

        broker = create_broker(TradingMode.LIVE)
        risk_manager = create_risk_manager(broker)
        exit_trade = ExitTrade(broker=broker, risk_manager=risk_manager)
        trade_executor = create_trade_executor(
            risk_manager, broker, market_data=MagicMock()
        )

        # Spy on the real, bound execute_exit -- `wraps` means the actual
        # production implementation still runs; this only lets the test
        # observe call count/order without faking the method itself.
        real_execute_exit = trade_executor.execute_exit
        spy_execute_exit = MagicMock(wraps=real_execute_exit)
        trade_executor.execute_exit = spy_execute_exit

        logger = logging.getLogger(_LOGGER_NAME)

        orchestrator = create_orchestrator(
            collector=None,
            signal_generator=None,
            broker=broker,
            trading_service=trade_executor,  # the real, production-shaped wiring
            tick_collector=None,
            exit_trade=exit_trade,
            enter_trade=None,
            logger=logger,
        )

        tick = make_tick()

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            orchestrator._on_tick(tick)

        assert spy_execute_exit.call_count == 2, (
            "expected both real exit actions (EURUSD ticket=999, GBPUSD "
            "ticket=888) to be attempted via trading_service.execute_exit -- "
            "a failure on the first must not silently abort the batch; "
            f"call_args_list={spy_execute_exit.call_args_list!r}"
        )

        matching = [
            r
            for r in caplog.records
            if r.name == _LOGGER_NAME and "exit execution failed" in r.getMessage()
        ]
        assert matching, (
            "expected SignalOrchestrator to log the real "
            "TradeExecutor.execute_exit failure (ticket=999, symbol=EURUSD) "
            "instead of silently swallowing it; captured records: "
            f"{[r.getMessage() for r in caplog.records]!r}"
        )
        assert len(matching) == 1, (
            "expected exactly one logged failure (only the EURUSD action "
            f"genuinely raised); got {[r.getMessage() for r in matching]!r}"
        )

        record = matching[0]
        message = record.getMessage()
        assert "ticket=999" in message
        assert "EURUSD" in message
        assert "RuntimeError" in message
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None
        assert isinstance(record.exc_info[1], RuntimeError)

    def test_execute_exit_actions_prefers_trading_service_over_broker_fallback(self):
        """Documents why the tests above (which use `trading_service=None`)
        and this class (which uses a real `trading_service`) are both
        necessary, not redundant: `_execute_exit_actions` checks
        `trading_service` first and `return`s before ever reaching the
        broker-fallback code, so the two branches are mutually exclusive at
        runtime and each needs its own real-callchain coverage.
        """
        import inspect

        source = inspect.getsource(SignalOrchestrator._execute_exit_actions)
        trading_service_idx = source.index("self.trading_service and hasattr")
        return_idx = source.index("return", trading_service_idx)
        broker_fallback_idx = source.index("# Fallback: call broker directly")
        assert trading_service_idx < return_idx < broker_fallback_idx, (
            "expected the trading_service branch to return before the "
            "broker-fallback code -- if this ever changes, both branches "
            "could run for the same action and the two test classes in "
            "this file would need to be revisited"
        )
