"""Tests for app/exit_strategies/exit_trade.py."""

from unittest.mock import MagicMock

from app.exit_strategies.exit_trade import ExitTrade


class TestExitTradeOnTickCooldown:
    """Tests for ExitTrade.on_tick()'s per-ticket cooldown gate."""

    def test_cooldown_gate_suppresses_second_exit_on_same_ticket(
        self, make_position, make_tick
    ):
        """Test that _should_exit's per-ticket cooldown suppresses a second exit action.

        Given:
          - An ExitTrade instance with mock broker and risk_manager
          - The broker returns one position with ticket=1 and profit=-6.0
            (which triggers LossExitManager's profit_drop exit on first tick)

        When:
          - exit_trade.on_tick(make_tick()) is called twice back-to-back
            (same test, no sleep, so both calls are within the cooldown window)

        Then:
          - The first call's return list has length 1 with reason="profit_drop"
          - The second call returns [] (empty list) because same ticket is within cooldown
        """
        # Arrange
        mock_broker = MagicMock()
        mock_risk_manager = MagicMock()

        # Set up the broker to return a position with profit=-6.0 (triggers loss exit)
        position = make_position(as_dict=True, ticket=1, profit=-6.0)
        mock_broker.get_open_positions.return_value = [position]

        exit_trade = ExitTrade(broker=mock_broker, risk_manager=mock_risk_manager)
        tick = make_tick()

        # Act & Assert - First call should return an exit action
        result1 = exit_trade.on_tick(tick)
        assert len(result1) == 1
        assert result1[0].reason == "profit_drop"
        assert result1[0].ticket == 1

        # Act & Assert - Second call (within cooldown) should return empty list
        result2 = exit_trade.on_tick(tick)
        assert result2 == []
