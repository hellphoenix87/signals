"""E2E coverage for Subphase 2.5 (project-refactor-sweep):

- Simulated (demo/backtest) positions must get distinct, non-None tickets so
  `Broker.close_position(ticket=...)` can actually match and remove the
  right one.
- The simulated position dict's volume must be stored under a key that
  `app.exit_strategies.exit_shared.pos_volume()` can read (i.e. "volume",
  not "lot"), so dict-shaped demo/backtest positions actually reach real
  exit-management logic (`LossExitManager`/`ProfitExitManager` via
  `ExitTrade.on_tick`) instead of silently bailing out on their
  `volume is None` guard.

This drives the real `Broker` (via `create_broker`) and real `ExitTrade`
(via `create_exit_trade`, composing the real `LossExitManager` /
`ProfitExitManager`) end-to-end. Only MT5 itself is mocked at the boundary
(`mock_mt5` fixture from tests/conftest.py) -- nothing else here is faked.
"""

from unittest.mock import MagicMock

import pytest

from app.exit_strategies.exit_shared import pos_ticket, pos_volume
from app.exit_strategies.exit_trade import create_exit_trade
from app.trade_execution.broker import create_broker
from app.trade_execution.mode import TradingMode


def _stub_tick_price(mock_mt5, make_tick, bid=1.1000, ask=1.1002):
    """Make mt5.symbol_info_tick return a usable object-style tick.

    Broker._simulate_trade/_backtest_trade call mt5.symbol_info_tick(symbol)
    to price the trade; without this it silently falls back to price=1.0,
    which still exercises the ticket/volume behavior under test but this
    keeps prices realistic.
    """
    mock_mt5.symbol_info_tick.return_value = make_tick(bid=bid, ask=ask)


class TestDemoModeSimulatedPositions:
    """Requirement 1: distinct tickets assigned, close_position matches correctly."""

    def test_two_demo_trades_get_distinct_non_none_tickets(self, mock_mt5, make_tick):
        _stub_tick_price(mock_mt5, make_tick)
        broker = create_broker(TradingMode.DEMO)

        broker.place_buy("EURUSD", 0.10, sl=1.0950, tp=1.1100)
        broker.place_sell("EURUSD", 0.20, sl=1.1050, tp=1.0900)

        positions = broker.get_open_positions()
        assert len(positions) == 2

        tickets = [pos_ticket(p) for p in positions]
        assert None not in tickets, "simulated positions must be assigned tickets"
        assert len(set(tickets)) == 2, "each simulated position must get a distinct ticket"

    def test_close_position_by_ticket_removes_only_that_position(
        self, mock_mt5, make_tick
    ):
        _stub_tick_price(mock_mt5, make_tick)
        broker = create_broker(TradingMode.DEMO)

        broker.place_buy("EURUSD", 0.10, sl=1.0950, tp=1.1100)
        broker.place_sell("GBPUSD", 0.20, sl=1.1050, tp=1.0900)

        positions = broker.get_open_positions()
        assert len(positions) == 2
        first_ticket = pos_ticket(positions[0])
        second_ticket = pos_ticket(positions[1])
        assert first_ticket != second_ticket

        closed = broker.close_position(ticket=first_ticket)
        assert closed is True

        remaining = broker.get_open_positions()
        assert len(remaining) == 1
        assert pos_ticket(remaining[0]) == second_ticket
        assert remaining[0]["symbol"] == "GBPUSD"

    def test_simulated_position_volume_is_readable_by_pos_volume(
        self, mock_mt5, make_tick
    ):
        """Direct proof of the lot -> volume key fix: pos_volume() must read
        a real number, not None, from a real simulated position dict."""
        _stub_tick_price(mock_mt5, make_tick)
        broker = create_broker(TradingMode.DEMO)

        broker.place_buy("EURUSD", 0.07, sl=1.0950, tp=1.1100)

        position = broker.get_open_positions()[0]
        assert pos_volume(position) == pytest.approx(0.07)
        assert pos_ticket(position) is not None

    def test_demo_position_reaches_real_loss_exit_logic_via_exit_trade(
        self, mock_mt5, make_tick
    ):
        """Full-stack proof: a real ExitTrade (composing the real
        LossExitManager/ProfitExitManager) must actually evaluate a
        demo-mode position's exit conditions -- not bail out silently
        because volume/ticket couldn't be read -- and produce a real
        exit action once the position is deep enough in loss.
        """
        _stub_tick_price(mock_mt5, make_tick)
        broker = create_broker(TradingMode.DEMO)

        broker.place_buy("EURUSD", 0.05, sl=1.0950, tp=1.1100)
        position = broker.get_open_positions()[0]
        ticket = pos_ticket(position)
        assert ticket is not None

        # Drive the position deep into loss (below LossExitManager's
        # -5 "profit_drop" threshold during the BE-arming window) --
        # if volume/ticket were still unreadable, ExitTrade.on_tick would
        # either skip the position entirely (ticket is None -> `continue`)
        # or the manager would bail on its `volume is None` guard, and no
        # exit action would ever be produced.
        position["profit"] = -10.0

        exit_trade = create_exit_trade(broker=broker, risk_manager=MagicMock())
        tick = make_tick(bid=1.0900, ask=1.0902)

        actions = exit_trade.on_tick(tick)

        assert len(actions) == 1, (
            "expected LossExitManager to produce a real exit action for a "
            "deeply-losing demo position; got none -- volume/ticket likely "
            "still unreadable"
        )
        action = actions[0]
        assert action.ticket == ticket
        assert action.symbol == "EURUSD"
        assert action.side == "sell"  # opposite of the original buy
        assert action.volume == pytest.approx(0.05)
        assert action.reason == "profit_drop"


class TestBacktestModeSimulatedPositions:
    """Same fix, exercised via the backtest code path (_backtest_trade)."""

    def test_two_backtest_trades_get_distinct_tickets_and_readable_volume(
        self, mock_mt5, make_tick
    ):
        _stub_tick_price(mock_mt5, make_tick)
        broker = create_broker(TradingMode.BACKTEST)

        broker.place_buy("EURUSD", 0.10, sl=1.0950, tp=1.1100, price=1.1000)
        broker.place_sell("EURUSD", 0.15, sl=1.1050, tp=1.0900, price=1.1000)

        positions = broker.get_open_positions()
        assert len(positions) == 2

        tickets = [pos_ticket(p) for p in positions]
        assert None not in tickets
        assert len(set(tickets)) == 2

        volumes = [pos_volume(p) for p in positions]
        assert None not in volumes, "backtest positions' volume must be readable"
        assert sorted(volumes) == pytest.approx([0.10, 0.15])

    def test_backtest_close_position_by_ticket_removes_only_that_position(
        self, mock_mt5, make_tick
    ):
        _stub_tick_price(mock_mt5, make_tick)
        broker = create_broker(TradingMode.BACKTEST)

        broker.place_buy("EURUSD", 0.10, sl=1.0950, tp=1.1100, price=1.1000)
        broker.place_sell("EURUSD", 0.15, sl=1.1050, tp=1.0900, price=1.1000)

        positions = broker.get_open_positions()
        first_ticket = pos_ticket(positions[0])
        second_ticket = pos_ticket(positions[1])

        assert broker.close_position(ticket=first_ticket) is True

        remaining = broker.get_open_positions()
        assert len(remaining) == 1
        assert pos_ticket(remaining[0]) == second_ticket
