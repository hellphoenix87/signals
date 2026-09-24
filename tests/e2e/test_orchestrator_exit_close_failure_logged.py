"""E2E coverage: `SignalOrchestrator._execute_exit_actions` must log an exit
failure and keep processing the rest of the batch.

`_execute_exit_actions` (app/services/trade_services.py) dispatches every exit
action to `trade_executor.execute_exit(action)`. A failure there -- a stale MT5
connection, an already-closed ticket -- must be logged via the orchestrator's
logger rather than silently swallowed, and must not abort the remaining actions
in the same tick's batch.

This drives a real `SignalOrchestrator`, wired to a real `ExitTrade` (with its
real `LossExitManager` -- app/exit_strategies/exit_trade.py,
app/exit_strategies/managers/loss.py), a real `Broker`
(`create_broker(TradingMode.LIVE)`) and a real `TradeExecutor`. Nothing about
the exit-decision or close-attempt logic is faked. Only the MT5 boundary itself
is mocked: the `mock_mt5` fixture from tests/conftest.py, plus a local
`mt5.positions_get` override that raises for one specific symbol's lookup
(simulating a genuine MT5-level failure) while still answering the unfiltered
`positions_get()` call `ExitTrade.on_tick` makes to discover the open positions.

Positions with `profit=-5.0` trigger `LossExitManager`'s real "profit_drop"
protective exit on the very first tick (no arming/pending state needed) -- a
genuine exit decision, not a hand-crafted exit action.
"""

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


class TestOrchestratorLogsExitExecutionFailures:
    def test_execute_exit_failure_is_logged_and_batch_continues(
        self, mock_mt5, make_tick, make_position, monkeypatch, caplog
    ):
        import MetaTrader5 as mt5

        losing_eurusd = make_position(
            ticket=999,
            symbol="EURUSD",
            type=0,  # buy
            volume=0.01,
            price_open=1.1000,
            profit=-5.0,  # instant real "profit_drop" exit
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
        spy_execute_exit = MagicMock(wraps=trade_executor.execute_exit)
        trade_executor.execute_exit = spy_execute_exit

        logger = logging.getLogger(_LOGGER_NAME)

        orchestrator = create_orchestrator(
            collector=None,
            signal_generator=None,
            trade_executor=trade_executor,
            tick_collector=None,
            exit_trade=exit_trade,
            logger=logger,
        )

        tick = make_tick()

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            orchestrator._on_tick(tick)

        assert spy_execute_exit.call_count == 2, (
            "expected both real exit actions (EURUSD ticket=999, GBPUSD "
            "ticket=888) to be attempted via trade_executor.execute_exit -- "
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
        # `_log_exception` must call `logger.exception(...)` (not just
        # `logger.error(...)` with a hand-built string) so the real live
        # exception/traceback -- raised for real inside the real call chain --
        # is what's captured.
        assert record.exc_info is not None
        assert isinstance(record.exc_info[1], RuntimeError)

    def test_no_bare_except_pass_remains_around_the_exit_call(self):
        """Regression-lock: pins the anti-pattern this coverage exists to
        prevent (a bare `except Exception: pass` around the exit dispatch in
        `_execute_exit_actions`), so a future change can't silently
        reintroduce it, independent of the scenario exercised above.
        """
        import inspect

        source = inspect.getsource(SignalOrchestrator._execute_exit_actions)
        assert not re.search(r"except\s+Exception\s*:\s*pass\b", source), (
            "found a bare `except Exception: pass` in _execute_exit_actions "
            "-- exit failures would be silently swallowed again; "
            "`self._log_exception(...)` is required instead"
        )
