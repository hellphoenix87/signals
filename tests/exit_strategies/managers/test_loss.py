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


def test_loss_exit_manager_exits_after_arming_window_expires_without_be(
    loss_manager, make_position, make_tick
):
    """Test that LossExitManager exits when arming window expires without reaching BE.

    Given:
      - A position with profit=-1.0 (never reaches break-even, never drops to -5)
      - A fresh PosState
      - A tick
      - be_arming_ticks is 20

    When:
      - check_exit_on_tick() is called 20 times consecutively with the same loss
      - Each call maintains profit=-1.0 (above -5 threshold, below 0)
      - is_break_even() remains False throughout

    Then:
      - Calls 1-20 return None (still within arming window)
      - Call 21 returns an ExitAction with reason="failed_to_reach_be"
      - This proves the safety net fires once the window is exhausted
    """
    position = make_position(
        as_dict=True,
        symbol="EURUSD",
        type=0,  # buy
        ticket=1,
        volume=0.01,
        price_open=1.1000,
        profit=-1.0,  # Always below break-even, above -5 threshold
    )

    tick = make_tick(as_dict=True, bid=1.0990, ask=1.0992)
    state = PosState(anchor=1.1000, prev_price=1.0990)

    # Drive through the arming window: ticks 1-20 should return None
    for tick_num in range(1, 21):
        result = loss_manager.check_exit_on_tick(position, tick, state)
        assert result is None, f"Expected None at tick {tick_num}, got {result}"
        assert state.be_arming_ticks == tick_num, (
            f"Expected be_arming_ticks={tick_num}, got {state.be_arming_ticks}"
        )
        assert state.be_armed is False, f"Unexpected be_armed=True at tick {tick_num}"

    # Verify we're at the boundary (20 ticks completed, arming window exhausted)
    assert state.be_arming_ticks == 20
    assert state.be_armed is False

    # Call 21: should exit with failed_to_reach_be
    result = loss_manager.check_exit_on_tick(position, tick, state)

    assert result is not None
    assert isinstance(result, ExitAction)
    assert result.reason == "failed_to_reach_be"
    assert result.ticket == 1
    assert result.symbol == "EURUSD"
    assert result.side == "buy"
    assert result.volume == 0.01


def test_loss_exit_manager_arms_instead_of_exiting_if_be_reached_on_deciding_tick(
    loss_manager, make_position, make_tick
):
    """A position that reaches break-even on the exact tick the arming window
    expires should arm for trailing-profit management, not be force-closed as
    failed_to_reach_be (pr-review finding: hoisting the safety net out of block 1
    into its own top-level check otherwise loses the original ordering, where BE
    was checked before deciding to exit).

    Given:
      - A position with profit=-1.0 for ticks 1-20 (never reaches BE, never
        drops to -5), then profit=0.0 (break-even) on tick 21 -- the exact tick
        the arming window expires.

    When:
      - check_exit_on_tick() is called 20 times with profit=-1.0, then once
        more with profit=0.0.

    Then:
      - Calls 1-20 return None as before.
      - Call 21 (profit=0.0, be_arming_ticks already at the limit) arms
        (state.be_armed becomes True) and returns None, instead of returning
        failed_to_reach_be.
    """
    losing_position = make_position(
        as_dict=True,
        symbol="EURUSD",
        type=0,
        ticket=1,
        volume=0.01,
        price_open=1.1000,
        profit=-1.0,
    )
    breakeven_position = make_position(
        as_dict=True,
        symbol="EURUSD",
        type=0,
        ticket=1,
        volume=0.01,
        price_open=1.1000,
        profit=0.0,
    )

    tick = make_tick(as_dict=True, bid=1.0990, ask=1.0992)
    state = PosState(anchor=1.1000, prev_price=1.0990)

    for _ in range(20):
        result = loss_manager.check_exit_on_tick(losing_position, tick, state)
        assert result is None

    assert state.be_arming_ticks == 20
    assert state.be_armed is False

    result = loss_manager.check_exit_on_tick(breakeven_position, tick, state)

    assert result is None
    assert state.be_armed is True
