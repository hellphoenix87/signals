"""Tests for app/trade_execution/broker.py"""

import pytest

from app.trade_execution.broker import Broker
from app.trade_execution.mode import TradingMode
from app.exit_strategies.exit_shared import pos_volume


class TestBrokerSimulatedTickets:
    """Test that simulated (backtest) positions get unique tickets."""

    def test_close_position_by_ticket_removes_correct_position(self, mock_mt5):
        """Test that closing by ticket removes the correct simulated position."""
        broker = Broker(TradingMode.BACKTEST)

        broker.place_buy("EURUSD", 0.01, None, None, price=1.1000)
        broker.place_buy("EURUSD", 0.02, None, None, price=1.1010)

        ticket1 = broker.open_positions_sim[0]["ticket"]
        ticket2 = broker.open_positions_sim[1]["ticket"]

        broker.close_position(ticket=ticket1)

        assert len(broker.open_positions_sim) == 1
        assert broker.open_positions_sim[0]["ticket"] == ticket2

    def test_backtest_trade_assigns_unique_tickets(self, mock_mt5):
        """Test that backtest mode assigns unique tickets."""
        broker = Broker(TradingMode.BACKTEST)

        # Place two backtest trades with explicit prices
        broker._backtest_trade("EURUSD", "BUY", 0.01, 1.0900, 1.1100, 1.1000)
        broker._backtest_trade("EURUSD", "SELL", 0.01, 1.1200, 1.0900, 1.1050)

        # Verify we have 2 positions
        assert len(broker.open_positions_sim) == 2

        # Verify each has a unique ticket
        ticket1 = broker.open_positions_sim[0].get("ticket")
        ticket2 = broker.open_positions_sim[1].get("ticket")

        assert ticket1 is not None
        assert ticket2 is not None
        assert ticket1 != ticket2

    def test_backtest_trade_without_price_is_rejected(self, mock_mt5):
        """Backtest mode has no live tick to fall back on, so a missing price raises."""
        broker = Broker(TradingMode.BACKTEST)

        with pytest.raises(ValueError):
            broker.place_buy("EURUSD", 0.01, None, None)


class TestBrokerSimulatedPositionVolume:
    """Test that simulated positions have 'volume' key readable by exit managers."""

    def test_backtest_position_has_volume_key_readable_by_pos_volume(self, mock_mt5):
        """Test that pos_volume() can read volume from backtest simulated positions."""
        broker = Broker(TradingMode.BACKTEST)

        placed_volume = 0.02
        broker._backtest_trade("EURUSD", "BUY", placed_volume, 1.0900, 1.1100, 1.1000)

        assert len(broker.open_positions_sim) == 1

        position = broker.open_positions_sim[0]

        # Verify 'volume' key exists (not 'lot')
        assert "volume" in position, "Backtest position should have 'volume' key"
        assert position["volume"] == placed_volume

        # Verify pos_volume() can read it
        volume = pos_volume(position)
        assert volume is not None
        assert volume == placed_volume
