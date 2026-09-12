"""Tests for app/exit_strategies/exit_shared.py."""

from app.exit_strategies.exit_shared import is_break_even


def test_is_break_even_dict_negative_profit():
    """Test that is_break_even() returns False for dict position with negative profit.

    Given:
      - A dict-shaped position with profit=-5.0

    When:
      - is_break_even(position) is called

    Then:
      - It returns False (position is in loss, not break-even or better)
    """
    position = {"profit": -5.0}
    assert is_break_even(position) is False


def test_is_break_even_dict_zero_profit():
    """Test that is_break_even() returns True for dict position with zero profit.

    Given:
      - A dict-shaped position with profit=0.0

    When:
      - is_break_even(position) is called

    Then:
      - It returns True (position is exactly at break-even)
    """
    position = {"profit": 0.0}
    assert is_break_even(position) is True


def test_is_break_even_dict_positive_profit():
    """Test that is_break_even() returns True for dict position with positive profit.

    Given:
      - A dict-shaped position with profit=5.0

    When:
      - is_break_even(position) is called

    Then:
      - It returns True (position is in profit, which is "break-even or better")
    """
    position = {"profit": 5.0}
    assert is_break_even(position) is True


def test_is_break_even_object_negative_profit(make_position):
    """Test that is_break_even() returns False for object position with negative profit.

    Given:
      - An object-shaped position (from make_position fixture) with profit=-5.0

    When:
      - is_break_even(position) is called

    Then:
      - It returns False (position is in loss, not break-even or better)
    """
    position = make_position(profit=-5.0)
    assert is_break_even(position) is False


def test_is_break_even_dict_missing_profit():
    """Test that is_break_even() returns True for dict position with no profit key.

    Given:
      - A dict-shaped position with no profit key at all

    When:
      - is_break_even(position) is called

    Then:
      - It returns True (preserves current "missing profit defaults to break-even" behavior)
    """
    position = {}
    assert is_break_even(position) is True
