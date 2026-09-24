"""Tests for app/exit_strategies/managers/loss.py."""

from unittest.mock import MagicMock

import pytest

from app.exit_strategies.exit_shared import ExitAction, PosState
from app.exit_strategies.managers.loss import LossExitManager


@pytest.fixture
def mock_config():
    """Mock config object for LossExitManager."""
    config = MagicMock()
    # Use the default 20 ticks for arming window in tests
    config.EXIT_BE_ARMING_TICKS = 20
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
def loss_manager(mock_config, mock_exit_action):
    """Create a LossExitManager with mocked dependencies."""
    manager = LossExitManager(
        config=mock_config,
        broker=MagicMock(),
        risk_manager=MagicMock(),
        get_min_profit_pips=lambda symbol: 1.0,
        pips_to_price=lambda symbol, pips: pips * 0.0001,
        exit_action=mock_exit_action,
    )
    return manager


def test_loss_exit_manager_reads_dict_position_profit_and_exits_on_drop(
    loss_manager, make_position, make_tick
):
    """Test that LossExitManager reads profit from dict position and exits on -5 drop.

    Given:
      - A dict-shaped position with profit=-6.0 (below the -5 threshold)
      - A fresh PosState (not yet armed)
      - A tick

    When:
      - check_exit_on_tick() is called
      - Profit is -6.0, which is <= the -5.0 drop_profit threshold

    Then:
      - It returns an ExitAction with reason="profit_drop"
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
        profit=-6.0,  # Below -5 threshold
    )

    tick = make_tick(as_dict=True, bid=1.0900, ask=1.0902)
    state = PosState(anchor=1.1000, prev_price=1.0900)

    result = loss_manager.check_exit_on_tick(position, tick, state)

    # Should exit because profit (-6.0) <= drop_profit (-5.0)
    assert result is not None
    assert isinstance(result, ExitAction)
    assert result.reason == "profit_drop"
    assert result.ticket == 1
    assert result.symbol == "EURUSD"
    assert result.side == "buy"
    assert result.volume == 0.01
