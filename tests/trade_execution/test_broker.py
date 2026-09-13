"""Tests for app/trade_execution/broker.py"""

import pytest
from app.trade_execution.broker import Broker, create_broker
from app.trade_execution.mode import TradingMode
from app.exit_strategies.exit_shared import pos_volume, pos_ticket


class TestBrokerSimulatedTickets:
    """Test that simulated (demo/backtest) positions get unique tickets."""

    def test_place_buy_twice_assigns_unique_tickets(self, mock_mt5, make_tick):
        """Test that two buy trades get unique, non-None tickets."""
        # Setup: mock mt5.symbol_info_tick to return a valid tick
        mock_mt5.symbol_info_tick.return_value = make_tick(as_dict=False, bid=1.1000, ask=1.1002)

        # Create a demo-mode broker
        broker = Broker(TradingMode.DEMO)

        # Place two buy trades
        broker.place_buy("EURUSD", 0.01, None, None)
        broker.place_buy("EURUSD", 0.01, None, None)

        # Verify we have 2 positions
        assert len(broker.open_positions_sim) == 2

        # Verify each has a ticket
        ticket1 = broker.open_positions_sim[0].get("ticket")
        ticket2 = broker.open_positions_sim[1].get("ticket")

        assert ticket1 is not None, "First trade should have a ticket"
        assert ticket2 is not None, "Second trade should have a ticket"
        assert ticket1 != ticket2, "Tickets should be unique"

    def test_close_position_by_ticket_removes_correct_position(self, mock_mt5, make_tick):
        """Test that closing by ticket removes the correct simulated position."""
        mock_mt5.symbol_info_tick.return_value = make_tick(as_dict=False, bid=1.1000, ask=1.1002)

        broker = Broker(TradingMode.DEMO)

        # Place two trades
        broker.place_buy("EURUSD", 0.01, None, None)
        broker.place_buy("EURUSD", 0.02, None, None)

        ticket1 = broker.open_positions_sim[0]["ticket"]
        ticket2 = broker.open_positions_sim[1]["ticket"]

        # Close the first position by ticket
        broker.close_position(ticket=ticket1)

        # Verify only one position remains
        assert len(broker.open_positions_sim) == 1

        # Verify the remaining position is the second one
        assert broker.open_positions_sim[0]["ticket"] == ticket2

    def test_backtest_trade_assigns_unique_tickets(self, mock_mt5):
        """Test that backtest mode also assigns unique tickets."""
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


class TestBrokerSimulatedPositionVolume:
    """Test that simulated positions have 'volume' key readable by exit managers."""

    def test_simulated_position_has_volume_key_readable_by_pos_volume(self, mock_mt5, make_tick):
        """Test that pos_volume() can read the volume from a simulated position."""
        mock_mt5.symbol_info_tick.return_value = make_tick(as_dict=False, bid=1.1000, ask=1.1002)

        broker = Broker(TradingMode.DEMO)

        # Place a trade
        placed_volume = 0.01
        broker.place_buy("EURUSD", placed_volume, None, None)

        # Verify the position was created
        assert len(broker.open_positions_sim) == 1

        # Get the simulated position
        position = broker.open_positions_sim[0]

        # Verify it has a 'volume' key (not 'lot')
        assert "volume" in position, "Simulated position should have 'volume' key"
        assert position["volume"] == placed_volume

        # Verify pos_volume() can read it (the key accessor from exit_shared)
        volume = pos_volume(position)
        assert volume is not None, "pos_volume() should not return None for simulated positions"
        assert volume == placed_volume

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
