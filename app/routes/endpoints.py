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
orchestrator_started = False


@router.get("/status")
def get_status():
    if getattr(br, "mode", None) == br.mode.DEMO:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return {
        "daily_profit": getattr(trade_executor, "daily_profit", None),
        "last_reset": getattr(trade_executor, "last_reset", None),
        "active_symbols": strategy.get_last_scanned_symbols(),
    }


@router.get("/tick")
def get_tick():
    print(f"start tick collection")
    tick = signal_orchestrator.get_tick()
    print(f"tick: {tick}")
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
    print("Testing historical data fetch...")
    candles = md.get_historical_candles(
        "EURUSD",
        timeframe=Config.TIMEFRAME,
        start_pos=0,
        count=getattr(Config, "CANDLE_COUNT", 500),
    )
    print(f"[EURUSD] test_historical fetched: {len(candles)} candles")
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


@router.get("/live_signal")
def live_signal():
    global orchestrator_started
    if not orchestrator_started:
        signal_orchestrator.start()
        orchestrator_started = True
    signal = signal_orchestrator.get_latest_signal()
    return {"signal": signal}


@router.post("/stop_orchestrator")
def stop_orchestrator():
    global orchestrator_started
    if orchestrator_started:
        signal_orchestrator.stop()
        orchestrator_started = False
        return {"status": "orchestrator stopped"}
    else:
        return {"status": "orchestrator was not running"}
