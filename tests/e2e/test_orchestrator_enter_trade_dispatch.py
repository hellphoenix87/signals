"""E2E coverage for Subphase 3.1 (project-refactor-sweep):

`SignalOrchestrator` must actually invoke the real `EnterTrade.enter_trade`
method when its entry logic decides to enter a trade, instead of guessing at
nonexistent attribute names (`on_signal` / `execute` / `enter`) that don't
exist on the real `EnterTrade` class (app/trade_execution/helpers/prepare_trade.py).

Pre-fix, `SignalOrchestrator._on_tick`/`_run_entries` look for one of those
guessed attributes on `self.enter_trade`, find nothing callable, and silently
do nothing -- so a real, wired-together `EnterTrade` -> `RiskManager` ->
`Broker` -> `TradeExecutor` chain never sees a single call and no trade is
ever placed, even when the signal generator reports a genuine "buy"/"sell"
entry signal.

This drives the real `SignalOrchestrator`, real `EnterTrade`, real
`RiskManager`, real `TradeExecutor`, and real `Broker` (via `create_broker`,
`TradingMode.DEMO`) end-to-end. Only MT5 itself is mocked at the boundary
(`mock_mt5` fixture from tests/conftest.py, plus a local `mt5.symbol_info`
stub since none of the app code paths exercised here need real MT5 symbol
metadata) -- nothing else here is faked.
"""

from unittest.mock import MagicMock

import pytest

from app.risk.risk_manager import create_risk_manager
from app.services.trade_services import create_orchestrator
from app.trade_execution.broker import create_broker
from app.trade_execution.helpers.prepare_trade import create_enter_trade
from app.trade_execution.mode import TradingMode
from app.trade_execution.trade_execution import create_trade_executor


class _FakeSymbolInfo:
    """Minimal stand-in for mt5.symbol_info()'s return value.

    Only the fields Broker/RiskManager actually read are populated.
    """

    def __init__(self):
        self.point = 0.00001
        self.digits = 5
        self.volume_min = 0.01
        self.volume_max = 100.0
        self.volume_step = 0.01
        self.trade_stops_level = 0
        self.trade_contract_size = 100000.0


def _stub_symbol_info(mock_mt5, monkeypatch):
    """mock_mt5 (tests/conftest.py) doesn't patch mt5.symbol_info -- Broker's
    pip/point/min-stop helpers and RiskManager.calculate_lot_size all call it
    directly, so real end-to-end execution needs it stubbed too."""
    import MetaTrader5 as mt5

    monkeypatch.setattr(mt5, "symbol_info", lambda symbol: _FakeSymbolInfo())


class _StubSignalGeneratorTick:
    """Fake signal_generator exposing only what the tick path (_on_tick) uses:
    get_confirmed_signal(). No trading_service is wired, so a genuine buy
    signal here should reach self.enter_trade directly.
    """

    def __init__(self, confirmed_signal):
        self._confirmed_signal = confirmed_signal
        self.calls = 0

    def get_confirmed_signal(self):
        self.calls += 1
        return self._confirmed_signal


class _StubSignalGeneratorCandle:
    """Fake signal_generator exposing only what the candle path (_run_entries)
    uses: generate_signal(snapshot)."""

    def __init__(self, signal_dict):
        self._signal_dict = signal_dict

    def generate_signal(self, snapshot):
        return [self._signal_dict]


def _build_real_entry_chain(mock_mt5, make_tick):
    """Wires a real Broker (DEMO mode) -> RiskManager -> TradeExecutor ->
    EnterTrade chain, matching app/factory.py's composition (minus market_data,
    which EnterTrade.enter_trade() itself never touches)."""
    mock_mt5.symbol_info_tick.return_value = make_tick(bid=1.0998, ask=1.1000)

    broker = create_broker(TradingMode.DEMO)
    risk_manager = create_risk_manager(broker)
    trade_executor = create_trade_executor(risk_manager, broker, market_data=MagicMock())
    enter_trade = create_enter_trade(
        market_data=MagicMock(),
        risk_manager=risk_manager,
        broker=broker,
        trade_executor=trade_executor,
    )
    return broker, enter_trade


