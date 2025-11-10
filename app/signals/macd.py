import pandas as pd
from app.utils.configure_logging import logger

LOG_FILE = "signals_log.txt"


def write_log_to_file(message):
    with open(LOG_FILE, "a") as f:
        f.write(message + "\n")


def calculate_ema(data, span):
    """
    Calculate Exponential Moving Average (EMA) for the given data and span.
    """
    return pd.Series(data).ewm(span=span, adjust=False).mean()


def calculate_macd(data):
    try:
        if len(data) < 26:  # Minimum data points for EMA(26)
            logger.error("Insufficient data for MACD calculation.")
            write_log_to_file("[macd] Insufficient data for MACD calculation.")
            return {"MACD": [], "SignalLine": []}

        # Extract closing prices
        closing_prices = [item["close"] for item in data if item["close"] is not None]

        # Calculate MACD line (12-period EMA - 26-period EMA)
        ema_12 = calculate_ema(closing_prices, 12)
        ema_26 = calculate_ema(closing_prices, 26)
        macd_line = ema_12 - ema_26

        # Calculate Signal Line (9-period EMA of MACD line)
        signal_line = calculate_ema(macd_line, 9)

        # Ensure the calculated lists have enough elements
        if len(macd_line) < 2 or len(signal_line) < 2:
            logger.error("Insufficient MACD or Signal Line points calculated.")
            write_log_to_file(
                "[macd] Insufficient MACD or Signal Line points calculated."
            )
            return {"MACD": [], "SignalLine": []}

        # Log the latest MACD and Signal Line values
        logger.info(
            f"MACD: {macd_line.iloc[-1]:.10f}, SignalLine: {signal_line.iloc[-1]:.10f}"
        )
        write_log_to_file(
            f"[macd] MACD: {macd_line.iloc[-1]:.10f}, SignalLine: {signal_line.iloc[-1]:.10f}"
        )

        return {"MACD": macd_line.tolist(), "SignalLine": signal_line.tolist()}
    except Exception as e:
        logger.error(f"Error in calculate_macd: {str(e)}")
        write_log_to_file(f"[macd] Error: {str(e)}")
        return {"MACD": [], "SignalLine": []}
