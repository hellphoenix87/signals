import logging
import pandas as pd


def calculate_ema(data, span: int):
    """Calculate Exponential Moving Average (EMA) for the given data and span."""
    return pd.Series(data).ewm(span=int(span), adjust=False).mean()


def calculate_macd(
    data,
    *,
    fast_period: int = 7,
    slow_period: int = 16,
    signal_period: int = 5,
    logger: logging.Logger | None = None,
):
    """
    Pure-ish MACD:
    - no file I/O
    - configurable periods (use different values per timeframe if desired)
    - returns lists: {"MACD": [...], "SignalLine": [...]}
    """
    log = logger or logging.getLogger(__name__)

    try:
        closing_prices = [
            item["close"] for item in data if item.get("close") is not None
        ]

        slow_period = int(slow_period)
        fast_period = int(fast_period)
        signal_period = int(signal_period)

        if slow_period <= 0 or fast_period <= 0 or signal_period <= 0:
            return {"MACD": [], "SignalLine": []}

        # Need at least slow_period points to compute EMA(slow) meaningfully
        if len(closing_prices) < slow_period:
            log.error("Insufficient data for MACD calculation.")
            return {"MACD": [], "SignalLine": []}

        ema_fast = calculate_ema(closing_prices, fast_period)
        ema_slow = calculate_ema(closing_prices, slow_period)
        macd_line = ema_fast - ema_slow
        signal_line = calculate_ema(macd_line, signal_period)

        if len(macd_line) < 2 or len(signal_line) < 2:
            log.error("Insufficient MACD or Signal Line points calculated.")
            return {"MACD": [], "SignalLine": []}

        log.info(
            "MACD: %.10f, SignalLine: %.10f",
            float(macd_line.iloc[-1]),
            float(signal_line.iloc[-1]),
        )

        return {"MACD": macd_line.tolist(), "SignalLine": signal_line.tolist()}

    except Exception as e:
        log.error("Error in calculate_macd: %s", str(e))
        return {"MACD": [], "SignalLine": []}
