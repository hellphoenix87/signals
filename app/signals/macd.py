import pandas as pd
from utils.configure_logging import logger


import pandas as pd
from utils.configure_logging import logger

def calculate_ema(data, span):
    """
    Calculate Exponential Moving Average (EMA) for the given data and span.

    Args:
        data (list): List of closing prices.
        span (int): The span for the EMA.

    Returns:
        pd.Series: The EMA values.
    """
    return pd.Series(data).ewm(span=span, adjust=False).mean()

def calculate_macd(data):
    try:
        if len(data) < 26:  # Minimum data points for EMA(26)
            logger.error("Insufficient data for MACD calculation.")
            return {"MACD": [], "SignalLine": []}

        # Extract closing prices
        closing_prices = [item['close'] for item in data if item['close'] is not None]

        # Calculate MACD line (12-period EMA - 26-period EMA)
        ema_12 = calculate_ema(closing_prices, 12)
        ema_26 = calculate_ema(closing_prices, 26)
        macd_line = ema_12 - ema_26

        # Calculate Signal Line (9-period EMA of MACD line)
        signal_line = calculate_ema(macd_line, 9)

        # Ensure the calculated lists have enough elements
        if len(macd_line) < 2 or len(signal_line) < 2:
            logger.error("Insufficient MACD or Signal Line points calculated.")
            return {"MACD": [], "SignalLine": []}

        return {"MACD": macd_line.tolist(), "SignalLine": signal_line.tolist()}
    except Exception as e:
        logger.error(f"Error in calculate_macd: {str(e)}")
        return {"MACD": [], "SignalLine": []}