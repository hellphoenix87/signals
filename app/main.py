from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.config.settings import Config
from app.data.market_data import MarketData
from app.execution.mode import TradingMode as Mode
from app.risk.risk_manager import RiskManager
from app.execution.broker import Broker
from app.services import signal_orchestrator
from app.strategies.breakout_strategy import BreakoutStrategy
from app.services.trading_services import TradingService
from app.services.live_collector import LiveCandleCollector
import MetaTrader5 as mt5

from app.services.signal_orchestrator import SignalOrchestrator
from app.utils.backtest_signals import backtest_signals

# Initialize dependencies
md = MarketData()
br = Broker(mode=Mode.BACKTEST)
rm = RiskManager(br)
strategy = BreakoutStrategy(md, rm, br)
trading_service = TradingService(strategy, rm, br, md)

signal_orchestrator = SignalOrchestrator(
    strategy,
    symbol="EURUSD",
    timeframe=Config.TIMEFRAME,
    count=Config.MIN_CANDLES_FOR_INDICATORS,
    interval=60,
)

orchestrator_started = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not mt5.initialize():
        raise RuntimeError("MT5 initialization failed")
    yield
    mt5.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/status")
def get_status():
    if getattr(br, "mode", None) == Mode.DEMO:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return {
        "daily_profit": getattr(trading_service, "daily_profit", None),
        "last_reset": getattr(trading_service, "last_reset", None),
        "active_symbols": strategy.get_last_scanned_symbols(),
    }


@app.post("/tick")
def manual_tick():
    print("manual_tick endpoint called")
    trading_service.tick()
    if getattr(br, "mode", None) == Mode.DEMO:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return {"status": "tick executed"}


@app.get("/simulated_positions")
def get_simulated_positions():
    if getattr(br, "mode", None) == Mode.DEMO:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return br.open_positions_sim


@app.post("/close_all")
def close_all_trades():
    trading_service._close_all_trades()
    return {"status": "all trades closed"}


@app.get("/test_historical")
def test_historical():
    from app.config.settings import Config

    print("Testing historical data fetch...")
    candles = md.get_historical_candles(
        "EURUSD",
        timeframe=Config.TIMEFRAME,
        start_pos=0,
        count=getattr(Config, "CANDLE_COUNT", 500),
    )
    print(f"[EURUSD] test_historical fetched: {len(candles)} candles")
    return {"candles": candles}


@app.get("/backtest_signals_historical")
def backtest_signals_endpoint_historical():
    from app.config.settings import Config
    from app.strategies.breakout_strategy import BreakoutStrategy

    candles = md.get_historical_candles(
        "EURUSD",
        timeframe=Config.TIMEFRAME,
        start_pos=0,
        count=Config.CANDLE_COUNT,
    )
    strategy = BreakoutStrategy(market_data=md, risk_manager=rm, broker=br)
    results = backtest_signals(
        strategy.strong_signal_strategy,
        candles,
        min_window=Config.MIN_CANDLES_FOR_INDICATORS,
    )
    return {"signals": results}


@app.get("/live_signal")
def live_signal():
    global orchestrator_started
    if not orchestrator_started:
        signal_orchestrator.start()
        orchestrator_started = True
    signal = signal_orchestrator.get_latest_signal()
    return {"signal": signal}


@app.post("/stop_orchestrator")
def stop_orchestrator():
    global orchestrator_started
    if orchestrator_started:
        signal_orchestrator.stop()
        orchestrator_started = False
        return {"status": "orchestrator stopped"}
    else:
        return {"status": "orchestrator was not running"}
