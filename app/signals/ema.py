import pandas as pd
from typing import List, Dict, Any, Optional


def calculate_ema(data: List[Dict[str, Any]], span: int) -> List[float]:
    """
    Calculate Exponential Moving Average (EMA) for the given data and span.
    Returns a list of floats.
    """
    if not data or span <= 0:
        return []

    # Extract closing prices
    closes = [float(item["close"]) for item in data if item.get("close") is not None]

    if len(closes) < span:
        return []

    # Calculate EMA using pandas
    series = pd.Series(closes).ewm(span=int(span), adjust=False).mean()

    return series.tolist()


def generate_ema_trend_signal(
    candles: List[Dict[str, Any]], period: int = 50
) -> Optional[str]:
    """
    Returns 'buy' (Bullish) if Price > EMA, 'sell' (Bearish) if Price < EMA.
    """
    if not candles:
        return None

    ema_values = calculate_ema(candles, period)

    if not ema_values:
        return None

    last_ema = ema_values[-1]
    last_close = float(candles[-1]["close"])

    if last_close > last_ema:
        return "buy"
    elif last_close < last_ema:
        return "sell"

    return "neutral"
