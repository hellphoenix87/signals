from typing import Any, Dict, List, Optional

from app.signals.indicators.macd import _macd_histogram
from app.signals.indicators.rsi import _compute_latest_rsi
from app.signals.indicators.sma_crossover import calculate_sma

FEATURE_NAMES = [
    "macd_hist",
    "macd_hist_delta",
    "rsi",
    "sma_diff",
    "sma_short_slope",
    "sma_long_slope",
]


def extract_features(
    candles: List[dict],
    *,
    macd_fast: int = 7,
    macd_slow: int = 16,
    macd_signal: int = 5,
    rsi_period: int = 7,
    sma_short: int = 5,
    sma_long: int = 20,
) -> Optional[Dict[str, float]]:
    """Compute a fixed set of named, raw-valued features from the same
    indicators the hand-coded rules already use (MACD histogram, RSI,
    SMA), for an ML model to combine instead of hand-picked thresholds.

    Returns `None` if there isn't enough data for every indicator --
    callers should treat that the same as any other "not enough data"
    case (e.g. hold/skip), never crash on it.
    """
    import logging

    log = logging.getLogger(__name__)

    hist = _macd_histogram(candles, macd_fast, macd_slow, macd_signal, log)
    if hist is None:
        return None
    macd_hist, macd_hist_prev = hist

    rsi_value = _compute_latest_rsi(candles, rsi_period)
    if rsi_value is None:
        return None

    if len(candles) < sma_long + 2:
        return None
    closes = [c["close"] for c in candles if c.get("close") is not None]
    short_sma = calculate_sma(closes, sma_short)
    long_sma = calculate_sma(closes, sma_long)
    if len(short_sma) < 2 or len(long_sma) < 2:
        return None

    return {
        "macd_hist": float(macd_hist),
        "macd_hist_delta": float(macd_hist - macd_hist_prev),
        "rsi": float(rsi_value),
        "sma_diff": float(short_sma[-1] - long_sma[-1]),
        "sma_short_slope": float(short_sma[-1] - short_sma[-2]),
        "sma_long_slope": float(long_sma[-1] - long_sma[-2]),
    }


def features_to_vector(features: Dict[str, float]) -> List[float]:
    """Order a feature dict into the fixed vector layout the model expects."""
    return [features[name] for name in FEATURE_NAMES]
