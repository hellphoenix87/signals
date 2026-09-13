"""Tests for app/exit_strategies/exit_shared.py."""

import pytest

from app.exit_strategies.exit_shared import (
    is_break_even,
    pos_entry,
    pos_profit,
    pos_side,
    pos_symbol,
    pos_ticket,
    pos_volume,
)


class FakePosition:
    """Object-style position for testing, mirroring MT5's position object."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


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


# Parametrized tests for accessor functions with both dict and object positions


class TestPosSymbol:
    """Tests for pos_symbol() accessor."""

    @pytest.mark.parametrize("as_dict", [True, False])
    def test_pos_symbol_normal(self, as_dict, make_position):
        """Test pos_symbol() returns correct symbol for both dict and object positions."""
        position = make_position(as_dict=as_dict, symbol="EURUSD")
        assert pos_symbol(position) == "EURUSD"

    def test_pos_symbol_dict_missing_field(self):
        """Test pos_symbol() returns None when symbol field is absent in dict."""
        position = {}
        assert pos_symbol(position) is None

    def test_pos_symbol_object_missing_field(self):
        """Test pos_symbol() returns None when symbol field is absent in object."""
        position = FakePosition()
        assert pos_symbol(position) is None


class TestPosSide:
    """Tests for pos_side() accessor."""

    @pytest.mark.parametrize("as_dict,type_value,expected", [
        (True, 0, "buy"),
        (True, 1, "sell"),
        (False, 0, "buy"),
        (False, 1, "sell"),
    ])
    def test_pos_side_numeric_type(self, as_dict, type_value, expected, make_position):
        """Test pos_side() converts numeric type (0/1) to buy/sell for both dict and object."""
        position = make_position(as_dict=as_dict, type=type_value)
        assert pos_side(position) == expected

    @pytest.mark.parametrize("as_dict,side_value,expected", [
        (True, "buy", "buy"),
        (True, "BUY", "buy"),
        (True, " buy ", "buy"),
        (True, "long", "buy"),
        (True, "LONG", "buy"),
        (True, "sell", "sell"),
        (True, "SELL", "sell"),
        (True, " sell ", "sell"),
        (True, "short", "sell"),
        (True, "SHORT", "sell"),
        (False, "buy", "buy"),
        (False, "BUY", "buy"),
        (False, " buy ", "buy"),
        (False, "long", "buy"),
        (False, "LONG", "buy"),
        (False, "sell", "sell"),
        (False, "SELL", "sell"),
        (False, " sell ", "sell"),
        (False, "short", "sell"),
        (False, "SHORT", "sell"),
    ])
    def test_pos_side_string_values(self, as_dict, side_value, expected):
        """Test pos_side() normalizes string side values for both dict and object."""
        # Construct position directly to avoid 'type' field from make_position
        # which has higher priority than 'side' in pos_side()
        if as_dict:
            position = {"side": side_value}
        else:
            position = FakePosition(side=side_value)
        assert pos_side(position) == expected

    @pytest.mark.parametrize("as_dict,unrecognized", [
        (True, "unknown"),
        (True, "invalid"),
        (False, "unknown"),
        (False, "invalid"),
    ])
    def test_pos_side_unrecognized_string(self, as_dict, unrecognized):
        """Test pos_side() returns None for unrecognized string values."""
        # Construct position directly to avoid 'type' field from make_position
        if as_dict:
            position = {"side": unrecognized}
        else:
            position = FakePosition(side=unrecognized)
        assert pos_side(position) is None

    def test_pos_side_dict_missing_field(self):
        """Test pos_side() returns None when no relevant field is present in dict."""
        position = {}
        assert pos_side(position) is None

    def test_pos_side_object_missing_field(self):
        """Test pos_side() returns None when no relevant field is present in object."""
        position = FakePosition()
        assert pos_side(position) is None


class TestPosTicket:
    """Tests for pos_ticket() accessor."""

    @pytest.mark.parametrize("as_dict", [True, False])
    def test_pos_ticket_normal(self, as_dict, make_position):
        """Test pos_ticket() returns correct ticket for both dict and object positions."""
        position = make_position(as_dict=as_dict, ticket=12345)
        assert pos_ticket(position) == 12345

    def test_pos_ticket_dict_missing_field(self):
        """Test pos_ticket() returns None when ticket field is absent in dict."""
        position = {}
        assert pos_ticket(position) is None

    def test_pos_ticket_object_missing_field(self):
        """Test pos_ticket() returns None when ticket field is absent in object."""
        position = FakePosition()
        assert pos_ticket(position) is None


class TestPosEntry:
    """Tests for pos_entry() accessor."""

    @pytest.mark.parametrize("as_dict", [True, False])
    def test_pos_entry_normal(self, as_dict, make_position):
        """Test pos_entry() returns correct entry price for both dict and object positions."""
        position = make_position(as_dict=as_dict, price_open=1.1234)
        assert pos_entry(position) == 1.1234

    @pytest.mark.parametrize("as_dict,entry_value", [
        (True, "1.1234"),
        (False, "1.1234"),
    ])
    def test_pos_entry_string_conversion(self, as_dict, entry_value, make_position):
        """Test pos_entry() converts string prices to float for both dict and object."""
        position = make_position(as_dict=as_dict, price_open=entry_value)
        assert pos_entry(position) == 1.1234

    def test_pos_entry_dict_missing_field(self):
        """Test pos_entry() returns None when entry field is absent in dict."""
        position = {}
        assert pos_entry(position) is None

    def test_pos_entry_object_missing_field(self):
        """Test pos_entry() returns None when entry field is absent in object."""
        position = FakePosition()
        assert pos_entry(position) is None


class TestPosVolume:
    """Tests for pos_volume() accessor."""

    @pytest.mark.parametrize("as_dict", [True, False])
    def test_pos_volume_normal(self, as_dict, make_position):
        """Test pos_volume() returns correct volume for both dict and object positions."""
        position = make_position(as_dict=as_dict, volume=0.5)
        assert pos_volume(position) == 0.5

    @pytest.mark.parametrize("as_dict,volume_value", [
        (True, "0.5"),
        (False, "0.5"),
    ])
    def test_pos_volume_string_conversion(self, as_dict, volume_value, make_position):
        """Test pos_volume() converts string volumes to float for both dict and object."""
        position = make_position(as_dict=as_dict, volume=volume_value)
        assert pos_volume(position) == 0.5

    def test_pos_volume_dict_missing_field(self):
        """Test pos_volume() returns None when volume field is absent in dict."""
        position = {}
        assert pos_volume(position) is None

    def test_pos_volume_object_missing_field(self):
        """Test pos_volume() returns None when volume field is absent in object."""
        position = FakePosition()
        assert pos_volume(position) is None


class TestPosProfit:
    """Tests for pos_profit() accessor."""

    @pytest.mark.parametrize("as_dict", [True, False])
    def test_pos_profit_normal(self, as_dict, make_position):
        """Test pos_profit() returns correct profit for both dict and object positions."""
        position = make_position(as_dict=as_dict, profit=10.5)
        assert pos_profit(position) == 10.5

    @pytest.mark.parametrize("as_dict,profit_value", [
        (True, "-5.25"),
        (False, "-5.25"),
    ])
    def test_pos_profit_string_conversion(self, as_dict, profit_value, make_position):
        """Test pos_profit() converts string profits to float for both dict and object."""
        position = make_position(as_dict=as_dict, profit=profit_value)
        assert pos_profit(position) == -5.25

    def test_pos_profit_dict_missing_field(self):
        """Test pos_profit() returns None when profit field is absent in dict."""
        position = {}
        assert pos_profit(position) is None

    def test_pos_profit_object_missing_field(self):
        """Test pos_profit() returns None when profit field is absent in object."""
        position = FakePosition()
        assert pos_profit(position) is None
