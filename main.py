import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from contextlib import asynccontextmanager
import MetaTrader5 as mt5
from utils.connection import initialize_mt5, shutdown_mt5
from data.market_data import get_account_info, get_symbol_data
from signals.sma_crossover import generate_sma_signal
from signals.rsi import generate_combined_signal
import asyncio
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Valid timeframes for trading data
valid_timeframes = ["M1", "M5", "H1", "D1"]

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 2  # Delay between retries in seconds
FETCH_TIMEOUT = 10  # Timeout for data fetch in seconds

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

async def fetch_signal_for_symbol(symbol: str, timeframe: str, num_bars: int):
    """
    Fetch data for a specific symbol, generate SMA and RSI signals, and return the combined signal with timestamp.
    """
    logger.info(f"Fetching signal for {symbol} with timeframe {timeframe} and num_bars {num_bars}")
    last_fetch_time = 0
    interval = 60  # Fetch new data every 60 seconds

    retries = 0
    while retries < MAX_RETRIES:
        try:
            # Validate timeframe
            if timeframe not in valid_timeframes:
                logger.error(f"Invalid timeframe: {timeframe}. Valid options are: {', '.join(valid_timeframes)}")
                return {"symbol": symbol, "signal": "error", "message": "Invalid timeframe"}

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
                # Using asyncio.wait_for to enforce a timeout on data fetching
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(get_symbol_data, symbol, mt5_timeframe, num_bars),
                        timeout=FETCH_TIMEOUT,
                    )

                    if not data:
                        logger.error(f"No data available for {symbol}")
                        return {"symbol": symbol, "signal": "error", "message": "No data available"}

                    # Convert datetime objects to strings
                    for item in data:
                        item['time'] = item['time'].isoformat()

                    # Generate the SMA crossover signal
                    sma_signal = generate_sma_signal(data)

                    # Generate RSI-based confirmation signal
                    combined_signal = generate_combined_signal(sma_signal, data)

                    # Get current timestamp for the signal
                    timestamp = datetime.datetime.now().isoformat()

                    last_fetch_time = current_time  # Update last fetch time
                    return {"symbol": symbol, "signal": combined_signal, "timestamp": timestamp}

                except asyncio.TimeoutError:
                    logger.error(f"Fetching data for {symbol} timed out after {FETCH_TIMEOUT} seconds")
                    retries += 1
                    if retries < MAX_RETRIES:
                        logger.info(f"Retrying fetch for {symbol} (attempt {retries + 1})")
                        await asyncio.sleep(RETRY_DELAY)
                    else:
                        logger.error(f"Failed to fetch data for {symbol} after {MAX_RETRIES} attempts")
                        return {"symbol": symbol, "signal": "error", "message": "Data fetch timeout"}
                except Exception as e:
                    logger.error(f"Error fetching data for {symbol}: {e}")
                    retries += 1
                    if retries < MAX_RETRIES:
                        logger.info(f"Retrying fetch for {symbol} (attempt {retries + 1})")
                        await asyncio.sleep(RETRY_DELAY)
                    else:
                        logger.error(f"Failed to fetch data for {symbol} after {MAX_RETRIES} attempts")
                        return {"symbol": symbol, "signal": "error", "message": f"Error: {str(e)}"}

        except Exception as e:
            logger.error(f"Unexpected error fetching signal for {symbol}: {e}")
            return {"symbol": symbol, "signal": "error", "message": f"Unexpected error: {str(e)}"}

        await asyncio.sleep(1)  # Small sleep to prevent max CPU usage

async def fetch_signals_for_multiple_symbols(symbols: list, timeframe: str, num_bars: int):
    """
    Fetch trading signals concurrently for multiple symbols and return the results.
    """
    tasks = []
    for symbol in symbols:
        task = asyncio.create_task(fetch_signal_for_symbol(symbol, timeframe, num_bars))
        tasks.append(task)

    # Run all tasks concurrently and gather the results
    results = await asyncio.gather(*tasks)

    # Return a dictionary of signals for each symbol
    return dict(zip(symbols, results))

@app.websocket("/ws/signal")
async def websocket_endpoint(websocket: WebSocket, symbols: str = "EURUSD", timeframe: str = "M1", num_bars: int = 100):
    """
    WebSocket endpoint to fetch symbol data continuously and send trading signals for multiple symbols.
    """
    symbol_list = symbols.split(",")

    # Validate parameters
    if timeframe not in valid_timeframes:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Valid options are: M1, M5, H1, D1")

    # Accept WebSocket connection
    await websocket.accept()

    try:
        while True:
            # Fetch signals for multiple symbols
            signals = await fetch_signals_for_multiple_symbols(symbol_list, timeframe, num_bars)
            
            # Ensure signals are structured as {symbol: {signal: "hold", timestamp: "2024-11-11T15:30:00"}}
            structured_signals = {
                symbol: {"symbol": symbol, "signal": signal.get('signal', 'error'), "timestamp": signal.get('timestamp')}
                for symbol, signal in signals.items()
            }
            
            # Log the signals being sent to WebSocket

            # Send the structured signals to the WebSocket client
            await websocket.send_json({"signals": structured_signals})

            # Wait before fetching new data (based on your preferred refresh interval)
            await asyncio.sleep(60)  # For example, fetch every 60 seconds

    except WebSocketDisconnect:
        logger.info("Client disconnected, stopping signal fetch")

