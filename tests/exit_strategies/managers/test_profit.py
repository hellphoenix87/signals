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


def test_profit_exit_manager_reads_dict_position_profit_and_exits_on_breach(
    profit_manager, make_position, make_tick
):
    """Test that ProfitExitManager reads profit from dict position and exits on trailing breach.

    Given:
      - A dict-shaped position with profit=0.05
      - A PosState pre-armed at break-even with best_profit=0.10 (tracking peak profit)
      - A tick with bid=1.1000

    When:
      - check_exit_on_tick() is called
      - The profit (0.05) has breached below the best_profit (0.10) by 0.05
      - This breach exceeds the 0.04 threshold

    Then:
      - It returns an ExitAction with reason="trailing_breach_gt_5c"
      - This proves dict position profit is read correctly via pos_profit()
        instead of silently reading None and defaulting to 0.0
    """
    position = make_position(
        as_dict=True,
        symbol="EURUSD",
        type=0,  # buy
        ticket=1,
        volume=0.01,
        price_open=1.1000,
        profit=0.05,  # Current profit: 0.05
    )

    tick = make_tick(as_dict=True, bid=1.1000, ask=1.1002)

    # Pre-armed state tracking that best profit seen was 0.10
    state = PosState(
        anchor=1.1000,
        prev_price=1.1000,
        ticks_seen=5,
    )
    state.be_armed = True  # Already armed at break-even
    state.best_profit = 0.10  # Peak profit seen: 0.10

    result = profit_manager.check_exit_on_tick(position, tick, state)

    # Should exit because 0.05 breach from 0.10 peak exceeds 0.04 threshold
    assert result is not None
    assert isinstance(result, ExitAction)
    assert result.reason == "trailing_breach_gt_5c"
    assert result.ticket == 1
    assert result.symbol == "EURUSD"
    assert result.side == "buy"
    assert result.volume == 0.01
