# data/market_data.py
import MetaTrader5 as mt5
from datetime import datetime
import numpy as np

def get_symbol_tick(symbol):
    """Retrieve current market tick data for a symbol."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"Failed to get tick information for {symbol}")
        return None
    return tick

def get_account_info():
    """Retrieve account info like balance, equity, margin."""
    account_info = mt5.account_info()
    if account_info is None:
        print("Failed to get account information")
        return None
    return account_info

def get_symbol_data(symbol="EURUSD", timeframe=mt5.TIMEFRAME_M1, num_bars=100):
    """
    Fetches recent data for a given symbol and timeframe.
    
    Args:
        symbol (str): Symbol to retrieve data for, e.g., "EURUSD".
        timeframe: Timeframe for the data (e.g., M1 for 1-minute bars).
        num_bars (int): Number of recent bars to retrieve.

    Returns:
        list: A list of dictionaries containing the OHLC data.
    """
    # Ensure the symbol is available in the market watch
    if not mt5.symbol_select(symbol, True):
        raise ValueError(f"Failed to select symbol {symbol}")

    # Get data
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, num_bars)
    if rates is None:
        raise RuntimeError(f"Failed to fetch data for symbol: {symbol}")

    # Convert to list of dicts with native Python data types
    data = []
    for rate in rates:
        data.append({
            "time": datetime.fromtimestamp(rate['time']),
            "open": float(rate['open']),
            "high": float(rate['high']),
            "low": float(rate['low']),
            "close": float(rate['close']),
            "tick_volume": int(rate['tick_volume'])
        })

    return data