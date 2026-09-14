import functools
from typing import Any, Callable, List, Optional, Type, Dict
import MetaTrader5 as mt5

from app.config.settings import Config
from app.utils.configure_logging import logger as default_logger
from app.signals.indicators.macd import calculate_macd as default_macd_fn
from app.signals.indicators.sma_crossover import generate_sma_signal
from app.signals.indicators.rsi import calculate_rsi

from app.signals.strategies.strong_signal_strategy import StrongSignalStrategy
from app.signals.strategies.multi_timeframe import MultiTimeframeStrongSignalStrategy
from app.signals.strategies.ntick_confirmed_signal_strategy import (
    NTickConfirmedSignalStrategy,
)


def build_indicator(name: str, config: Any) -> Callable[[List[dict]], Any]:
    """Build one of the production entry-layer indicator callables by name
    ("macd", "sma", "rsi"), reading its tunables from `config`. Single
    source of truth so callers other than `strategy_factory` (e.g.
    backtest tooling running single-indicator ablations) don't duplicate
    the partial-construction parameters.
    """
    if name == "macd":
        return default_macd_fn
    if name == "sma":
        return functools.partial(
            generate_sma_signal,
            short_window=int(getattr(config, "ENTRY_SMA_SHORT_WINDOW", 5)),
            long_window=int(getattr(config, "ENTRY_SMA_LONG_WINDOW", 20)),
        )
    if name == "rsi":
        return functools.partial(
            calculate_rsi,
            period=int(getattr(config, "ENTRY_RSI_PERIOD", 7)),
        )
    raise ValueError(f"Unknown indicator: {name!r}")


def strategy_factory(
    strategy_cls: Type = StrongSignalStrategy,
    config: Any = Config,
    logger: Any = default_logger,
    indicators: Optional[Dict[str, Callable[[List[dict]], Any]]] = None,
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

    if indicators is None:
        if use_multi:
            # Multi-timeframe path: bias (SMA/M15) and confirm (RSI/M5)
            # layers below already own those indicators -- keep the entry
            # (M1) layer MACD-only so timeframes don't duplicate signals.
            indicators = {"macd": build_indicator("macd", config)}
        else:
            indicators = {
                name: build_indicator(name, config) for name in ("macd", "sma", "rsi")
            }

    min_candles = (
        min_candles
        if min_candles is not None
        else int(getattr(config, "MIN_CANDLES_FOR_INDICATORS", 1) or 1)
    )
    confidence_threshold = float(getattr(config, "CONFIDENCE_THRESHOLD", 0.5) or 0.5)

    # Ablation (docs/test-results/single-indicator-ablation.md) found RSI
    # carries more individual edge than MACD/SMA, which an equal-weight
    # vote dilutes -- weight it higher by default. No-op for the MTF entry
    # layer (MACD only, nothing to weigh against).
    weights = {
        "macd": float(getattr(config, "ENTRY_MACD_WEIGHT", 1.0)),
        "sma": float(getattr(config, "ENTRY_SMA_WEIGHT", 1.0)),
        "rsi": float(getattr(config, "ENTRY_RSI_WEIGHT", 2.0)),
    }

    base = strategy_cls(
        indicators=indicators,
        logger=logger,
        min_candles=min_candles,
        confidence_threshold=confidence_threshold,
        config=config,
        weights=weights,
        **kwargs
    )

    strategy = base

    if use_multi:
        tf_bias = getattr(config, "TF_BIAS", mt5.TIMEFRAME_M15)
        tf_confirm = getattr(config, "TF_CONFIRM", mt5.TIMEFRAME_M5)
        tf_entry = getattr(config, "TF_ENTRY", mt5.TIMEFRAME_M1)

        bias_sma = functools.partial(
            generate_sma_signal,
            short_window=int(getattr(config, "MTF_BIAS_SMA_SHORT", 10)),
            long_window=int(getattr(config, "MTF_BIAS_SMA_LONG", 50)),
        )
        bias_strategy = strategy_cls(
            indicators={"sma": bias_sma},
            logger=logger,
            min_candles=min_candles,
            confidence_threshold=confidence_threshold,
            config=config,
        )
        confirm_strategy = strategy_cls(
            indicators={"rsi": calculate_rsi},
            logger=logger,
            min_candles=min_candles,
            confidence_threshold=confidence_threshold,
            config=config,
        )
        strategy = MultiTimeframeStrongSignalStrategy(
            bias_strategy=bias_strategy,
            confirm_strategy=confirm_strategy,
            entry_strategy=base,
            tf_bias=tf_bias,
            tf_confirm=tf_confirm,
            tf_entry=tf_entry,
            config=config,
        )

    if use_n_tick and n_ticks > 1:
        strategy = NTickConfirmedSignalStrategy(strategy, n_ticks=n_ticks)

    return strategy
