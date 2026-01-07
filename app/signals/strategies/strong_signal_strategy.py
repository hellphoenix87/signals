from typing import Any, Callable, List, Optional, Dict
from app.signals.strategies.base_signal_strategy import BaseSignalStrategy
from app.utils.configure_logging import logger as default_logger


class StrongSignalStrategy(BaseSignalStrategy):
    """
    Modular indicator processing strategy.
    Accepts any set of indicator functions and combines their outputs.
    """

    def __init__(
        self,
        indicators: Dict[str, Callable[[List[dict]], Any]],
        logger: Any = default_logger,
        min_candles: Optional[int] = None,
        confidence_threshold: float = 0.5,
        config: Any = None,
    ):
        self.indicators = indicators
        self.logger = logger
        self.min_candles = int(min_candles or 1)
        self.confidence_threshold = float(confidence_threshold)
        self.config = config

    def generate_signal(
        self, candles: List[dict], *, apply_entry_filters: bool = False
    ) -> dict:
        print(f"candles received: {len(candles)}")
        symbol = self._resolve_symbol(candles, getattr(self, "config", None))
        print(f"Generating signal for {symbol} using StrongSignalStrategy")
        if not candles or len(candles) < self.min_candles:
            self.logger.error(
                f"Not enough data to calculate indicators. Need at least {self.min_candles} data points."
            )
            return {"error": "Not enough data for calculations"}

        results = {}
        for name, fn in self.indicators.items():
            try:
                results[name] = fn(candles)
            except Exception as e:
                self.logger.error(f"{name} indicator failed: {e}")
                results[name] = None

        # Example logic: combine indicators (customize as needed)
        # Here, we just check for 'buy'/'sell' in any indicator
        buy_votes = sum(1 for v in results.values() if v == "buy")
        sell_votes = sum(1 for v in results.values() if v == "sell")
        total_votes = buy_votes + sell_votes

        confidence = total_votes / max(1, len(self.indicators))
        raw_signal = "hold"
        if buy_votes > sell_votes and confidence >= self.confidence_threshold:
            raw_signal = "buy"
        elif sell_votes > buy_votes and confidence >= self.confidence_threshold:
            raw_signal = "sell"

        # Optionally, add entry filters here if needed

        self.logger.info(
            f"Indicators: {results}, Raw signal: {raw_signal}, Confidence: {confidence:.2f}"
        )

        return {
            "final_signal": raw_signal,
            "raw_signal": raw_signal,
            "confidence": confidence,
            "indicators": results,
            "symbol": symbol,
        }
