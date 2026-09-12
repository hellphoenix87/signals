"""Tests for app/trade_execution/trade_execution.py."""

from unittest.mock import MagicMock

from app.trade_execution.trade_execution import TradeExecutor


def test_close_all_trades_closes_all_open_positions(mock_broker, make_position):
    """Test that close_all_trades() closes all open positions via broker.close_position().

    Given:
      - mock_broker.get_open_positions.return_value is a list of two positions

    When:
      - TradeExecutor.close_all_trades() is called

    Then:
      - mock_broker.close_position is called exactly twice, once with ticket=1 and once with ticket=2
    """
    # Arrange
    mock_broker.get_open_positions.return_value = [
        make_position(as_dict=True, ticket=1),
        make_position(as_dict=True, ticket=2),
    ]

    trade_executor = TradeExecutor(
        risk_manager=MagicMock(),
        broker=mock_broker,
        market_data=MagicMock(),
    )

    # Act
    result = trade_executor.close_all_trades()

    # Assert
    assert mock_broker.close_position.call_count == 2
    mock_broker.close_position.assert_any_call(ticket=1)
    mock_broker.close_position.assert_any_call(ticket=2)
    assert set(result["closed"]) == {1, 2}
    assert result["failed"] == []


def test_close_all_trades_skips_positions_with_no_ticket(mock_broker, make_position):
    """Test that close_all_trades() skips positions where pos_ticket returns None.

    Given:
      - mock_broker.get_open_positions returns a position with ticket=None

    When:
      - TradeExecutor.close_all_trades() is called

    Then:
      - mock_broker.close_position is never called for that position
    """
    # Arrange
    mock_broker.get_open_positions.return_value = [
        make_position(as_dict=True, ticket=None),  # No ticket
    ]

    trade_executor = TradeExecutor(
        risk_manager=MagicMock(),
        broker=mock_broker,
        market_data=MagicMock(),
    )

    # Act
    result = trade_executor.close_all_trades()

    # Assert
    mock_broker.close_position.assert_not_called()
    assert result == {"closed": [], "failed": []}


def test_close_all_trades_continues_after_one_failure(mock_broker, make_position):
    """A failure closing one position must not prevent attempting the rest.

    This is a panic-button endpoint (close ALL positions) -- an exception or a
    False return from broker.close_position() for one ticket should be
    recorded as a failure, not abort the loop and leave every other position
    open.
    """
    mock_broker.get_open_positions.return_value = [
        make_position(as_dict=True, ticket=1),
        make_position(as_dict=True, ticket=2),
        make_position(as_dict=True, ticket=3),
    ]

    def close_position(ticket=None, **_kwargs):
        if ticket == 1:
            raise RuntimeError("MT5 order_send returned None")
        if ticket == 2:
            return False
        return True

    mock_broker.close_position.side_effect = close_position

    trade_executor = TradeExecutor(
        risk_manager=MagicMock(),
        broker=mock_broker,
        market_data=MagicMock(),
    )

    result = trade_executor.close_all_trades()

    # All three tickets were attempted despite ticket=1 raising.
    assert mock_broker.close_position.call_count == 3
    assert result["closed"] == [3]
    assert {f["ticket"] for f in result["failed"]} == {1, 2}
    failure_by_ticket = {f["ticket"]: f["error"] for f in result["failed"]}
    assert "RuntimeError" in failure_by_ticket[1]
    assert failure_by_ticket[2] == "close_position returned False"