_ENTRY_SIGNAL = {
    "symbol": "EURUSD",
    "final_signal": "buy",
    "direction": "BUY",
    "price": 1.1000,
    "sl_pips": 20,
    "tp_pips": 40,
    "pullback_completed": True,
}


class TestOrchestratorCallsRealEnterTrade:
    def test_tick_driven_entry_signal_reaches_real_broker(
        self, mock_mt5, make_tick, monkeypatch
    ):
        """A confirmed buy signal delivered on the tick path must actually
        result in EnterTrade.enter_trade() running against the real
        RiskManager/Broker/TradeExecutor chain -- not silently vanish because
        the orchestrator guessed at a nonexistent enter_trade attribute name.
        """
        _stub_symbol_info(mock_mt5, monkeypatch)
        broker, enter_trade = _build_real_entry_chain(mock_mt5, make_tick)

        signal_generator = _StubSignalGeneratorTick(dict(_ENTRY_SIGNAL))

        orchestrator = create_orchestrator(
            collector=MagicMock(),
            signal_generator=signal_generator,
            broker=broker,
            trading_service=None,  # force the enter_trade fallback branch
            tick_collector=None,
            exit_trade=None,
            enter_trade=enter_trade,
            logger=None,
        )

        assert broker.get_open_positions() == []

        tick = make_tick(bid=1.0998, ask=1.1000)
        orchestrator._on_tick(tick)

        positions = broker.get_open_positions()
        assert len(positions) == 1, (
            "expected the real EnterTrade -> RiskManager -> Broker chain to "
            "place a trade for a confirmed buy signal on the tick path; got "
            f"{positions!r} -- the orchestrator likely never called a real "
            "method on enter_trade"
        )
        placed = positions[0]
        assert placed["symbol"] == "EURUSD"
        assert placed["direction"] == "BUY"
        assert placed["volume"] > 0

    def test_candle_driven_entry_signal_reaches_real_broker(
        self, mock_mt5, make_tick, monkeypatch
    ):
        """Same regression, exercised via the candle-close entry path
        (_run_entries) instead of the tick path."""
        _stub_symbol_info(mock_mt5, monkeypatch)
        broker, enter_trade = _build_real_entry_chain(mock_mt5, make_tick)

        signal_generator = _StubSignalGeneratorCandle(dict(_ENTRY_SIGNAL))

        orchestrator = create_orchestrator(
            collector=MagicMock(),
            signal_generator=signal_generator,
            broker=broker,
            trading_service=None,
            tick_collector=None,
            exit_trade=None,
            enter_trade=enter_trade,
            logger=None,
        )

        assert broker.get_open_positions() == []

        import datetime

        orchestrator._run_entries(snapshot={}, asof=datetime.datetime.now(datetime.timezone.utc))

        positions = broker.get_open_positions()
        assert len(positions) == 1, (
            "expected the real EnterTrade -> RiskManager -> Broker chain to "
            "place a trade for a confirmed buy signal on the candle path; "
            f"got {positions!r} -- the orchestrator likely never called a "
            "real method on enter_trade"
        )
        placed = positions[0]
        assert placed["symbol"] == "EURUSD"
        assert placed["direction"] == "BUY"
        assert placed["volume"] > 0

    def test_enter_trade_has_no_guessed_attributes(self):
        """Documents the actual, real EnterTrade surface: this subphase's fix
        is only correct because none of the previously-guessed attribute
        names exist on the real class. If this assumption ever changes, the
        orchestrator's dispatch logic (and this test) need revisiting.
        """
        from app.trade_execution.helpers.prepare_trade import EnterTrade

        for guessed_name in ("on_signal", "execute", "enter"):
            assert not hasattr(EnterTrade, guessed_name), (
                f"EnterTrade unexpectedly grew a `{guessed_name}` method -- "
                "the guessing-based dispatch this subphase removed may no "
                "longer be purely dead code"
            )
        assert hasattr(EnterTrade, "enter_trade")
