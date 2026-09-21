import logging
import numpy as np


def generate_stochastic_signal(
    data,
    *,
    period: int = 14,
    oversold: float = 20.0,
    overbought: float = 80.0,
    logger: logging.Logger | None = None,
):
    """
    Pure-ish: no file I/O. Caller decides logging.
    """
    log = logger or logging.getLogger(__name__)

    try:
        if len(data) < period:
            log.debug(
                "Not enough data for Stochastic Oscillator. Need at least %s data points.",
                period,
            )
            return "hold"

        window = data[-period:]
        highs = np.array(
            [float(item["high"]) for item in window if item.get("high") is not None],
            dtype=float,
        )
        lows = np.array(
            [float(item["low"]) for item in window if item.get("low") is not None],
            dtype=float,
        )
        closes = np.array(
            [float(item["close"]) for item in window if item.get("close") is not None],
            dtype=float,
        )
        if len(highs) < period or len(lows) < period or len(closes) < period:
            return "hold"

        highest_high = np.max(highs)
        lowest_low = np.min(lows)
        if highest_high == lowest_low:
            return "hold"

        latest_close = closes[-1]
        percent_k = 100.0 * (latest_close - lowest_low) / (highest_high - lowest_low)

        if percent_k < oversold:
            return "buy"
        if percent_k > overbought:
            return "sell"
        return "hold"

    except Exception as e:
        log.error("Error in generate_stochastic_signal: %s", e)
        return "hold"
