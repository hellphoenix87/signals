import numpy as np
import logging


def calculate_rsi(
    data,
    *,
    period: int = 7,
    logger: logging.Logger | None = None,
):
    """
    RSI indicator that returns a simple signal: 'buy', 'sell', or 'hold'.
    """
    log = logger or logging.getLogger(__name__)

    try:
        period = int(period)
        if period <= 0:
            return "hold"

        closes = np.array(
            [float(bar["close"]) for bar in data if bar.get("close") is not None],
            dtype=float,
        )
        if len(closes) < period + 1:
            log.debug(f"Insufficient data for RSI. Need at least {period + 1} bars.")
            return "hold"

        delta = np.diff(closes)
        gains = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)

        avg_gain = np.zeros_like(gains)
        avg_loss = np.zeros_like(losses)
        avg_gain[0] = np.mean(gains[:period])
        avg_loss[0] = np.mean(losses[:period])

        for i in range(1, len(gains)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period

        rs = avg_gain / (avg_loss + 1e-8)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # Use the latest RSI value for signal
        latest_rsi = rsi[-1] if len(rsi) > 0 else np.nan

        if np.isnan(latest_rsi):
            return "hold"
        elif latest_rsi < 30:
            return "buy"
        elif latest_rsi > 70:
            return "sell"
        else:
            return "hold"

    except Exception as e:
        log.error(f"Error in calculate_rsi: {e}")
        return "hold"
