from typing import Any, List, Optional

import numpy as np
import pandas as pd

from app.signals.strategies.base_signal_strategy import BaseSignalStrategy


class RsiFadeSignalStrategy(BaseSignalStrategy):
    """
    Mean-reversion entry: buy when RSI is oversold, sell when overbought, but
    only on the FIRST candle of each run (RSI tends to stay extreme for several
    candles, and later entries in the same run add correlated exposure, not
    new information).

    Stateless: both the current and the previous candle's raw signal are
    computed from the candle list itself, so live and backtest agree no matter
    when the strategy was constructed. RSI uses Wilder smoothing
    (EWM, alpha = 1/period) over the whole list -- the same definition the
    signal lab screened (`scripts/signal_lab.py::_rsi`).

    Motivation and results: docs/plans/in-progress/entry-timing.md (Test P1b).
    """

    def __init__(self, period: int = 14, low: float = 30.0, high: float = 70.0, logger: Optional[Any] = None):
        """
        Args:
            period: RSI period.
            low: Oversold level (RSI below it -> buy).
            high: Overbought level (RSI above it -> sell).
            logger: Optional logger.
        """
        self.period = int(period)
        self.low = float(low)
        self.high = float(high)
        self.logger = logger

    def _rsi(self, closes: pd.Series) -> pd.Series:
        d = closes.diff()
        up = d.clip(lower=0).ewm(alpha=1 / self.period, adjust=False).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / self.period, adjust=False).mean()
        return 100 - 100 / (1 + up / dn.replace(0, np.nan))

    def _raw(self, rsi: float) -> str:
        if rsi != rsi:  # NaN
            return "hold"
        if rsi < self.low:
            return "buy"
        if rsi > self.high:
            return "sell"
        return "hold"

    def generate_signal(self, candles: List[dict], *args, **kwargs):
        if not isinstance(candles, list) or len(candles) < self.period + 2:
            return {"final_signal": "hold", "raw_signal": "hold", "reason": "not_enough_candles"}
        closes = pd.Series([float(c["close"]) for c in candles])
        rsi = self._rsi(closes)
        now, prev = self._raw(float(rsi.iloc[-1])), self._raw(float(rsi.iloc[-2]))
        final = now if (now != "hold" and now != prev) else "hold"
        return {
            "final_signal": final,
            "raw_signal": now,
            "rsi_value": float(rsi.iloc[-1]),
            "reason": "rsi_fade_first_of_run" if final != "hold" else ("rsi_fade_run_continues" if now != "hold" else "rsi_neutral"),
            "symbol": candles[-1].get("symbol"),
        }
