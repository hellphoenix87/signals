import logging
import numpy as np


def calculate_adx(
    candles,
    *,
    period: int = 14,
    logger: logging.Logger | None = None,
) -> float:
    """
    Wilder's Average Directional Index: a 0-100 measure of trend *strength*,
    not direction (unlike this codebase's other indicator functions, this
    does not return "buy"/"sell"/"hold"). Returns 0.0 -- "no measurable
    trend strength" -- if there isn't enough data for a stable reading
    (needs roughly `2 * period` bars) or if a low-level calculation error
    occurs, so a caller gating on a strength threshold fails safely toward
    "not trending" rather than raising.
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
        if n < 2 * period + 1:
            log.debug(f"Insufficient data for ADX. Need at least {2 * period + 1} bars.")
            return 0.0

        highs, lows, closes = highs[-n:], lows[-n:], closes[-n:]

        up_move = highs[1:] - highs[:-1]
        down_move = lows[:-1] - lows[1:]

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        prev_close = closes[:-1]
        true_range = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(np.abs(highs[1:] - prev_close), np.abs(lows[1:] - prev_close)),
        )

        atr = _wilder_smooth(true_range, period)
        plus_dm_smooth = _wilder_smooth(plus_dm, period)
        minus_dm_smooth = _wilder_smooth(minus_dm, period)

        plus_di = 100.0 * (plus_dm_smooth / (atr + 1e-8))
        minus_di = 100.0 * (minus_dm_smooth / (atr + 1e-8))

        di_sum = plus_di + minus_di
        dx = np.where(di_sum > 0, 100.0 * np.abs(plus_di - minus_di) / (di_sum + 1e-8), 0.0)

        if len(dx) < period:
            return 0.0

        adx_series = _wilder_smooth(dx, period)
        latest_adx = adx_series[-1] if len(adx_series) else 0.0

        if np.isnan(latest_adx):
            return 0.0
        return float(latest_adx)

    except Exception as e:
        log.error(f"Error in calculate_adx: {e}")
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
