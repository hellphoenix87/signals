"""Shared fixtures for mocking MT5, the broker, and tick/position data.

Positions and ticks in this codebase can be either dicts or MT5-style
objects depending on call site (see app/exit_strategies/exit_shared.py's
get_any()). The make_tick/make_position factories below can produce either
shape so tests can cover both.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_mt5(monkeypatch):
    """Patch the MetaTrader5 functions app code calls, so tests never touch a real terminal."""
    import MetaTrader5 as mt5

    stub = MagicMock()
    stub.initialize.return_value = True
    stub.shutdown.return_value = None
    stub.symbol_select.return_value = True
    stub.symbol_info_tick.return_value = None
    stub.positions_get.return_value = ()
    stub.last_error.return_value = (0, "no error")

    for name in (
        "initialize",
        "shutdown",
        "symbol_select",
        "symbol_info_tick",
        "positions_get",
        "last_error",
        "order_send",
        "copy_rates_from_pos",
    ):
        if hasattr(mt5, name):
            monkeypatch.setattr(mt5, name, getattr(stub, name))

    return stub


class FakeTick:
    """Object-style tick, mirroring MT5's tick object."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakePosition:
    """Object-style position, mirroring MT5's position object."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def make_tick():
    """Factory for a tick as either a dict or an object."""

    def _make(as_dict: bool = False, **overrides):
        fields = {"bid": 1.1000, "ask": 1.1002, "last": 1.1001, "spread": 2}
        fields.update(overrides)
        return fields if as_dict else FakeTick(**fields)

    return _make


@pytest.fixture
def make_position():
    """Factory for a position as either a dict or an object."""

    def _make(as_dict: bool = False, **overrides):
        fields = {
            "ticket": 1,
            "symbol": "EURUSD",
            "type": 0,  # 0 = buy, 1 = sell (MT5 convention)
            "volume": 0.01,
            "price_open": 1.1000,
            "profit": 0.0,
        }
        fields.update(overrides)
        return fields if as_dict else FakePosition(**fields)

    return _make


@pytest.fixture
def mock_broker():
    """A stub Broker covering the methods/attrs app code accesses via getattr()."""
    broker = MagicMock()
    broker.mode = MagicMock(DEMO="demo", LIVE="live", BACKTEST="backtest")
    broker.open_positions_sim = {}
    broker.place_market_order.return_value = None
    broker.close_position.return_value = None
    return broker
