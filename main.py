from fastapi import FastAPI, WebSocket, HTTPException
from contextlib import asynccontextmanager
from utils.connection import initialize_mt5, shutdown_mt5
from data.market_data import get_account_info, get_symbol_data
from strategies.strong_signal import generate_strong_signal
import asyncio
from utils.configure_logging import logger
from utils.process_handling import continuous_fetch, debug_active_tasks_and_threads, TaskManager

# Valid timeframes for trading data
valid_timeframes = ["M1", "M5", "H1", "D1"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize MT5 connection
    if not initialize_mt5():
        logger.error("Failed to initialize MT5 connection")
    yield
    # Shutdown: disconnect MT5 connection
    shutdown_mt5()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "Welcome to the MT5 Trading Bot API!"}

@app.get("/account_info")
async def account_info():
    # Endpoint to get account information
    return get_account_info()

@app.websocket("/ws/signal")
async def websocket_endpoint(websocket: WebSocket, symbols: str = "EURUSD", timeframe: str = "M1", num_bars: int = 100):
    """
    WebSocket endpoint to fetch symbol data continuously and send trading signals for multiple symbols.
    """
    symbol_list = symbols.split(",")
    if timeframe not in valid_timeframes:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Valid options are: M1, M5, H1, D1")

    await websocket.accept()

    # Create a TaskManager instance to manage tasks
    task_manager = TaskManager()

    # Start continuous data fetching
    task = asyncio.create_task(continuous_fetch(websocket, symbol_list, timeframe, num_bars, get_symbol_data, generate_strong_signal, task_manager))
    task_manager.add_task(task)

    # Wait for the task to complete
    await task

# Start the debug logger in the background
asyncio.create_task(debug_active_tasks_and_threads())