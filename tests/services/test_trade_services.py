"""Tests for SignalOrchestrator trade entry logic."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import pytest

from app.services.trade_services import SignalOrchestrator


class TestSignalOrchestratorEnterTrade:
    """Tests for the EnterTrade integration in SignalOrchestrator._run_entries."""

    def test_run_entries_calls_enter_trade_with_signal_and_account_balance(self):
        """
        When _run_entries is called with a valid buy/sell signal and enter_trade
        is provided (trading_service=None), it should call enter_trade.enter_trade()
        with the signal dict and account_balance from the broker.

        This test verifies that the dead-code path (guessing at on_signal/execute/enter
        methods) is replaced with a direct call to enter_trade.enter_trade().
        """
        # Setup mocks
        collector_mock = MagicMock()
        signal_generator_mock = MagicMock()
        broker_mock = MagicMock()
        enter_trade_mock = MagicMock(spec=["enter_trade"])

        # Configure broker to return an account balance
        broker_mock.get_account_balance = MagicMock(return_value=10000.0)

        # Create orchestrator with enter_trade but no trading_service
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator_mock,
            broker=broker_mock,
            trading_service=None,
            enter_trade=enter_trade_mock,
        )

        # Create a signal that should trigger entry
        signal_dict = {
            "symbol": "EURUSD",
            "final_signal": "buy",
            "pullback_completed": True,
        }

        # Configure signal_generator to return this signal
        signal_generator_mock.generate_signal = MagicMock(return_value=signal_dict)

        # Call _run_entries with a snapshot and timestamp
        snapshot = {}
        asof = datetime.now(timezone.utc)
        orchestrator._run_entries(snapshot=snapshot, asof=asof)

        # Assert that enter_trade.enter_trade was called with the signal and account_balance
        enter_trade_mock.enter_trade.assert_called_once()
        call_args = enter_trade_mock.enter_trade.call_args

        # Check positional arguments
        assert call_args[0][0] == signal_dict  # First arg: signal dict
        assert call_args[0][1] == 10000.0  # Second arg: account_balance

    def test_run_entries_handles_missing_broker_balance(self):
        """
        When broker.get_account_balance is not callable or raises an exception,
        account_balance should default to 0.0.
        """
        # Setup mocks
        collector_mock = MagicMock()
        signal_generator_mock = MagicMock()
        broker_mock = MagicMock()
        enter_trade_mock = MagicMock(spec=["enter_trade"])

        # Configure broker to not have get_account_balance or have it raise
        broker_mock.get_account_balance = MagicMock(side_effect=Exception("No balance"))

        # Create orchestrator
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator_mock,
            broker=broker_mock,
            trading_service=None,
            enter_trade=enter_trade_mock,
        )

        # Create a signal
        signal_dict = {
            "symbol": "EURUSD",
            "final_signal": "sell",
            "pullback_completed": True,
        }

        # Configure signal_generator
        signal_generator_mock.generate_signal = MagicMock(return_value=signal_dict)

        # Call _run_entries
        snapshot = {}
        asof = datetime.now(timezone.utc)
        orchestrator._run_entries(snapshot=snapshot, asof=asof)

        # Assert enter_trade was called with 0.0 as account_balance
        enter_trade_mock.enter_trade.assert_called_once()
        call_args = enter_trade_mock.enter_trade.call_args
        assert call_args[0][1] == 0.0  # Second arg should be 0.0

    def test_run_entries_no_broker_defaults_account_balance_to_zero(self):
        """
        When broker is None, account_balance should default to 0.0.
        """
        # Setup mocks
        collector_mock = MagicMock()
        signal_generator_mock = MagicMock()
        enter_trade_mock = MagicMock(spec=["enter_trade"])

        # Create orchestrator with no broker
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator_mock,
            broker=None,
            trading_service=None,
            enter_trade=enter_trade_mock,
        )

        # Create a signal
        signal_dict = {
            "symbol": "EURUSD",
            "final_signal": "buy",
            "pullback_completed": True,
        }

        # Configure signal_generator
        signal_generator_mock.generate_signal = MagicMock(return_value=signal_dict)

        # Call _run_entries
        snapshot = {}
        asof = datetime.now(timezone.utc)
        orchestrator._run_entries(snapshot=snapshot, asof=asof)

        # Assert enter_trade was called with 0.0 as account_balance
        enter_trade_mock.enter_trade.assert_called_once()
        call_args = enter_trade_mock.enter_trade.call_args
        assert call_args[0][1] == 0.0  # Second arg should be 0.0


class TestSignalOrchestratorGenerateSignalCall:
    """Tests for the direct call to signal_generator.generate_signal()."""

    def test_run_entries_calls_generate_signal_directly_with_snapshot(self):
        """
        Verifies that _run_entries calls signal_generator.generate_signal exactly once
        with snapshot as the sole positional argument (no fallback lookups or calling
        conventions).
        """
        # Setup mocks
        collector_mock = MagicMock()
        signal_generator_mock = MagicMock()
        broker_mock = MagicMock()

        # Create orchestrator
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator_mock,
            broker=broker_mock,
        )

        # Create a signal dict to return
        signal_dict = {
            "symbol": "EURUSD",
            "final_signal": "hold",
        }

        # Configure signal_generator.generate_signal to return the signal
        signal_generator_mock.generate_signal = MagicMock(return_value=signal_dict)

        # Call _run_entries with snapshot and timestamp
        snapshot = {"some": "data"}
        asof = datetime.now(timezone.utc)
        orchestrator._run_entries(snapshot=snapshot, asof=asof)

        # Assert that generate_signal was called exactly once with snapshot as sole positional arg
        signal_generator_mock.generate_signal.assert_called_once_with(snapshot)

    def test_run_entries_handles_exception_from_generate_signal(self):
        """
        Verifies that exceptions from generate_signal are caught and logged,
        and execution continues gracefully without raising.
        """
        # Setup mocks
        collector_mock = MagicMock()
        signal_generator_mock = MagicMock()
        logger_mock = MagicMock()

        # Create orchestrator with a logger
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator_mock,
            logger=logger_mock,
        )

        # Configure signal_generator.generate_signal to raise
        signal_generator_mock.generate_signal = MagicMock(
            side_effect=ValueError("Signal generation failed")
        )

        # Call _run_entries - should not raise
        snapshot = {}
        asof = datetime.now(timezone.utc)
        orchestrator._run_entries(snapshot=snapshot, asof=asof)

        # Assert that generate_signal was called
        signal_generator_mock.generate_signal.assert_called_once_with(snapshot)
        # Assert that the exception was logged
        logger_mock.exception.assert_called_once()


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
        from app.signals.strategies.multi_timeframe import MultiTimeframeStrongSignalStrategy
        from app.signals.strategies.ntick_confirmed_signal_strategy import (
            NTickConfirmedSignalStrategy,
        )

        # Setup mocks
        collector_mock = MagicMock()
        broker_mock = MagicMock()
        logger_mock = MagicMock()

        # Create the strategy instance based on the parametrized class name
        if strategy_class == "StrongSignalStrategy":
            # Minimal: empty indicators dict
            signal_generator = StrongSignalStrategy(indicators={})
        elif strategy_class == "MultiTimeframeStrongSignalStrategy":
            # Minimal: base strategy with empty indicators
            base_strategy = StrongSignalStrategy(indicators={})
            signal_generator = MultiTimeframeStrongSignalStrategy(base=base_strategy)
        elif strategy_class == "NTickConfirmedSignalStrategy":
            # Minimal: base strategy with empty indicators
            base_strategy = StrongSignalStrategy(indicators={})
            signal_generator = NTickConfirmedSignalStrategy(
                base_strategy=base_strategy
            )

        # Spy on the real generate_signal so we can assert it was actually
        # invoked, rather than only checking that _run_entries didn't raise
        # (which every real implementation also satisfies vacuously, since
        # _run_entries swallows all exceptions).
        real_generate_signal = signal_generator.generate_signal
        generate_signal_spy = MagicMock(wraps=real_generate_signal)
        signal_generator.generate_signal = generate_signal_spy

        # Create orchestrator
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator,
            broker=broker_mock,
            logger=logger_mock,
        )

        # Create a candles snapshot (format depends on strategy)
        if strategy_class == "MultiTimeframeStrongSignalStrategy":
            # MultiTimeframe expects dict[int, list[dict]]
            snapshot = {
                1: [{"close": 1.1000, "symbol": "EURUSD", "time": datetime.now(timezone.utc)}],
                5: [{"close": 1.1001, "symbol": "EURUSD", "time": datetime.now(timezone.utc)}],
                15: [{"close": 1.1002, "symbol": "EURUSD", "time": datetime.now(timezone.utc)}],
            }
        else:
            # StrongSignalStrategy and NTickConfirmedSignalStrategy expect list[dict]
            snapshot = [
                {"close": 1.1000, "symbol": "EURUSD", "time": datetime.now(timezone.utc)}
            ]

        # Call _run_entries - should not raise
        asof = datetime.now(timezone.utc)
        orchestrator._run_entries(snapshot=snapshot, asof=asof)

        # generate_signal must have actually been invoked with the snapshot
        # positionally, and its call must not have raised (no exception logged) -
        # a real regression check, not just "no exception propagated out".
        generate_signal_spy.assert_called_once_with(snapshot)
        logger_mock.exception.assert_not_called()


class TestSignalOrchestratorOnTickEnterTrade:
    """Tests for the EnterTrade integration in SignalOrchestrator._on_tick."""

    def test_on_tick_calls_enter_trade_with_confirmed_signal(self):
        """
        When _on_tick receives a confirmed signal from the signal_generator
        and enter_trade is available (trading_service=None), it should call
        enter_trade.enter_trade() with the signal and account_balance.
        """
        # Setup mocks
        collector_mock = MagicMock()
        signal_generator_mock = MagicMock()
        broker_mock = MagicMock()
        enter_trade_mock = MagicMock(spec=["enter_trade"])

        # Configure broker to return an account balance
        broker_mock.get_account_balance = MagicMock(return_value=5000.0)

        # Create orchestrator
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator_mock,
            broker=broker_mock,
            trading_service=None,
            enter_trade=enter_trade_mock,
        )

        # Create a confirmed signal
        signal_dict = {
            "symbol": "GBPUSD",
            "final_signal": "buy",
        }

        # Configure signal_generator to have a confirmed signal
        signal_generator_mock.get_confirmed_signal = MagicMock(return_value=signal_dict)
        signal_generator_mock.on_new_tick = MagicMock()  # For tick forwarding

        # Create a tick
        tick = {"bid": 1.5000, "ask": 1.5002}

        # Call _on_tick
        orchestrator._on_tick(tick)

        # Assert enter_trade.enter_trade was called
        enter_trade_mock.enter_trade.assert_called_once()
        call_args = enter_trade_mock.enter_trade.call_args
        assert call_args[0][0] == signal_dict
        assert call_args[0][1] == 5000.0


class TestSignalOrchestratorExecuteExitActions:
    """Tests for _execute_exit_actions exit logging."""

    def test_execute_exit_actions_logs_broker_close_position_failure(self):
        """
        When broker.close_position raises an exception, the exception should
        be logged via logger.exception instead of being silently swallowed.
        Verifies both the keyword-argument call and the positional-argument
        fallback both log failures.
        """
        # Setup mocks
        collector_mock = MagicMock()
        signal_generator_mock = MagicMock()
        logger_mock = MagicMock()

        # Create a broker where close_position raises RuntimeError
        # Use spec=["close_position"] to ensure close_trade and close_order
        # are not auto-created as MagicMock attributes
        broker_mock = MagicMock(spec=["close_position"])
        broker_mock.close_position = MagicMock(side_effect=RuntimeError("boom"))

        # Create orchestrator with the broker and logger
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator_mock,
            broker=broker_mock,
            logger=logger_mock,
        )

        # Create an exit action
        action = {
            "ticket": 1,
            "symbol": "EURUSD",
            "side": "sell",
            "volume": 0.01,
        }

        # Call _execute_exit_actions
        orchestrator._execute_exit_actions([action])

        # Assert that logger.exception was called (proving the failure was logged)
        logger_mock.exception.assert_called_once()
        # Verify the logged message mentions the error and relevant context
        call_args = logger_mock.exception.call_args
        logged_msg = call_args[0][0]
        assert "broker close failed" in logged_msg
        assert "ticket=1" in logged_msg
        assert "symbol=EURUSD" in logged_msg
        assert "RuntimeError" in logged_msg

    def test_execute_exit_actions_logs_positional_args_fallback_failure(self):
        """
        When the keyword-argument call to broker.close_position raises TypeError
        (wrong signature) and the positional-argument fallback also raises an exception,
        that exception should be logged instead of silently swallowed.
        """
        # Setup mocks
        collector_mock = MagicMock()
        signal_generator_mock = MagicMock()
        logger_mock = MagicMock()

        # Create a broker where close_position raises TypeError first, then ValueError in fallback
        broker_mock = MagicMock(spec=["close_position"])
        broker_mock.close_position = MagicMock(
            side_effect=[TypeError("wrong sig"), ValueError("positional failed")]
        )

        # Create orchestrator
        orchestrator = SignalOrchestrator(
            collector=collector_mock,
            signal_generator=signal_generator_mock,
            broker=broker_mock,
            logger=logger_mock,
        )

        # Create an exit action
        action = {
            "ticket": 1,
            "symbol": "EURUSD",
            "side": "sell",
            "volume": 0.01,
        }

        # Call _execute_exit_actions
        orchestrator._execute_exit_actions([action])

        # Assert that logger.exception was called for the second failure
        logger_mock.exception.assert_called_once()
        call_args = logger_mock.exception.call_args
        logged_msg = call_args[0][0]
        assert "broker close failed" in logged_msg
        assert "ValueError" in logged_msg
