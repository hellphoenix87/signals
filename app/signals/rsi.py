import logging
import numpy as np
import pandas as pd


def calculate_rsi(data, period=14):
    """
    Calculate the RSI (Relative Strength Index) for the given data.
    `data` should be a list or array of closing prices.
    """
    closes = np.array([bar["close"] for bar in data])

    # Calculate the differences between consecutive closing prices
    delta = np.diff(closes)

    # Separate gains and losses
    gains = np.maximum(delta, 0)
    losses = np.abs(np.minimum(delta, 0))

    # Calculate the average gain and loss
    avg_gain = pd.Series(gains).rolling(window=period).mean().values
    avg_loss = pd.Series(losses).rolling(window=period).mean().values

    # Calculate RSI
    rs = avg_gain / (
        avg_loss + 1e-6
    )  # Adding a small constant to prevent division by zero
    rsi = 100 - (100 / (1 + rs))

    # Return the RSI, aligning it to the original data length
    rsi = np.concatenate(
        ([np.nan] * (period - 1), rsi)
    )  # NaNs for periods without RSI data
    return rsi
