import numpy as np
import logging


def calculate_rsi(
    data,
    *,
    period: int = 7,
    logger: logging.Logger | None = None,
):
    """
    Fast Signal RSI (FSI) for M1 scalping + tick confirmation.

    - Wilder EMA smoothing (classic RSI)
    - Aligned to input length (NaNs for warmup)
    - Returns np.array of RSI values
    """
    log = logger or logging.getLogger(__name__)

    try:
        period = int(period)
        if period <= 0:
            return np.array([])

        # Extract closes
        closes = np.array(
            [float(bar["close"]) for bar in data if bar.get("close") is not None],
            dtype=float,
        )
        if len(closes) < period + 1:
            log.debug(f"Insufficient data for FSI. Need at least {period + 1} bars.")
            return np.full(len(data), np.nan)

        delta = np.diff(closes)
        gains = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)

        # Wilder EMA
        avg_gain = np.zeros_like(gains)
        avg_loss = np.zeros_like(losses)
        avg_gain[0] = np.mean(gains[:period])
        avg_loss[0] = np.mean(losses[:period])

        for i in range(1, len(gains)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period

        rs = avg_gain / (avg_loss + 1e-8)  # avoid div-by-zero
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # Align to original closes length
        rsi_aligned = np.concatenate((np.full(period, np.nan), rsi[period - 1 :]))
        if len(rsi_aligned) < len(closes):
            rsi_aligned = np.concatenate(
                (rsi_aligned, np.full(len(closes) - len(rsi_aligned), np.nan))
            )
        elif len(rsi_aligned) > len(closes):
            rsi_aligned = rsi_aligned[: len(closes)]

        return rsi_aligned

    except Exception as e:
        log.error(f"Error in calculate_fsi: {e}")
        return np.full(len(data), np.nan)
