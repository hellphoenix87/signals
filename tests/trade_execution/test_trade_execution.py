"""Tests for app/trade_execution/trade_execution.py."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.config.settings import Config
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


class TestSpreadGate:
    """Tests for `TradeExecutor._spread_ok`.

    `Config.MAX_SPREAD_POINTS` went live in PR #66/#70 with no test coverage at
    all. The backtest exercises its own `--max-entry-spread-pips` check against
    the entry tick, not this code path, so nothing verified that the live gate
    reads the tick and point size correctly or that it fails open the way its
    docstring promises.
    """

    @staticmethod
    def _executor(mock_broker, *, point_size=0.00001, bid=1.1000, ask=1.1002):
        """A TradeExecutor whose market data returns one tick at a given spread."""
        market_data = MagicMock()
        market_data.get_symbol_tick.return_value = SimpleNamespace(bid=bid, ask=ask)
        mock_broker.get_point_size.return_value = point_size
        return TradeExecutor(
            risk_manager=MagicMock(), broker=mock_broker, market_data=market_data
        )

    def test_gate_disabled_at_zero_fails_open_without_fetching_a_tick(
        self, mock_broker, monkeypatch
    ):
        """`0` is this codebase's "disabled" convention -- the gate must return
        True immediately, before spending a tick fetch on it."""
        monkeypatch.setattr(Config, "MAX_SPREAD_POINTS", 0, raising=False)
        executor = self._executor(mock_broker)

        assert executor._spread_ok("EURUSD") is True
        executor.market_data.get_symbol_tick.assert_not_called()

    def test_spread_above_threshold_is_blocked(self, mock_broker, monkeypatch):
        """20 points of spread against a 10-point cap must be refused."""
        monkeypatch.setattr(Config, "MAX_SPREAD_POINTS", 10, raising=False)
        # 0.00020 / 0.00001 = 20 points
        executor = self._executor(mock_broker, bid=1.10000, ask=1.10020)

        assert executor._spread_ok("EURUSD") is False

    def test_spread_below_threshold_is_allowed(self, mock_broker, monkeypatch):
        """5 points of spread against a 10-point cap must be allowed."""
        monkeypatch.setattr(Config, "MAX_SPREAD_POINTS", 10, raising=False)
        # 0.00005 / 0.00001 = 5 points
        executor = self._executor(mock_broker, bid=1.10000, ask=1.10005)

        assert executor._spread_ok("EURUSD") is True

    def test_spread_exactly_at_threshold_is_allowed(self, mock_broker, monkeypatch):
        """`_spread_ok` compares with `<=`, so the boundary is inclusive.
        Pinned explicitly because an off-by-one here silently changes how many
        entries the live gate rejects."""
        monkeypatch.setattr(Config, "MAX_SPREAD_POINTS", 10, raising=False)
        # 0.00010 / 0.00001 = exactly 10 points
        executor = self._executor(mock_broker, bid=1.10000, ask=1.10010)

        assert executor._spread_ok("EURUSD") is True

    def test_missing_tick_fails_open(self, mock_broker, monkeypatch):
        """A transient data gap must not block trading -- the gate fails open."""
        monkeypatch.setattr(Config, "MAX_SPREAD_POINTS", 10, raising=False)
        executor = self._executor(mock_broker)
        executor.market_data.get_symbol_tick.return_value = None

        assert executor._spread_ok("EURUSD") is True

    def test_missing_point_size_fails_open(self, mock_broker, monkeypatch):
        """Same for an unresolvable point size -- dividing by it would raise."""
        monkeypatch.setattr(Config, "MAX_SPREAD_POINTS", 10, raising=False)
        executor = self._executor(mock_broker, point_size=0.0)

        assert executor._spread_ok("EURUSD") is True

    def test_blocked_signal_never_reaches_the_broker(self, mock_broker, monkeypatch):
        """End-to-end through `execute_signals`: a symbol whose spread is too
        wide must not produce an order. This is the behavior PR #66 shipped and
        nothing verified."""
        monkeypatch.setattr(Config, "MAX_SPREAD_POINTS", 10, raising=False)
        monkeypatch.setattr(Config, "DAILY_TARGET_PROFIT", 0, raising=False)
        monkeypatch.setattr(Config, "DAILY_MAX_LOSS", 0, raising=False)
        # 0.00050 / 0.00001 = 50 points, well over the cap
        executor = self._executor(mock_broker, bid=1.10000, ask=1.10050)

        executor.execute_signals(
            [{"symbol": "EURUSD", "final_signal": "buy", "price": 1.10000}]
        )

        mock_broker.place_buy.assert_not_called()
        mock_broker.place_sell.assert_not_called()

    def test_allowed_signal_does_reach_the_broker(self, mock_broker, monkeypatch):
        """The negative case above only means something if the same signal
        genuinely places an order once the spread is inside the cap."""
        monkeypatch.setattr(Config, "MAX_SPREAD_POINTS", 10, raising=False)
        monkeypatch.setattr(Config, "DAILY_TARGET_PROFIT", 0, raising=False)
        monkeypatch.setattr(Config, "DAILY_MAX_LOSS", 0, raising=False)
        # 0.00002 / 0.00001 = 2 points, comfortably inside the cap
        executor = self._executor(mock_broker, bid=1.10000, ask=1.10002)
        mock_broker.calculate_sl_tp_prices.return_value = (1.0995, 1.1050)

        executor.execute_signals(
            [
                {
                    "symbol": "EURUSD",
                    "final_signal": "buy",
                    "price": 1.10000,
                    "lot": 0.01,
                }
            ]
        )

        mock_broker.place_buy.assert_called_once()
