def backtest_signals(strategy, candles, min_window):
    """
    Generate signals for each 5-min candle using the latest indicator window.
    Returns a list of signals and their confidence.
    """
    results = []
    for i in range(min_window, len(candles)):
        window = candles[i - min_window : i]
        signal = strategy.generate_signal(window)
        results.append(
            {
                "index": i,
                "time": candles[i]["time"],  # Adjust if your candle dict has 'time'
                "signal": signal.get("final_signal"),
                "confidence": signal.get("confidence"),
            }
        )
    return results
