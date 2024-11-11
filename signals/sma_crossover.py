import logging
import numpy as np

def calculate_sma(data, window_size):
    """
    Calculate Simple Moving Average (SMA) for the given data and window size.

    Args:
        data (list): List of closing prices.
        window_size (int): The window size for the moving average.

    Returns:
        np.array: The SMA values.
    """
    return np.convolve(data, np.ones(window_size), 'valid') / window_size

def generate_sma_signal(data, short_window=50, long_window=200, threshold=0.0001):
    """
    Generate trading signals based on Simple Moving Average (SMA) crossover.

    Args:
        data (list): List of dictionaries with OHLC data.
        short_window (int): Window size for the short-term moving average.
        long_window (int): Window size for the long-term moving average.
        threshold (float): Minimum difference between SMAs to trigger a signal.

    Returns:
        str: "buy", "sell", or "hold" based on the SMA crossover.
    """
    # Ensure there are enough data points for the short and long SMA calculation
    if len(data) < long_window:
        logging.error(f"Not enough data to calculate SMA. Need at least {long_window} data points.")
        return "hold"  # Return "hold" or another default value
    
    # Extract closing prices
    closing_prices = [item['close'] for item in data]

    # Calculate short and long moving averages
    short_sma = calculate_sma(closing_prices, short_window)
    long_sma = calculate_sma(closing_prices, long_window)

    # Log the latest SMA values for debugging
    logging.info(f"Short SMA: {short_sma[-1]}, Long SMA: {long_sma[-1]}")

    # Calculate the difference between the short and long SMAs
    sma_diff = short_sma[-1] - long_sma[-1]

    # Check for crossover signal (buy or sell)
    if sma_diff > threshold and short_sma[-2] <= long_sma[-2]:
        logging.info("Generated 'buy' signal")
        return "buy"
    elif sma_diff < -threshold and short_sma[-2] >= long_sma[-2]:
        logging.info("Generated 'sell' signal")
        return "sell"
    else:
        logging.info("Generated 'hold' signal")
        return "hold"


