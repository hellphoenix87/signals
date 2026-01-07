from typing import Any, Callable, List, Optional, Type, Dict
import MetaTrader5 as mt5

from app.config.settings import Config
from app.utils.configure_logging import logger as default_logger
from app.signals.indicators.sma_crossover import generate_sma_signal as default_sma_fn
from app.signals.indicators.macd import calculate_macd as default_macd_fn
from app.signals.indicators.rsi import calculate_rsi as default_rsi_fn

from app.signals.strategies.strong_signal_strategy import StrongSignalStrategy
from app.signals.strategies.multi_timeframe import MultiTimeframeStrongSignalStrategy
from app.signals.strategies.ntick_confirmed_signal_strategy import (
    NTickConfirmedSignalStrategy,
)


def strategy_factory(
    strategy_cls: Type = StrongSignalStrategy,
    config: Any = Config,
    logger: Any = default_logger,
    indicators: Optional[Dict[str, Callable[[List[dict]], Any]]] = None,
    log_file: Optional[str] = None,
    min_candles: Optional[int] = None,
    use_multi: Optional[bool] = None,
    use_n_tick: Optional[bool] = None,
    n_ticks: Optional[int] = None,
    **kwargs
):
    """
    Plug & Play Strategy Factory: instantiate and wire up any strategy.
    Optionally wrap with multi-timeframe or n-tick confirmation.
    Accepts one or multiple indicators.
    """
    use_multi = (
        use_multi
        if use_multi is not None
        else getattr(config, "USE_MULTI_TIMEFRAME_SIGNALS", False)
    )
    use_n_tick = (
        use_n_tick
        if use_n_tick is not None
        else getattr(config, "USE_N_TICK_CONFIRMATION", False)
    )
    n_ticks = (
        n_ticks
        if n_ticks is not None
        else int(getattr(config, "N_TICK_CONFIRMATION", 0) or 0)
    )

    # Default indicators if none provided
    if indicators is None:
        indicators = {
            # "sma": default_sma_fn,
            "macd": default_macd_fn,
            # "rsi": default_rsi_fn,
        }

    base = strategy_cls(
        indicators=indicators,
        logger=logger,
        min_candles=min_candles,
        config=config,
        **kwargs
    )

    strategy = base

    if use_multi:
        tf_bias = getattr(config, "TF_BIAS", mt5.TIMEFRAME_M15)
        tf_confirm = getattr(config, "TF_CONFIRM", mt5.TIMEFRAME_M5)
        tf_entry = getattr(config, "TF_ENTRY", mt5.TIMEFRAME_M1)
        strategy = MultiTimeframeStrongSignalStrategy(
            base=base, tf_bias=tf_bias, tf_confirm=tf_confirm, tf_entry=tf_entry
        )

    if use_n_tick and n_ticks > 1:
        strategy = NTickConfirmedSignalStrategy(strategy, n_ticks=n_ticks)

    return strategy
