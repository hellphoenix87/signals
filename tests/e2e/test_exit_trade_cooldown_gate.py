"""E2E coverage for Subphase 5.2 (project-refactor-sweep):

`ExitTrade.on_tick` (app/exit_strategies/exit_trade.py) runs the real
`LossExitManager`/`ProfitExitManager` protective checks for every open
position on every tick, but gates each ticket through `_should_exit(ticket)`
first -- a per-ticket cooldown (`Config.EXIT_COOLDOWN_SECONDS`, default 2.0s)
that suppresses a second exit-action check for the same ticket if `on_tick`
is called again within the cooldown window. This is a genuinely useful
safety behavior: without it, a losing position would re-emit (and
potentially re-trigger a duplicate close attempt for) the exact same exit
action on every single tick until the position actually closes.

This test drives a real `ExitTrade` wired with a real `Broker`
(`create_broker(TradingMode.DEMO)`) and a real `RiskManager`
(`create_risk_manager`), rather than the unit-level test's bare
`MagicMock()` collaborators -- so the cooldown gate is proven against
something closer to the real broker/position-fetching path
(`Broker.get_open_positions()` reading its own `open_positions_sim` store,
same as `tests/e2e/test_broker_simulated_positions.py`), not just a mock
object handed a canned position list. Only MT5 itself is mocked at the
boundary (`mock_mt5` fixture from tests/conftest.py).

Scenario: open one real demo position via `broker.place_buy(...)`, drive its
profit to -6.0 (<= LossExitManager's -5 "profit_drop" threshold, so it
exits on the very first tick with no arming/pending state needed), then call
`exit_trade.on_tick(tick)` twice back-to-back with no sleep in between --
both calls land well within the default 2-second cooldown window. The first
call must return a real "profit_drop" exit action for the ticket; the very
next call, for the same ticket, must return an empty list -- proving the
cooldown genuinely suppresses a duplicate/spammy exit signal for the same
position on the immediately-following tick, rather than re-emitting the same
close instruction every tick.
"""

from __future__ import annotations

from app.exit_strategies.exit_shared import pos_ticket
from app.exit_strategies.exit_trade import ExitTrade
from app.risk.risk_manager import create_risk_manager
from app.trade_execution.broker import create_broker
from app.trade_execution.mode import TradingMode


def _stub_tick_price(mock_mt5, make_tick, bid=1.1000, ask=1.1002):
    """Broker._simulate_trade calls mt5.symbol_info_tick(symbol) to price a
    demo trade; without this it silently falls back to price=1.0. Mirrors
    tests/e2e/test_broker_simulated_positions.py's helper of the same name.
    """
    mock_mt5.symbol_info_tick.return_value = make_tick(bid=bid, ask=ask)


class TestExitTradeCooldownGate:
    def test_second_immediate_tick_for_same_ticket_is_suppressed_by_cooldown(
        self, mock_mt5, make_tick
    ):
        _stub_tick_price(mock_mt5, make_tick)
        broker = create_broker(TradingMode.DEMO)
        risk_manager = create_risk_manager(broker)

        broker.place_buy("EURUSD", 0.05, sl=1.0950, tp=1.1100)
        position = broker.get_open_positions()[0]
        ticket = pos_ticket(position)
        assert ticket is not None

        # Drive the position into a genuine, instant "profit_drop" exit
        # condition (<= LossExitManager's -5 threshold during the BE-arming
        # window) -- same technique test_broker_simulated_positions.py uses
        # to reach real exit-management logic for a demo-mode position.
        position["profit"] = -6.0

        exit_trade = ExitTrade(broker=broker, risk_manager=risk_manager)
        tick = make_tick(bid=1.0900, ask=1.0902)

        first_actions = exit_trade.on_tick(tick)
        assert len(first_actions) == 1, (
            "expected the real LossExitManager to produce a genuine "
            "'profit_drop' exit action on the first tick for a position "
            f"already at -6.0 profit; got {first_actions!r}"
        )
        first_action = first_actions[0]
        assert first_action.ticket == ticket
        assert first_action.symbol == "EURUSD"
        assert first_action.reason == "profit_drop"

        # Same ticket, called again immediately (no sleep) -- well within
        # the default 2.0s EXIT_COOLDOWN_SECONDS window. The cooldown gate
        # in _should_exit() must suppress a second check for this ticket,
        # so on_tick must return nothing at all for it -- not a duplicate
        # "profit_drop" action, and not any other exit reason either.
        second_actions = exit_trade.on_tick(tick)
        assert second_actions == [], (
            "expected the cooldown gate to suppress a duplicate exit check "
            "for the same ticket on the immediately-following tick; got "
            f"{second_actions!r} -- a real broker/position would otherwise "
            "be re-signaled for close on every single tick"
        )

    def test_cooldown_suppression_does_not_leak_across_different_tickets(
        self, mock_mt5, make_tick
    ):
        """Guards against an overly-broad fix (e.g. a single global cooldown
        instead of a per-ticket one): a second, distinct losing position
        opened in the same tick must still get its own real exit action,
        even though another ticket's cooldown was just armed.
        """
        _stub_tick_price(mock_mt5, make_tick)
        broker = create_broker(TradingMode.DEMO)
        risk_manager = create_risk_manager(broker)

        broker.place_buy("EURUSD", 0.05, sl=1.0950, tp=1.1100)
        broker.place_sell("GBPUSD", 0.10, sl=1.1050, tp=1.0900)

        positions = broker.get_open_positions()
        assert len(positions) == 2
        for pos in positions:
            pos["profit"] = -6.0

        first_ticket = pos_ticket(positions[0])
        second_ticket = pos_ticket(positions[1])
        assert first_ticket != second_ticket

        exit_trade = ExitTrade(broker=broker, risk_manager=risk_manager)
        tick = make_tick()

        actions = exit_trade.on_tick(tick)
        tickets_seen = {a.ticket for a in actions}
        assert tickets_seen == {first_ticket, second_ticket}, (
            "expected both distinct losing positions to get their own real "
            f"'profit_drop' exit action on the same tick; got actions={actions!r}"
        )
