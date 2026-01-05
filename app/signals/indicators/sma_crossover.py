import logging
import numpy as np


def calculate_sma(data, window_size):
    return np.convolve(data, np.ones(window_size), "valid") / window_size


def generate_sma_signal(
    data,
    *,
    short_window=5,
    long_window=20,
    slope_threshold=0.00002,
    diff_threshold=0.00005,
    price_jump_threshold=0.00025,
    logger: logging.Logger | None = None,
):
    """
    Pure-ish: no file I/O. Caller decides logging.
    """
    log = logger or logging.getLogger(__name__)

    try:
        if len(data) < long_window + 2:
            log.error(
                "Not enough data to calculate SMA. Need at least %s data points.",
                long_window + 2,
            )
            return "hold"

        closing_prices = [
            item["close"] for item in data if item.get("close") is not None
        ]
        short_sma = calculate_sma(closing_prices, short_window)
        long_sma = calculate_sma(closing_prices, long_window)

        if len(short_sma) < 2 or len(long_sma) < 2:
            log.error("Not enough SMA values to generate signal.")
            return "hold"

        s_sma, l_sma = short_sma[-1], long_sma[-1]
        prev_s_sma, prev_l_sma = short_sma[-2], long_sma[-2]

        short_slope = s_sma - prev_s_sma
        long_slope = l_sma - prev_l_sma
        diff = s_sma - l_sma
        prev_diff = prev_s_sma - prev_l_sma

        signal = "hold"

        # Price jump logic
        if abs(closing_prices[-1] - closing_prices[-2]) > price_jump_threshold:
            signal = "buy" if closing_prices[-1] > closing_prices[-2] else "sell"
            log.info("Price jump detected -> %s", signal)
        elif short_slope > slope_threshold and long_slope > 0:
            signal = "buy"
        elif short_slope < -slope_threshold and long_slope < 0:
            signal = "sell"
        elif diff > diff_threshold and prev_diff <= 0:
            signal = "buy"
        elif diff < -diff_threshold and prev_diff >= 0:
            signal = "sell"

        log.info("Generated '%s' signal", signal)
        return signal

    except Exception as e:
        log.error("Error in generate_sma_signal: %s", e)
        return "error"
