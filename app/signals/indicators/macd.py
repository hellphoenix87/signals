import logging
import pandas as pd


def calculate_ema(data, span: int):
    return pd.Series(data).ewm(span=int(span), adjust=False).mean()


def _macd_histogram(
    data,
    fast_period: int,
    slow_period: int,
    signal_period: int,
    log: logging.Logger,
) -> tuple[float, float] | None:
    """Return `(hist_last, hist_prev)`, or `None` if there isn't enough data."""
    closing_prices = [item["close"] for item in data if item.get("close") is not None]

    slow_period = int(slow_period)
    fast_period = int(fast_period)
    signal_period = int(signal_period)

    if slow_period <= 0 or fast_period <= 0 or signal_period <= 0:
        return None

    if len(closing_prices) < slow_period:
        log.error("Insufficient data for MACD calculation.")
        return None

    ema_fast = calculate_ema(closing_prices, fast_period)
    ema_slow = calculate_ema(closing_prices, slow_period)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal_period)

    if len(macd_line) < 2 or len(signal_line) < 2:
        log.error("Insufficient MACD or Signal Line points calculated.")
        return None

    macd_last = float(macd_line.iloc[-1])
    signal_last = float(signal_line.iloc[-1])
    log.info("MACD: %.10f, SignalLine: %.10f", macd_last, signal_last)

    hist_last = macd_last - signal_last
    hist_prev = float(macd_line.iloc[-2]) - float(signal_line.iloc[-2])
    return hist_last, hist_prev


def calculate_macd(
    data,
    *,
    fast_period: int = 7,
    slow_period: int = 16,
    signal_period: int = 5,
    logger: logging.Logger | None = None,
):
    """
    MACD indicator that returns a simple signal: 'buy', 'sell', or 'hold'.
    """
    log = logger or logging.getLogger(__name__)

    try:
        hist = _macd_histogram(data, fast_period, slow_period, signal_period, log)
        if hist is None:
            return "hold"
        hist_last, hist_prev = hist

        # Histogram logic: Buy only if positive AND growing (accelerating)
        if hist_last > 0 and hist_last > hist_prev:
            return "buy"
        elif hist_last < 0 and hist_last < hist_prev:
            return "sell"
        else:
            return "hold"

    except Exception as e:
        log.error("Error in calculate_macd: %s", str(e))
        return "hold"


def calculate_macd_crossover(
    data,
    *,
    fast_period: int = 7,
    slow_period: int = 16,
    signal_period: int = 5,
    logger: logging.Logger | None = None,
):
    """
    Experimental alternate MACD trigger: fires only on a true crossover
    (the histogram flipping sign, equivalent to the MACD line crossing the
    signal line) instead of `calculate_macd`'s "still accelerating in the
    same direction" condition, which re-fires on most candles throughout
    an entire trending stretch rather than just at its start. Not used by
    `strategy_factory`/live trading -- exists for backtest comparison
    (see docs/test-results/macd-crossover-fix.md).
    """
    log = logger or logging.getLogger(__name__)

    try:
        hist = _macd_histogram(data, fast_period, slow_period, signal_period, log)
        if hist is None:
            return "hold"
        hist_last, hist_prev = hist

        if hist_last > 0 and hist_prev <= 0:
            return "buy"
        elif hist_last < 0 and hist_prev >= 0:
            return "sell"
        else:
            return "hold"

    except Exception as e:
        log.error("Error in calculate_macd_crossover: %s", str(e))
        return "hold"
