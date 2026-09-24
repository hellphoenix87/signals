"""Tests for app/exit_strategies/managers/profit.py."""

from unittest.mock import MagicMock

import pytest

from app.exit_strategies.exit_shared import ExitAction, PosState
from app.exit_strategies.managers.profit import ProfitExitManager


@pytest.fixture
def mock_config():
    """Mock config object for ProfitExitManager."""
    config = MagicMock()
    config.profit_exits_on_tick = True
    config.be_distance_pips = 3.0
    config.htf_filter_enabled = False
    return config


@pytest.fixture
def mock_exit_action():
    """Factory for _exit_action that returns an ExitAction."""
    def _make_exit_action(ticket, symbol, position_side, volume, reason):
        return ExitAction(
            ticket=ticket,
            symbol=symbol,
            side=position_side,
            volume=volume,
            reason=reason,
        )
    return _make_exit_action


@pytest.fixture
def profit_manager(mock_config, mock_exit_action):
    """Create a ProfitExitManager with mocked dependencies."""
    manager = ProfitExitManager(
        config=mock_config,
        broker=MagicMock(),
        get_min_profit_pips=lambda symbol: 1.0,
        dynamic_buffer=MagicMock(),
        htf_allows_profit_exit=MagicMock(return_value=True),
        pips_to_price=lambda symbol, pips: pips * 0.0001,  # 1 pip = 0.0001 price units
        is_favorable_vs_anchor=MagicMock(return_value=True),
        exit_action=mock_exit_action,
    )
    return manager




def test_check_exit_on_candle_close_htf_gating_blocks_exit_when_htf_opposes(
    profit_manager, make_position
):
    """Test that check_exit_on_candle_close respects HTF gating and blocks exit.

    Given:
      - A dict-shaped buy position with profit=0.05
      - A PosState pre-armed at break-even with best_profit=0.10 (same as tick test)
      - An htf_allows_profit_exit that returns False (HTF opposes the exit)
      - A close_price=1.1000

    When:
      - check_exit_on_candle_close() is called
      - The profit (0.05) would normally breach from 0.10 peak by 0.05
      - But HTF bias blocks the exit

    Then:
      - It returns None (exit is blocked by HTF gating)
    """
    position = make_position(
        as_dict=True,
        symbol="EURUSD",
        type=0,  # buy
        ticket=1,
        volume=0.01,
        price_open=1.1000,
        profit=0.05,
    )

    # Pre-armed state with best_profit=0.10 (would trigger breach exit without HTF gating)
    state = PosState(
        anchor=1.1000,
        prev_price=1.1000,
        ticks_seen=5,
    )
    state.be_armed = True
    state.best_profit = 0.10

    # Mock htf_allows_profit_exit to return False (HTF blocks the exit)
    profit_manager._htf_allows_profit_exit = MagicMock(return_value=False)

    result = profit_manager.check_exit_on_candle_close(position, 1.1000, state)

    # Should return None because HTF gating blocks the exit
    assert result is None
    # Verify that htf_allows_profit_exit was called with correct args
    profit_manager._htf_allows_profit_exit.assert_called_once_with(
        symbol="EURUSD", position_side="buy"
    )
