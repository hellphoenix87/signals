from app.config.settings import Config
from app.utils.configure_logging import logger
from app.signals.sma_crossover import generate_sma_signal
from app.signals.macd import calculate_macd
from app.signals.rsi import calculate_rsi

CONFIDENCE_THRESHOLD = getattr(Config, "CONFIDENCE_THRESHOLD", 0.5)


class StrongSignalStrategy:
    def __init__(self):
        pass

    def generate_signal(self, candles: list) -> dict:
        try:
            if len(candles) < 200:
                logger.error(
                    "Not enough data to calculate indicators. Need at least 200 data points."
                )
                return {"error": "Not enough data for calculations"}

            # SMA signal
            sma_signal = generate_sma_signal(candles)
            if not sma_signal:
                logger.error("SMA signal generation failed.")
                return {"error": "SMA signal generation failed"}
            sma_trend = (
                "bullish"
                if sma_signal == "buy"
                else "bearish" if sma_signal == "sell" else "neutral"
            )
            sma_strength = 1 if sma_signal in ("buy", "sell") else 0

            # MACD

            macd_values = calculate_macd(candles)
            macd_lines = macd_values.get("MACD", [])
            signal_line = macd_values.get("SignalLine", [])

            if not macd_lines or not signal_line:
                logger.error(
                    "MACD signal generation failed. Data is missing or invalid."
                )
                return {"error": "MACD signal generation failed"}
            macd_trend = (
                "bullish"
                if macd_lines[-1] > signal_line[-1]
                else "bearish" if macd_lines[-1] < signal_line[-1] else "neutral"
            )
            macd_strength = abs(macd_lines[-1] - signal_line[-1])

            # RSI

            closing_prices = [
                item["close"] for item in candles if item["close"] is not None
            ]

            rsi_values = calculate_rsi(candles)  # returns array
            latest_rsi = rsi_values[-1]  # get most recent RSI value

            rsi_trend = (
                "bullish"
                if latest_rsi < 30
                else "bearish" if latest_rsi > 70 else "neutral"
            )
            # --- Entry Logic ---
            final_signal = "hold"
            confidence = (sma_strength + macd_strength) / 2

            if (
                sma_trend == "bullish"
                and macd_trend == "bullish"
                and confidence >= CONFIDENCE_THRESHOLD
                # and rsi_trend == "bullish"
            ):
                final_signal = "buy"
            elif (
                sma_trend == "bearish"
                and macd_trend == "bearish"
                and confidence >= CONFIDENCE_THRESHOLD
                # and rsi_trend == "bearish"
            ):
                final_signal = "sell"
            else:
                final_signal = "hold"

            logger.info(
                f"Signal summary: SMA={sma_trend}, MACD={macd_trend} "
                f"Decision={final_signal}, Confidence={confidence:.2f}"
            )
            with open("signals_log.txt", "a") as f:
                f.write(
                    f"SMA={sma_trend}, MACD={macd_trend}"
                    f"Decision={final_signal}, Confidence={confidence:.2f}\n\n"
                )

            return {"final_signal": final_signal, "confidence": confidence}

        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return {"error": str(e)}
