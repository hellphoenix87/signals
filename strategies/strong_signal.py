from utils.configure_logging import logger
from signals.sma_crossover import generate_sma_signal
from signals.rsi import calculate_rsi, generate_combined_signal
from signals.macd import calculate_macd


def generate_strong_signal(data: list) -> str:
    try:
        # Validate input data
        if len(data) < 200:  # Assuming SMA requires at least 200 points
            logger.error("Not enough data to calculate SMA. Need at least 200 data points.")
            return {"error": "Not enough data for calculations"}

        # Generate SMA signal
        sma_signal = generate_sma_signal(data)
        if not sma_signal:
            logger.error("SMA signal generation failed.")
            return {"error": "SMA signal generation failed"}
        
        sma_trend = "bullish" if sma_signal == "buy" else "bearish"
        logger.info(f"Latest SMA Signal: {sma_signal}")

        # Generate RSI signal
        rsi_signal = generate_combined_signal(sma_signal, data)
        rsi_value = rsi_signal.get("rsi", None)
        if rsi_value is None:
            logger.error("RSI signal is missing or invalid.")
            return {"error": "RSI signal generation failed"}
        
        rsi_trend = (
            "bullish" if rsi_value < 30 else "bearish" if rsi_value > 70 else "neutral"
        )
        logger.info(f"Latest RSI: {rsi_value}, Overbought: 70, Oversold: 30")

        # Generate MACD signal
        macd_values = calculate_macd(data)
        macd_lines = macd_values.get("MACD", [])
        signal_line = macd_values.get("SignalLine", [])

        if not macd_lines or not signal_line or len(macd_lines) == 0 or len(signal_line) == 0:
            logger.error("MACD signal generation failed. Data is missing or invalid.")
            return {"error": "MACD signal generation failed"}

        macd_trend = (
            "bullish" if macd_lines[-1] > signal_line[-1]
            else "bearish" if macd_lines[-1] < signal_line[-1]
            else "neutral"
        )
        logger.info(f"Latest MACD: {macd_lines[-1]}, Signal Line: {signal_line[-1]}")

        # Combine the signals logically
        if sma_trend == "bullish" and rsi_trend == "bullish" and macd_trend == "bullish":
            final_signal = "buy"
        elif sma_trend == "bearish" and rsi_trend == "bearish" and macd_trend == "bearish":
            final_signal = "sell"
        else:
            final_signal = "hold"

        return {"final_signal": final_signal}

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {"error": str(e)}
