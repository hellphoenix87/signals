from fastapi import APIRouter

from app.factory import (
    orchestrators,
    signal_orchestrator,  # for backward compatibility
    trade_executor,
    br,
    rm,
    md,
)
from app.utils.backtest_signals import backtest_signals
from app.config.settings import Config
from app.strategies.enter_trade import BreakoutStrategy

router = APIRouter()


def get_any_orchestrator():
    return signal_orchestrator


@router.get("/status")
def get_status():
    running = {sym: orch.is_running() for sym, orch in orchestrators.items()}
    return {
        "orchestrator_running": running,
        "daily_profit": getattr(trade_executor, "daily_profit", None),
        "last_reset": getattr(trade_executor, "last_reset", None),
        "active_symbols": list(getattr(Config, "SYMBOLS", ["EURUSD"])),
    }


@router.post("/trading/start")
def trading_start():
    for orch in orchestrators.values():
        orch.start()
    return {"status": "trading started", "orchestrator_running": True}


@router.post("/trading/stop")
def trading_stop():
    for orch in orchestrators.values():
        orch.stop()
    return {"status": "trading stopped", "orchestrator_running": False}


@router.get("/signal/latest")
def signal_latest(symbol: str = None):
    orch = orchestrators.get(symbol) if symbol else get_any_orchestrator()
    signal = orch.get_latest_signal()
    return {"signal": signal}


@router.get("/live_signal")
def live_signal(symbol: str = None):
    orch = orchestrators.get(symbol) if symbol else get_any_orchestrator()
    signal = orch.get_latest_signal()
    return {"signal": signal}


@router.get("/tick")
def get_tick(symbol: str = None):
    orch = orchestrators.get(symbol) if symbol else get_any_orchestrator()
    tick = orch.get_tick()
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
    for orch in orchestrators.values():
        orch.stop()
    return {"status": "orchestrator stopped", "orchestrator_running": False}
