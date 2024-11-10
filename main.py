from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from contextlib import asynccontextmanager
import MetaTrader5 as mt5
from utils.connection import initialize_mt5, shutdown_mt5  # Import connection setup
from data.market_data import get_account_info, get_symbol_data  # Example function for data retrieval
from strategies.sma_crossover import generate_sma_signal  # Import the SMA signal generator
import asyncio
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

async def fetch_signal_continuously(websocket: WebSocket, symbol: str, timeframe: str, num_bars: int):
    """
    Function to fetch data continuously from MT5, calculate the SMA signal,
    and send only the trading signal to the WebSocket client.
    """
    logger.info(f"Started fetching signal for {symbol} with timeframe {timeframe} and num_bars {num_bars}")
    last_fetch_time = 0
    interval = 60  # Fetch new data every 60 seconds

    try:
        while True:
            # Validate timeframe
            if timeframe not in valid_timeframes:
                await websocket.send_text(f"Invalid timeframe. Valid options are: {', '.join(valid_timeframes)}")
                return

            # Map the timeframe string to MT5 constant
            timeframe_map = {
                "M1": mt5.TIMEFRAME_M1,
                "M5": mt5.TIMEFRAME_M5,
                "H1": mt5.TIMEFRAME_H1,
                "D1": mt5.TIMEFRAME_D1,
            }
            mt5_timeframe = timeframe_map.get(timeframe)

            # Fetch data if enough time has passed
            current_time = time.time()
            if current_time - last_fetch_time > interval:
                data = get_symbol_data(symbol, mt5_timeframe, num_bars)
                # Convert datetime objects to strings
                for item in data:
                    item['time'] = item['time'].isoformat()

                # Generate the trading signal using SMA crossover strategy
                signal = generate_sma_signal(data)

                # Send only the trading signal to the client
                await websocket.send_json({"signal": signal})
                logger.info(f"Generated signal: {signal}")  # Log the signal
                last_fetch_time = current_time  # Update last fetch time

            await asyncio.sleep(1)  # Small sleep to prevent max CPU usage

    except WebSocketDisconnect:
        logger.info(f"Client disconnected, stopping signal fetch for {symbol}")
    finally:
        logger.info(f"Stopped fetching signal for {symbol}")

@app.websocket("/ws/signal")
async def websocket_endpoint(websocket: WebSocket, symbol: str = "EURUSD", timeframe: str = "M1", num_bars: int = 100):
    """
    WebSocket endpoint to fetch symbol data continuously and send trading signals.
    """
    # Validate parameters
    if timeframe not in valid_timeframes:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Valid options are: M1, M5, H1, D1")
    
    # Accept WebSocket connection
    await websocket.accept()

    # Fetch and send trading signals continuously
    await fetch_signal_continuously(websocket, symbol, timeframe, num_bars)