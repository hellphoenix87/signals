"""Tests for SignalOrchestrator's entry, tick, and exit-dispatch logic."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.services.trade_services import SignalOrchestrator


def _orchestrator(**overrides):
    """Build a SignalOrchestrator with MagicMock collaborators.

    `collector`, `signal_generator` and `trade_executor` are required by the
    real constructor, so they always get a mock unless overridden.
    """
    kwargs = {
        "collector": MagicMock(),
        "signal_generator": MagicMock(),
        "trade_executor": MagicMock(),
    }
    kwargs.update(overrides)
    return SignalOrchestrator(**kwargs)


class TestSignalOrchestratorEntryDispatch:
    """`_run_entries` hands signals to `trade_executor.process_signal`."""

    def test_run_entries_passes_signals_and_snapshot_to_trade_executor(self):
        trade_executor = MagicMock()
        signal_generator = MagicMock()

        orchestrator = _orchestrator(
            signal_generator=signal_generator, trade_executor=trade_executor
        )

        signal_dict = {
            "symbol": "EURUSD",
            "final_signal": "buy",
            "pullback_completed": True,
        }
        signal_generator.generate_signal = MagicMock(return_value=signal_dict)

        snapshot = {"some": "data"}
        orchestrator._run_entries(snapshot=snapshot, asof=datetime.now(timezone.utc))

        trade_executor.process_signal.assert_called_once_with([signal_dict], snapshot)

    def test_run_entries_logs_process_signal_failure_without_raising(self):
        trade_executor = MagicMock()
        trade_executor.process_signal = MagicMock(side_effect=RuntimeError("boom"))
        signal_generator = MagicMock()
        signal_generator.generate_signal = MagicMock(
            return_value={"symbol": "EURUSD", "final_signal": "buy"}
        )
        logger = MagicMock()

        orchestrator = _orchestrator(
            signal_generator=signal_generator,
            trade_executor=trade_executor,
            logger=logger,
        )

        orchestrator._run_entries(snapshot={}, asof=datetime.now(timezone.utc))

        logger.exception.assert_called_once()
        assert "process_signal" in logger.exception.call_args[0][0]

    def test_run_entries_updates_htf_bias_before_executing(self):
        exit_trade = MagicMock()
        signal_generator = MagicMock()
        signal_generator.generate_signal = MagicMock(
            return_value={
                "symbol": "EURUSD",
                "final_signal": "buy",
                "m5_confirm": "buy",
                "m15_bias": "buy",
            }
        )

        orchestrator = _orchestrator(
            signal_generator=signal_generator, exit_trade=exit_trade
        )

        asof = datetime.now(timezone.utc)
        orchestrator._run_entries(snapshot={}, asof=asof)

        exit_trade.update_bias.assert_called_once_with(
            "EURUSD", m5="buy", m15="buy", asof_epoch=asof.timestamp()
        )


class TestSignalOrchestratorGenerateSignalCall:
    """Tests for the direct call to signal_generator.generate_signal()."""

    def test_run_entries_calls_generate_signal_directly_with_snapshot(self):
        """
        Verifies that _run_entries calls signal_generator.generate_signal exactly once
        with snapshot as the sole positional argument (no fallback lookups or calling
        conventions).
        """
        signal_generator = MagicMock()
        orchestrator = _orchestrator(signal_generator=signal_generator)

        signal_generator.generate_signal = MagicMock(
            return_value={"symbol": "EURUSD", "final_signal": "hold"}
        )

        snapshot = {"some": "data"}
        orchestrator._run_entries(snapshot=snapshot, asof=datetime.now(timezone.utc))

        signal_generator.generate_signal.assert_called_once_with(snapshot)

    def test_run_entries_handles_exception_from_generate_signal(self):
        """
        Verifies that exceptions from generate_signal are caught and logged,
        and execution continues gracefully without raising.
        """
        signal_generator = MagicMock()
        logger = MagicMock()

        orchestrator = _orchestrator(
            signal_generator=signal_generator, logger=logger
        )

        signal_generator.generate_signal = MagicMock(
            side_effect=ValueError("Signal generation failed")
        )

        snapshot = {}
        orchestrator._run_entries(snapshot=snapshot, asof=datetime.now(timezone.utc))

        signal_generator.generate_signal.assert_called_once_with(snapshot)
        logger.exception.assert_called_once()


class TestSignalOrchestratorRealStrategies:
    """Tests for _run_entries with real strategy implementations."""

    @pytest.mark.parametrize(
        "strategy_class",
        [
            "StrongSignalStrategy",
            "MultiTimeframeStrongSignalStrategy",
            "NTickConfirmedSignalStrategy",
        ],
    )
    def test_run_entries_works_with_real_strategy_implementations(self, strategy_class):
        """
        Parametrized test: instantiate each real strategy class with minimal
        constructor args and verify _run_entries can call generate_signal without raising.
        """
        from app.signals.strategies.strong_signal_strategy import StrongSignalStrategy
        from app.signals.strategies.multi_timeframe import (
            MultiTimeframeStrongSignalStrategy,
        )
        from app.signals.strategies.ntick_confirmed_signal_strategy import (
            NTickConfirmedSignalStrategy,
        )

        logger = MagicMock()

        if strategy_class == "StrongSignalStrategy":
            signal_generator = StrongSignalStrategy(indicators={})
        elif strategy_class == "MultiTimeframeStrongSignalStrategy":
            # Three separate per-timeframe strategies, matching the real ctor.
            signal_generator = MultiTimeframeStrongSignalStrategy(
                bias_strategy=StrongSignalStrategy(indicators={}),
                confirm_strategy=StrongSignalStrategy(indicators={}),
                entry_strategy=StrongSignalStrategy(indicators={}),
                tf_bias=15,
                tf_confirm=5,
                tf_entry=1,
            )
        else:
            signal_generator = NTickConfirmedSignalStrategy(
                base_strategy=StrongSignalStrategy(indicators={})
            )

        # Spy on the real generate_signal so we can assert it was actually
        # invoked, rather than only checking that _run_entries didn't raise
        # (which every real implementation also satisfies vacuously, since
        # _run_entries swallows all exceptions).
        generate_signal_spy = MagicMock(wraps=signal_generator.generate_signal)
        signal_generator.generate_signal = generate_signal_spy

        orchestrator = _orchestrator(
            signal_generator=signal_generator, logger=logger
        )

        if strategy_class == "MultiTimeframeStrongSignalStrategy":
            # MultiTimeframe expects dict[int, list[dict]], keyed by timeframe
            snapshot = {
                1: [{"close": 1.1000, "symbol": "EURUSD", "time": datetime.now(timezone.utc)}],
                5: [{"close": 1.1001, "symbol": "EURUSD", "time": datetime.now(timezone.utc)}],
                15: [{"close": 1.1002, "symbol": "EURUSD", "time": datetime.now(timezone.utc)}],
            }
        else:
            snapshot = [
                {"close": 1.1000, "symbol": "EURUSD", "time": datetime.now(timezone.utc)}
            ]

        orchestrator._run_entries(snapshot=snapshot, asof=datetime.now(timezone.utc))

        # generate_signal must have actually been invoked with the snapshot
        # positionally, and its call must not have raised (no exception logged) -
        # a real regression check, not just "no exception propagated out".
        generate_signal_spy.assert_called_once_with(snapshot)
        logger.exception.assert_not_called()


class TestSignalOrchestratorOnTick:
    """`_on_tick` executes a confirmed signal via `trade_executor.process_signal`."""

    def test_on_tick_executes_confirmed_signal(self):
        trade_executor = MagicMock()
        signal_generator = MagicMock()

        orchestrator = _orchestrator(
            signal_generator=signal_generator, trade_executor=trade_executor
        )

        signal_dict = {"symbol": "GBPUSD", "final_signal": "buy"}
        signal_generator.get_confirmed_signal = MagicMock(return_value=signal_dict)
        signal_generator.on_new_tick = MagicMock()

        orchestrator._on_tick({"bid": 1.5000, "ask": 1.5002})

        trade_executor.process_signal.assert_called_once_with([signal_dict], None)
        assert orchestrator.get_latest_signal() == signal_dict

    def test_on_tick_ignores_a_hold_signal(self):
        trade_executor = MagicMock()
        signal_generator = MagicMock()

        orchestrator = _orchestrator(
            signal_generator=signal_generator, trade_executor=trade_executor
        )

        signal_generator.get_confirmed_signal = MagicMock(
            return_value={"symbol": "GBPUSD", "final_signal": "hold"}
        )
        signal_generator.on_new_tick = MagicMock()

        orchestrator._on_tick({"bid": 1.5000, "ask": 1.5002})

        trade_executor.process_signal.assert_not_called()

    def test_on_tick_logs_exit_trade_failure_and_keeps_going(self):
        exit_trade = MagicMock()
        exit_trade.on_tick = MagicMock(side_effect=RuntimeError("exit blew up"))
        signal_generator = MagicMock(spec=[])
        logger = MagicMock()

        orchestrator = _orchestrator(
            signal_generator=signal_generator, exit_trade=exit_trade, logger=logger
        )

        orchestrator._on_tick({"bid": 1.5000, "ask": 1.5002})

        logger.exception.assert_called_once()
        assert "exit_trade.on_tick" in logger.exception.call_args[0][0]


class TestSignalOrchestratorExecuteExitActions:
    """`_execute_exit_actions` dispatches to `trade_executor.execute_exit`."""

    def test_execute_exit_actions_logs_failure_but_continues(self):
        """A failure on one action must be logged and must not abort the batch."""
        trade_executor = MagicMock()
        trade_executor.execute_exit = MagicMock(
            side_effect=[RuntimeError("MT5 connection lost"), None]
        )
        logger = MagicMock()

        orchestrator = _orchestrator(trade_executor=trade_executor, logger=logger)

        actions = [
            {"ticket": 1, "symbol": "EURUSD", "side": "sell", "volume": 0.01},
            {"ticket": 2, "symbol": "GBPUSD", "side": "buy", "volume": 0.02},
        ]

        orchestrator._execute_exit_actions(actions)

        assert trade_executor.execute_exit.call_count == 2

        logger.exception.assert_called_once()
        logged_msg = logger.exception.call_args[0][0]
        assert "exit execution failed" in logged_msg
        assert "ticket=1" in logged_msg
        assert "symbol=EURUSD" in logged_msg
        assert "RuntimeError" in logged_msg

    def test_execute_exit_actions_reads_context_from_object_shaped_actions(self):
        """Exit actions are `ExitAction` dataclasses in production, not dicts --
        the failure log must read ticket/symbol off attributes too."""

        class _Action:
            ticket = 7
            symbol = "USDJPY"

        trade_executor = MagicMock()
        trade_executor.execute_exit = MagicMock(side_effect=RuntimeError("boom"))
        logger = MagicMock()

        orchestrator = _orchestrator(trade_executor=trade_executor, logger=logger)
        orchestrator._execute_exit_actions([_Action()])

        logged_msg = logger.exception.call_args[0][0]
        assert "ticket=7" in logged_msg
        assert "symbol=USDJPY" in logged_msg
