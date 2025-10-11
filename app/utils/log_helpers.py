def log_signal_details_to_file(
    log_file,
    context,
    sma_signal=None,
    short_sma=None,
    long_sma=None,
    rsi=None,
    rsi_overbought=70,
    rsi_oversold=30,
    macd_trend=None,
    decision=None,
    confidence=None,
):
    with open(log_file, "a") as f:
        if short_sma is not None and long_sma is not None:
            f.write(f"{context} - Short SMA: {short_sma}, Long SMA: {long_sma}\n")
        if sma_signal is not None:
            f.write(f"{context} - Generated '{sma_signal}' signal\n")
        if rsi is not None:
            f.write(
                f"{context} - Latest RSI: {rsi:.2f}, Overbought: {rsi_overbought}, Oversold: {rsi_oversold}\n"
            )
        if macd_trend is not None:
            f.write(f"{context} - MACD trend: {macd_trend}\n")
        if decision is not None and confidence is not None:
            f.write(
                f"{context} - Signal summary: Decision={decision}, Confidence={confidence:.2f}\n"
            )
