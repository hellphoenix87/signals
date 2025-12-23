from fastapi import APIRouter

from app.factory import (
    signal_orchestrator,
    trade_executor,
    strategy,
    br,
    rm,
    md,
)
from app.utils.backtest_signals import backtest_signals
from app.config.settings import Config
from app.strategies.enter_trade import BreakoutStrategy

router = APIRouter()


@router.get("/status")
def get_status():
    if getattr(br, "mode", None) == br.mode.DEMO:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return {
        "orchestrator_running": signal_orchestrator.is_running(),
        "daily_profit": getattr(trade_executor, "daily_profit", None),
        "last_reset": getattr(trade_executor, "last_reset", None),
        "active_symbols": strategy.get_last_scanned_symbols(),
    }


@router.post("/trading/start")
def trading_start():
    # Explicitly start the background trading loop (and ticks, via orchestrator.start()).
    signal_orchestrator.start()
    return {"status": "trading started", "orchestrator_running": True}


@router.post("/trading/stop")
def trading_stop():
    signal_orchestrator.stop()
    return {"status": "trading stopped", "orchestrator_running": False}


@router.get("/signal/latest")
def signal_latest():
    # Read-only: does not start orchestrator.
    signal = signal_orchestrator.get_latest_signal()
    return {"signal": signal}


@router.get("/live_signal")
def live_signal():
    # Backward-compatible: now read-only (no side-effects).
    signal = signal_orchestrator.get_latest_signal()
    return {"signal": signal}


@router.get("/tick")
def get_tick():
    # Debug/inspection:
    # If orchestrator is running, ticks should already be running. This also ensures ticks if called standalone.
    tick = signal_orchestrator.get_tick()
    return {"tick": str(tick)}


@router.get("/simulated_positions")
def get_simulated_positions():
    if getattr(br, "mode", None) == br.mode.DEMO:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return br.open_positions_sim


@router.post("/close_all")
def close_all_trades():
    trade_executor._close_all_trades()
    return {"status": "all trades closed"}


@router.get("/test_historical")
def test_historical():
    candles = md.get_historical_candles(
        "EURUSD",
        timeframe=Config.TIMEFRAME,
        start_pos=0,
        count=getattr(Config, "CANDLE_COUNT", 500),
    )
    return {"candles": candles}


@router.get("/backtest_signals_historical")
def backtest_signals_endpoint_historical():
    candles = md.get_historical_candles(
        "EURUSD",
        timeframe=Config.TIMEFRAME,
        start_pos=0,
        count=Config.CANDLE_COUNT,
    )
    strategy_instance = BreakoutStrategy(market_data=md, risk_manager=rm, broker=br)
    results = backtest_signals(
        strategy_instance.strong_signal_strategy,
        candles,
        min_window=Config.MIN_CANDLES_FOR_INDICATORS,
    )
    return {"signals": results}


@router.post("/stop_orchestrator")
def stop_orchestrator():
    # Backward-compatible alias for older clients.
    signal_orchestrator.stop()
    return {"status": "orchestrator stopped", "orchestrator_running": False}
