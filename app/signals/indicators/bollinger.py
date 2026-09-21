import logging
import numpy as np


def generate_bollinger_signal(
    data,
    *,
    window: int = 20,
    std_mult: float = 2.0,
    logger: logging.Logger | None = None,
):
    """
    Pure-ish: no file I/O. Caller decides logging.
    """
    log = logger or logging.getLogger(__name__)

    try:
        if len(data) < window + 1:
            log.debug(
                "Not enough data for Bollinger Bands. Need at least %s data points.",
                window + 1,
            )
            return "hold"

        closes = np.array(
            [float(item["close"]) for item in data if item.get("close") is not None],
            dtype=float,
        )
        if len(closes) < window + 1:
            return "hold"

        window_closes = closes[-window:]
        sma = np.mean(window_closes)
        std = np.std(window_closes)
        upper = sma + std_mult * std
        lower = sma - std_mult * std
        latest_close = closes[-1]

        if latest_close <= lower:
            return "buy"
        if latest_close >= upper:
            return "sell"
        return "hold"

    except Exception as e:
        log.error("Error in generate_bollinger_signal: %s", e)
        return "hold"
