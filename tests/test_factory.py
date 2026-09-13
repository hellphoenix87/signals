"""Tests for factory getter functions."""

import pytest


def test_get_market_data_returns_singleton():
    """get_market_data() returns the exact md singleton."""
    from app import factory

    result = factory.get_market_data()
    assert result is factory.md


def test_get_broker_returns_singleton():
    """get_broker() returns the exact br singleton."""
    from app import factory

    result = factory.get_broker()
    assert result is factory.br


def test_get_trade_executor_returns_singleton():
    """get_trade_executor() returns the exact trade_executor singleton."""
    from app import factory

    result = factory.get_trade_executor()
    assert result is factory.trade_executor


def test_get_orchestrators_returns_singleton():
    """get_orchestrators() returns the exact orchestrators singleton."""
    from app import factory

    result = factory.get_orchestrators()
    assert result is factory.orchestrators
