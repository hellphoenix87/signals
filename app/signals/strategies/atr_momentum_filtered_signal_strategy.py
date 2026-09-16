from typing import Any
from app.signals.strategies.base_signal_strategy import BaseSignalStrategy
from app.signals.indicators.atr import calculate_atr


class AtrMomentumFilteredSignalStrategy(BaseSignalStrategy):
    """
    Wraps another strategy and neutralizes a buy/sell signal (forces
    "hold") unless the latest closed candle's move is at least
    `atr * move_mult` -- filtering out signals fired during low-volatility
    chop rather than genuine momentum. Passes the underlying signal
    through unchanged whenever there isn't enough candle history to
    compute ATR, so it fails toward "don't gate" rather than blocking
    trading on a transient data gap.
    """

    def __init__(self, strategy: BaseSignalStrategy, *, atr_period: int, move_mult: float):
        self.strategy = strategy
        self.atr_period = int(atr_period)
        self.move_mult = float(move_mult)

    def generate_signal(self, candles, *args, **kwargs):
        result = self.strategy.generate_signal(candles, *args, **kwargs)
        if not isinstance(result, dict) or result.get("error"):
            return result

        final_signal = (result.get("final_signal") or "hold").lower()
        if final_signal not in ("buy", "sell"):
            return result

        closes = [c["close"] for c in candles if isinstance(c, dict) and "close" in c]
        if len(closes) < 2:
            return result

        atr = calculate_atr(candles, period=self.atr_period)
        if atr <= 0:
            return result

        move = abs(float(closes[-1]) - float(closes[-2]))
        if move < atr * self.move_mult:
            return {
                **result,
                "final_signal": "hold",
                "raw_signal": "hold",
                "reason": "atr_momentum_filtered",
                "atr": atr,
                "atr_move": move,
            }

        return result
