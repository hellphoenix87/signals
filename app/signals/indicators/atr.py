import logging
import numpy as np


def calculate_atr(
    candles,
    *,
    period: int = 14,
    logger: logging.Logger | None = None,
) -> float:
    """
    Wilder's Average True Range in price units: a measure of recent
    volatility, not direction. Returns 0.0 -- "no measurable volatility
    reading" -- if there isn't enough data for a stable reading (needs
    `period + 1` bars) or if a low-level calculation error occurs, so a
    caller gating on this value fails safe toward "don't gate" rather
    than raising.
    """
    log = logger or logging.getLogger(__name__)

    try:
        period = int(period)
        if period <= 0:
            return 0.0

        highs = np.array(
            [float(c["high"]) for c in candles if c.get("high") is not None],
            dtype=float,
        )
        lows = np.array(
            [float(c["low"]) for c in candles if c.get("low") is not None],
            dtype=float,
        )
        closes = np.array(
            [float(c["close"]) for c in candles if c.get("close") is not None],
            dtype=float,
        )

        n = min(len(highs), len(lows), len(closes))
        if n < period + 1:
            log.debug(f"Insufficient data for ATR. Need at least {period + 1} bars.")
            return 0.0

        highs, lows, closes = highs[-n:], lows[-n:], closes[-n:]

        prev_close = closes[:-1]
        true_range = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(np.abs(highs[1:] - prev_close), np.abs(lows[1:] - prev_close)),
        )

        atr_series = _wilder_smooth(true_range, period)
        latest_atr = atr_series[-1] if len(atr_series) else 0.0

        if np.isnan(latest_atr):
            return 0.0
        return float(latest_atr)

    except Exception as e:
        log.error(f"Error in calculate_atr: {e}")
        return 0.0


def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's running-average smoothing: seeded with a simple mean of the
    first `period` values, then each subsequent value is a `1/period`-weighted
    blend of the prior smoothed value and the new raw value."""
    smoothed = np.zeros_like(values)
    if len(values) < period:
        return smoothed
    smoothed[period - 1] = np.mean(values[:period])
    for i in range(period, len(values)):
        smoothed[i] = (smoothed[i - 1] * (period - 1) + values[i]) / period
    return smoothed[period - 1 :]
