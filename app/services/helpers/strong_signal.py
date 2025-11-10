from typing import Any, Callable, List, Optional
from datetime import datetime

from app.config.settings import Config
from app.utils.configure_logging import logger as default_logger
from app.signals.sma_crossover import generate_sma_signal as default_sma_fn
from app.signals.macd import calculate_macd as default_macd_fn
from app.signals.rsi import calculate_rsi as default_rsi_fn


def create_strong_signal_strategy(
    config: Any = Config,
    logger: Any = default_logger,
    sma_fn: Callable[[List[dict]], Any] = default_sma_fn,
    macd_fn: Callable[[List[dict]], dict] = default_macd_fn,
    rsi_fn: Callable[[List[dict]], Any] = default_rsi_fn,
    log_file: Optional[str] = None,
    min_candles: Optional[int] = None,
):
    """Provider for DI wiring (does not start anything)."""
    return StrongSignalStrategy(
        config=config,
        logger=logger,
        sma_fn=sma_fn,
        macd_fn=macd_fn,
        rsi_fn=rsi_fn,
        log_file=log_file,
        min_candles=min_candles,
    )


class StrongSignalStrategy:
    """DI-ready Strong Signal strategy."""

    def __init__(
        self,
        config: Any = Config,
        logger: Any = default_logger,
        sma_fn: Callable[[List[dict]], Any] = default_sma_fn,
        macd_fn: Callable[[List[dict]], dict] = default_macd_fn,
        rsi_fn: Callable[[List[dict]], Any] = default_rsi_fn,
        log_file: Optional[str] = None,
        min_candles: Optional[int] = None,
    ):
        self.config = config
        self.logger = logger
        self.sma_fn = sma_fn
        self.macd_fn = macd_fn
        self.rsi_fn = rsi_fn
        self.log_file = log_file or getattr(config, "LOG_FILE", "signals_log.txt")
        self.min_candles = min_candles or getattr(
            config, "MIN_CANDLES_FOR_INDICATORS", 202
        )
        self.confidence_threshold = getattr(config, "CONFIDENCE_THRESHOLD", 0.5)

    def generate_signal(self, candles: List[dict]) -> dict:
        try:
            if not candles or len(candles) < self.min_candles:
                self.logger.error(
                    "Not enough data to calculate indicators. "
                    f"Need at least {self.min_candles} data points."
                )
                return {"error": "Not enough data for calculations"}

            # SMA
            sma_signal = self.sma_fn(candles)
            if sma_signal is None:
                self.logger.error("SMA signal generation failed.")
                return {"error": "SMA signal generation failed"}
            sma_trend = (
                "bullish"
                if sma_signal == "buy"
                else "bearish" if sma_signal == "sell" else "neutral"
            )
            sma_strength = 1 if sma_signal in ("buy", "sell") else 0

            # MACD
            macd_values = self.macd_fn(candles) or {}
            macd_lines = macd_values.get("MACD", [])
            signal_line = macd_values.get("SignalLine", [])
            if len(macd_lines) == 0 or len(signal_line) == 0:
                self.logger.error(
                    "MACD signal generation failed. Data missing or invalid."
                )
                return {"error": "MACD signal generation failed"}
            macd_trend = (
                "bullish"
                if macd_lines[-1] > signal_line[-1]
                else "bearish" if macd_lines[-1] < signal_line[-1] else "neutral"
            )
            macd_strength = abs(macd_lines[-1] - signal_line[-1])

            # RSI
            rsi_values = self.rsi_fn(candles)
            if rsi_values is None or len(rsi_values) == 0:
                self.logger.error("RSI calculation failed or returned no values.")
                return {"error": "RSI calculation failed"}
            try:
                latest_rsi_val = float(rsi_values[-1])
            except Exception:
                self.logger.error("RSI latest value invalid.")
                return {"error": "RSI latest value invalid"}

            rsi_trend = (
                "bullish"
                if latest_rsi_val < 30
                else "bearish" if latest_rsi_val > 70 else "neutral"
            )

            # Decision / confidence
            final_signal = "hold"
            confidence = (sma_strength + macd_strength) / 2

            if (
                sma_trend == "bullish"
                and macd_trend == "bullish"
                and confidence >= self.confidence_threshold
            ):
                final_signal = "buy"
            elif (
                sma_trend == "bearish"
                and macd_trend == "bearish"
                and confidence >= self.confidence_threshold
            ):
                final_signal = "sell"
            else:
                final_signal = "hold"

            # Logging
            try:
                self.logger.info(
                    f"Signal summary: SMA={sma_trend}, MACD={macd_trend}, "
                    f"Decision={final_signal}, Confidence={confidence:.2f}"
                )
            except Exception:
                pass

            try:
                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                with open(self.log_file, "a") as f:
                    f.write(
                        f"{ts} | SMA={sma_trend}, MACD={macd_trend}, "
                        f"Decision={final_signal}, Confidence={confidence:.6f}\n"
                    )
            except Exception:
                pass

            return {"final_signal": final_signal, "confidence": confidence}

        except Exception as e:
            try:
                self.logger.error(f"Unexpected error: {str(e)}")
            except Exception:
                pass
            return {"error": str(e)}
