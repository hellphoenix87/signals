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

def generate_sma_signal(data, short_window=50, long_window=200):
    """
    Generate trading signals based on Simple Moving Average (SMA) crossover.

    Args:
        data (list): List of dictionaries with OHLC data.
        short_window (int): Window size for the short-term moving average.
        long_window (int): Window size for the long-term moving average.

    Returns:
        str: "buy", "sell", or "hold" based on the SMA crossover.
    """
    # Extract closing prices
    closing_prices = [item['close'] for item in data]

    # Calculate short and long moving averages
    short_sma = calculate_sma(closing_prices, short_window)
    long_sma = calculate_sma(closing_prices, long_window)

    # Check for crossover signal
    if short_sma[-1] > long_sma[-1] and short_sma[-2] <= long_sma[-2]:
        return "buy"
    elif short_sma[-1] < long_sma[-1] and short_sma[-2] >= long_sma[-2]:
        return "sell"
    else:
        return "hold"
