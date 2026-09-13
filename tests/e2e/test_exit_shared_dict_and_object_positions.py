"""E2E coverage for Subphase 5.1 (project-refactor-sweep):

`app/exit_strategies/exit_shared.py`'s six position-field accessors
(`pos_symbol`, `pos_side`, `pos_ticket`, `pos_entry`, `pos_volume`,
`pos_profit`) plus `get_any()`/`is_break_even()` must behave identically
whether `position` is a dict (as MT5 sometimes represents it, or in
demo/backtest mode) or an object (a real MT5 position object, mirrored by
the `FakePosition` test double).

This subphase is test-only -- these are pure, side-effect-free functions
with no I/O and no real "full stack" to exercise, so an artificially
manufactured e2e scenario around them wouldn't prove anything a thorough
parametrized unit test (owned by `developer`, in
`tests/exit_strategies/test_exit_shared.py`) doesn't already cover more
directly and more exhaustively. Per this subphase's own instructions, the
right e2e-level check here is a lighter-weight one: prove that the *real*,
wired-together exit-management code that actually calls these accessors
(`ExitTrade` composing the real `LossExitManager`/`ProfitExitManager`)
handles a dict-shaped position and an object-shaped position identically,
end to end -- i.e. that the dict/object-agnostic contract the unit tests
verify at the function level is also honored the moment these accessors
are wired into real decision-making code, not just in isolation.

Only MT5 itself is mocked (`mock_mt5`); `ExitTrade`, `LossExitManager`, and
`ProfitExitManager` are the real, unmodified classes from `app/`.
"""

from unittest.mock import MagicMock

from app.exit_strategies.exit_trade import ExitTrade


def _make_broker(position):
    """A minimal stub broker exposing just what ExitTrade.on_tick needs:
    `get_open_positions()` returning our single test position. No MT5, no
    network, no real broker calls anywhere in this path.
    """
    broker = MagicMock()
    broker.get_open_positions.return_value = [position]
    # get_atr / get_pip_size are optional (checked via getattr/callable in
    # ExitTrade); leave them absent so ExitTrade falls back to its own
    # pip-size defaults -- irrelevant to the loss-exit path under test.
    del broker.get_atr
    del broker.get_pip_size
    return broker


def test_loss_manager_exits_identically_for_dict_and_object_buy_position(
    make_position, make_tick, mock_mt5
):
    """A buy position deep in loss (profit <= -5) must trigger the same
    'profit_drop' exit from the real LossExitManager (via real
    ExitTrade.on_tick), whether the position is dict-shaped or
    object-shaped -- proving pos_symbol/pos_side/pos_ticket/pos_entry/
    pos_volume/pos_profit are all read identically by real, wired code.
    """
    tick = make_tick()

    dict_position = make_position(
        as_dict=True,
        ticket=101,
        symbol="EURUSD",
        type=0,  # buy
        volume=0.02,
        price_open=1.1000,
        profit=-10.0,
    )
    object_position = make_position(
        as_dict=False,
        ticket=101,
        symbol="EURUSD",
        type=0,  # buy
        volume=0.02,
        price_open=1.1000,
        profit=-10.0,
    )

    dict_trade = ExitTrade(broker=_make_broker(dict_position), risk_manager=MagicMock())
    object_trade = ExitTrade(
        broker=_make_broker(object_position), risk_manager=MagicMock()
    )

    dict_actions = dict_trade.on_tick(tick)
    object_actions = object_trade.on_tick(tick)

    assert len(dict_actions) == 1
    assert len(object_actions) == 1

    dict_action, object_action = dict_actions[0], object_actions[0]

    # Same reason, same ticket/symbol, same closing side (opposite of the
    # position's buy side), same volume -- identical regardless of shape.
    assert dict_action.reason == object_action.reason == "profit_drop"
    assert dict_action.ticket == object_action.ticket == 101
    assert dict_action.symbol == object_action.symbol == "EURUSD"
    assert dict_action.side == object_action.side == "sell"
    assert dict_action.volume == object_action.volume == 0.02


def test_loss_manager_exits_identically_for_dict_and_object_sell_position(
    make_position, make_tick, mock_mt5
):
    """Same as above but for a sell position (numeric type=1), which also
    exercises pos_side's numeric MT5 convention (1 -> 'sell') identically
    for both position shapes through the real, wired code path.
    """
    tick = make_tick()

    dict_position = make_position(
        as_dict=True,
        ticket=202,
        symbol="GBPUSD",
        type=1,  # sell
        volume=0.05,
        price_open=1.2500,
        profit=-7.5,
    )
    object_position = make_position(
        as_dict=False,
        ticket=202,
        symbol="GBPUSD",
        type=1,  # sell
        volume=0.05,
        price_open=1.2500,
        profit=-7.5,
    )

    dict_trade = ExitTrade(broker=_make_broker(dict_position), risk_manager=MagicMock())
    object_trade = ExitTrade(
        broker=_make_broker(object_position), risk_manager=MagicMock()
    )

    dict_actions = dict_trade.on_tick(tick)
    object_actions = object_trade.on_tick(tick)

    assert len(dict_actions) == 1
    assert len(object_actions) == 1

    dict_action, object_action = dict_actions[0], object_actions[0]

    assert dict_action.reason == object_action.reason == "profit_drop"
    assert dict_action.ticket == object_action.ticket == 202
    assert dict_action.symbol == object_action.symbol == "GBPUSD"
    # Closing side for a sell position is "buy".
    assert dict_action.side == object_action.side == "buy"
    assert dict_action.volume == object_action.volume == 0.05


def test_loss_manager_takes_no_action_identically_when_position_is_healthy(
    make_position, make_tick, mock_mt5
):
    """A healthy, in-profit position (not break-even-armed yet, profit well
    above the -5 drop threshold) must produce no exit action from either
    shape -- i.e. is_break_even()/pos_profit() agreeing across shapes also
    means "don't exit" is consistent, not just "do exit".
    """
    tick = make_tick()

    dict_position = make_position(
        as_dict=True,
        ticket=303,
        symbol="USDJPY",
        type=0,
        volume=0.01,
        price_open=150.00,
        profit=1.0,
    )
    object_position = make_position(
        as_dict=False,
        ticket=303,
        symbol="USDJPY",
        type=0,
        volume=0.01,
        price_open=150.00,
        profit=1.0,
    )

    dict_trade = ExitTrade(broker=_make_broker(dict_position), risk_manager=MagicMock())
    object_trade = ExitTrade(
        broker=_make_broker(object_position), risk_manager=MagicMock()
    )

    assert dict_trade.on_tick(tick) == []
    assert object_trade.on_tick(tick) == []


def test_no_real_mt5_calls_made(make_position, make_tick, mock_mt5):
    """Sanity guard: exercising the real ExitTrade/LossExitManager path
    above never touches a real MT5 terminal -- only the mock_mt5 stub.
    """
    tick = make_tick()
    position = make_position(
        as_dict=True,
        ticket=404,
        symbol="EURUSD",
        type=0,
        volume=0.01,
        price_open=1.1000,
        profit=-10.0,
    )
    trade = ExitTrade(broker=_make_broker(position), risk_manager=MagicMock())
    trade.on_tick(tick)

    # ExitTrade only reaches into MT5 (mt5.symbol_info) from the profit-exit
    # path's _pips_to_price fallback, which this loss-exit scenario never
    # reaches; asserting mock_mt5 wasn't unexpectedly hit for real confirms
    # this test suite stays at the MT5 boundary described in CLAUDE.md.
    assert not mock_mt5.initialize.called
