import datetime
import threading
from fastapi import WebSocket, WebSocketDisconnect
import MetaTrader5 as mt5
import asyncio
from utils.configure_logging import logger
import time

# Valid timeframes for trading data
valid_timeframes = ["M1", "M5", "H1", "D1"]

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 2  # Delay between retries in seconds
FETCH_TIMEOUT = 10  # Timeout for data fetch in seconds
CANCEL_TIMEOUT = 5  # Timeout for task cancellation in seconds

async def fetch_signal_for_symbol(symbol: str, timeframe: str, num_bars: int, get_symbol_data, generate_strong_signal):
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
                try:
                    # Fetch market data
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

                    # Use the combined signal function for a stronger signal
                    strong_signal = generate_strong_signal(data)

                    # Get current timestamp for the signal
                    timestamp = datetime.datetime.now().isoformat()

                    last_fetch_time = current_time  # Update last fetch time
                    return {"symbol": symbol, "signal": strong_signal, "timestamp": timestamp}

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


async def fetch_signals_for_multiple_symbols(symbols: list, timeframe: str, num_bars: int, get_symbol_data, generate_strong_signal):
    """
    Fetch trading signals concurrently for multiple symbols and return the results.
    """
    tasks = []
    for symbol in symbols:
        task = asyncio.create_task(fetch_signal_for_symbol(symbol, timeframe, num_bars, get_symbol_data, generate_strong_signal))
        tasks.append(task)

    # Run all tasks concurrently and gather the results
    results = await asyncio.gather(*tasks)

    # Return a dictionary of signals for each symbol
    return dict(zip(symbols, results))

async def continuous_fetch(websocket: WebSocket, symbols: list, timeframe: str, num_bars: int, get_symbol_data, generate_strong_signal, task_manager):
    """
    Continuously fetch signals for multiple symbols and send them to the WebSocket client.
    """
    try:
        while True:
            signals = await fetch_signals_for_multiple_symbols(symbols, timeframe, num_bars, get_symbol_data, generate_strong_signal)
            await websocket.send_json({"signals": signals})
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        logger.info("Client disconnected, stopping signal fetch")
    except Exception as e:
        logger.error(f"Error in continuous_fetch: {e}")
    finally:
        # Cancel all running tasks with a timeout
        await task_manager.cancel_all_tasks()

class TaskManager:
    def __init__(self):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)

    async def cancel_all_tasks(self):
        for task in self.tasks:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=CANCEL_TIMEOUT)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.error(f"Task {task} did not cancel within {CANCEL_TIMEOUT} seconds")
        self.tasks.clear()

async def debug_active_tasks_and_threads():
    """
    Periodically logs the count of active threads and asyncio tasks.
    """
    while True:
        logger.info(f"Active asyncio tasks: {len(asyncio.all_tasks())}")
        logger.info(f"Active threads: {threading.active_count()}")
        await asyncio.sleep(60)