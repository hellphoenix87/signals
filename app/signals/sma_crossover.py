import logging
import numpy as np

LOG_FILE = "signals_log.txt"


def calculate_sma(data, window_size):
    """Calculate Simple Moving Average (SMA) for the given data and window size."""
    return np.convolve(data, np.ones(window_size), "valid") / window_size


def write_log_to_file(message):
    with open(LOG_FILE, "a") as f:
        f.write(message + "\n")


def generate_sma_signal(
    data,
    short_window=50,
    long_window=200,
    slope_threshold=0.00005,
    diff_threshold=0.0001,
):
    """
    Generate trading signals based on SMA trend slope and crossover.

    Option B logic:
    - Use SMA slope (momentum) to detect trend direction.
    - Use SMA crossover to confirm trend reversal.
    - Generate 'buy', 'sell', or 'hold'.
    """
    try:
        if len(data) < long_window + 2:
            logging.error(
                f"Not enough data to calculate SMA. Need at least {long_window+2} data points."
            )
            write_log_to_file(
                f"[sma_crossover] Not enough data. Need {long_window+2} points."
            )
            return "hold"

        closing_prices = [item["close"] for item in data if item["close"] is not None]
        short_sma = calculate_sma(closing_prices, short_window)
        long_sma = calculate_sma(closing_prices, long_window)

        if len(short_sma) < 2 or len(long_sma) < 2:
            logging.error("Not enough SMA values to generate signal.")
            write_log_to_file("[sma_crossover] Not enough SMA values.")
            return "hold"

        # Latest SMA values
        s_sma, l_sma = short_sma[-1], long_sma[-1]
        prev_s_sma, prev_l_sma = short_sma[-2], long_sma[-2]

        # Calculate slopes
        short_slope = s_sma - prev_s_sma
        long_slope = l_sma - prev_l_sma
        diff = s_sma - l_sma
        prev_diff = prev_s_sma - prev_l_sma

        logging.info(f"Short SMA: {s_sma}, Long SMA: {l_sma}")
        write_log_to_file(f"[sma_crossover] Short SMA: {s_sma}, Long SMA: {l_sma}")
        logging.debug(
            f"SMA Diff: {diff}, Prev Diff: {prev_diff}, Short slope: {short_slope}, Long slope: {long_slope}"
        )
        write_log_to_file(
            f"[sma_crossover] Diff: {diff}, Prev Diff: {prev_diff}, Short slope: {short_slope}, Long slope: {long_slope}"
        )

        # Determine signal
        signal = "hold"

        # Trend-following: SMA slope
        if short_slope > slope_threshold and long_slope > 0:
            signal = "buy"
        elif short_slope < -slope_threshold and long_slope < 0:
            signal = "sell"

        # Confirm with crossover
        if diff > diff_threshold and prev_diff <= 0:
            signal = "buy"  # bullish crossover
        elif diff < -diff_threshold and prev_diff >= 0:
            signal = "sell"  # bearish crossover

        logging.info(f"Generated '{signal}' signal")
        write_log_to_file(f"[sma_crossover] Generated '{signal}' signal")
        return signal

    except Exception as e:
        logging.error(f"Error in generate_sma_signal: {str(e)}")
        write_log_to_file(f"[sma_crossover] Error: {str(e)}")
        return "error"
