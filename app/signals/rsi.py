import logging
import numpy as np
import pandas as pd

def calculate_rsi(data, period=14):
    """
    Calculate the RSI (Relative Strength Index) for the given data.
    `data` should be a list or array of closing prices.
    """
    closes = np.array([bar['close'] for bar in data])

    # Calculate the differences between consecutive closing prices
    delta = np.diff(closes)
    
    # Separate gains and losses
    gains = np.maximum(delta, 0)
    losses = np.abs(np.minimum(delta, 0))

    # Calculate the average gain and loss
    avg_gain = pd.Series(gains).rolling(window=period).mean().values
    avg_loss = pd.Series(losses).rolling(window=period).mean().values

    # Calculate RSI
    rs = avg_gain / (avg_loss + 1e-6)  # Adding a small constant to prevent division by zero
    rsi = 100 - (100 / (1 + rs))

    # Return the RSI, aligning it to the original data length
    rsi = np.concatenate(([np.nan]*(period - 1), rsi))  # NaNs for periods without RSI data
    return rsi

def generate_combined_signal(sma_signal, data, rsi_period=14, rsi_overbought=70, rsi_oversold=30):
    """
    Combines SMA crossover signal with RSI confirmation.
    Returns the final trading signal.
    """
    # Calculate RSI for the data
    rsi_value = calculate_rsi(data, rsi_period)
    
    # Use only the latest SMA signal and the corresponding RSI value
    latest_rsi = rsi_value[-1]
    
    logging.info(f"Latest SMA Signal: {sma_signal}")
    logging.info(f"Latest RSI: {latest_rsi}, Overbought: {rsi_overbought}, Oversold: {rsi_oversold}")

    if sma_signal == "buy" and latest_rsi < rsi_oversold:
        final_signal = "buy"
    elif sma_signal == "sell" and latest_rsi > rsi_overbought:
        final_signal = "sell"
    else:
        final_signal = "hold"

    return {
        "sma_signal": sma_signal,
        "rsi": latest_rsi,
        "final_signal": final_signal
    }
