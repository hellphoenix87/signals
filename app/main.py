from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.data import market_data
from app.risk import risk_manager
from app.execution import broker
from app.strategies.breakout_strategy import BreakoutStrategy
from app.services.trading_services import TradingService
import MetaTrader5 as mt5

# Initialize dependencies
md = market_data
br = broker.Broker()
rm = risk_manager.RiskManager(br)

strategy = BreakoutStrategy(md, rm, br)
trading_service = TradingService(strategy, rm, br, md)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize MetaTrader5 when the app starts
    if not mt5.initialize():
        raise RuntimeError("MT5 initialization failed")
    yield
    # Shutdown MetaTrader5 when the app stops
    mt5.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/status")
def get_status():
    if br.demo_mode:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return {
        "daily_profit": trading_service.daily_profit,
        "last_reset": trading_service.last_reset,
        "active_symbols": strategy.get_last_scanned_symbols(),
    }


@app.post("/tick")
def manual_tick():
    """
    Manually trigger one iteration of the trading service.
    """
    print("manual_tick endpoint called")
    trading_service.tick()
    if br.demo_mode:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return {"status": "tick executed"}


@app.get("/simulated_positions")
def get_simulated_positions():
    """
    Return all open simulated (paper trading) positions.
    """

    if br.demo_mode:
        print(f"Paper trading mode: {len(br.open_positions_sim)} open positions")
    return br.open_positions_sim


@app.post("/close_all")
def close_all_trades():
    trading_service._close_all_trades()
    return {"status": "all trades closed"}
