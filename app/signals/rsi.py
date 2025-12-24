import logging
import numpy as np
import pandas as pd


def calculate_rsi(
    data,
    *,
    period: int = 14,
    logger: logging.Logger | None = None,
):
    """
    Pure-ish RSI:
    - no file I/O
    - configurable period
    - returns numpy array aligned to input length (NaNs for warmup)
    """
    log = logger or logging.getLogger(__name__)

    try:
        period = int(period)
        if period <= 0:
            return np.array([])

        if not data or len(data) < period + 1:
            log.error(
                "Insufficient data for RSI calculation. Need at least %s bars.",
                period + 1,
            )
            return np.array([])

        closes = np.array(
            [float(bar["close"]) for bar in data if bar.get("close") is not None],
            dtype=float,
        )

        if len(closes) < period + 1:
            log.error("Insufficient non-null close prices for RSI calculation.")
            return np.array([])

        delta = np.diff(closes)

        gains = np.maximum(delta, 0.0)
        losses = np.maximum(-delta, 0.0)

        avg_gain = pd.Series(gains).rolling(window=period).mean().to_numpy()
        avg_loss = pd.Series(losses).rolling(window=period).mean().to_numpy()

        rs = avg_gain / (avg_loss + 1e-6)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # Align to closes length: gains/losses length is len(closes)-1, avg_* length same
        # We want output length == len(closes)
        rsi_aligned = np.concatenate(([np.nan] * period, rsi[period - 1 :]))
        # If any minor mismatch occurs, trim/pad conservatively
        if len(rsi_aligned) > len(closes):
            rsi_aligned = rsi_aligned[: len(closes)]
        elif len(rsi_aligned) < len(closes):
            rsi_aligned = np.concatenate(
                (rsi_aligned, [np.nan] * (len(closes) - len(rsi_aligned)))
            )

        return rsi_aligned

    except Exception as e:
        log.error("Error in calculate_rsi: %s", str(e))
        return np.array([])
