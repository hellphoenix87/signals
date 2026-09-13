"""Tests for app/exit_strategies/exit_trade.py."""

from unittest.mock import MagicMock

from app.exit_strategies.exit_trade import ExitTrade, ExitTradeConfig
from app.exit_strategies.exit_shared import PosState


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


class TestExitTradeOnCandleCloseWithHTF:
    """Tests for ExitTrade.on_candle_close()'s HTF gating of profit exits."""

    def test_htf_bias_blocks_candle_close_profit_exit(self, make_position):
        """Test that on_candle_close respects HTF bias and blocks exit when supportive.

        Given:
          - An ExitTrade configured with htf_filter_enabled=True, profit_exits_on_candle_close=True
          - A buy position with profit=0.05, break-even armed with best_profit=0.10
          - HTF bias set to m15="buy" (supportive of the position)

        When:
          - on_candle_close(symbol="EURUSD", close_price=1.1000) is called
          - The profit (0.05) would normally breach from best_profit (0.10) by 0.05
          - But HTF m15 bias supports the buy position

        Then:
          - It returns [] (empty list, exit is blocked)
        """
        mock_broker = MagicMock()
        mock_risk_manager = MagicMock()

        # Create ExitTrade with HTF filtering and candle-close exits enabled
        config = ExitTradeConfig(
            htf_filter_enabled=True,
            profit_exits_on_candle_close=True,
            profit_exits_on_tick=False,
        )
        exit_trade = ExitTrade(broker=mock_broker, risk_manager=mock_risk_manager, config=config)

        # Set HTF bias: m15="buy" supports a buy position
        exit_trade.update_bias("EURUSD", m15="buy")

        # Create a buy position with profit=0.05
        position = make_position(
            as_dict=True,
            symbol="EURUSD",
            type=0,  # buy
            ticket=1,
            volume=0.01,
            price_open=1.1000,
            profit=0.05,
        )
        mock_broker.get_open_positions.return_value = [position]

        # Manually set up state with best_profit=0.10 to trigger breach condition
        state = PosState(anchor=1.1000, prev_price=1.1000)
        state.be_armed = True
        state.best_profit = 0.10
        exit_trade._state_by_ticket[1] = state

        # Act: Call on_candle_close
        result = exit_trade.on_candle_close(symbol="EURUSD", close_price=1.1000)

        # Assert: Exit should be blocked by HTF gating
        assert result == []

    def test_htf_bias_allows_candle_close_profit_exit_when_opposing(self, make_position):
        """Test that on_candle_close allows exit when HTF bias opposes position.

        Given:
          - An ExitTrade configured with htf_filter_enabled=True, profit_exits_on_candle_close=True
          - A buy position with profit=0.05, break-even armed with best_profit=0.10
          - HTF bias set to m15="sell" (opposing the position)

        When:
          - on_candle_close(symbol="EURUSD", close_price=1.1000) is called
          - The profit (0.05) breaches from best_profit (0.10) by 0.05
          - HTF m15 bias opposes the buy position (allows exit)

        Then:
          - It returns a non-empty list with an exit action with reason="trailing_breach_gt_5c"
        """
        mock_broker = MagicMock()
        mock_risk_manager = MagicMock()

        # Create ExitTrade with HTF filtering and candle-close exits enabled
        config = ExitTradeConfig(
            htf_filter_enabled=True,
            profit_exits_on_candle_close=True,
            profit_exits_on_tick=False,
        )
        exit_trade = ExitTrade(broker=mock_broker, risk_manager=mock_risk_manager, config=config)

        # Set HTF bias: m15="sell" opposes a buy position
        exit_trade.update_bias("EURUSD", m15="sell")

        # Create a buy position with profit=0.05
        position = make_position(
            as_dict=True,
            symbol="EURUSD",
            type=0,  # buy
            ticket=1,
            volume=0.01,
            price_open=1.1000,
            profit=0.05,
        )
        mock_broker.get_open_positions.return_value = [position]

        # Manually set up state with best_profit=0.10 to trigger breach condition
        state = PosState(anchor=1.1000, prev_price=1.1000)
        state.be_armed = True
        state.best_profit = 0.10
        exit_trade._state_by_ticket[1] = state

        # Act: Call on_candle_close
        result = exit_trade.on_candle_close(symbol="EURUSD", close_price=1.1000)

        # Assert: Exit should be allowed because HTF opposes
        assert len(result) > 0
        assert result[0].reason == "trailing_breach_gt_5c"
        assert result[0].ticket == 1
        assert result[0].symbol == "EURUSD"
