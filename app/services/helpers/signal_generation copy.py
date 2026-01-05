from __future__ import annotations

from collections import deque
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime

import MetaTrader5 as mt5

from app.config.settings import Config
from app.utils.configure_logging import logger as default_logger
from app.signals.macd import calculate_macd as default_macd_fn
from app.signals.ema import generate_ema_trend_signal as default_ema_fn


def create_signal_strategy(
    config: Any = Config,
    logger: Any = default_logger,
    macd_fn: Callable[[List[dict]], dict] = default_macd_fn,
    ema_fn: Callable[[List[dict]], Any] = default_ema_fn,
    log_file: Optional[str] = None,
    min_candles: Optional[int] = None,
):
    """
    Factory: returns either single-timeframe or multi-timeframe strategy based on config.
    Optionally wraps with n-tick confirmation if enabled in config.
    """
    use_multi = getattr(config, "USE_MULTI_TIMEFRAME_SIGNALS", False)
    use_n_tick = getattr(config, "USE_N_TICK_CONFIRMATION", False)
    n_ticks = int(getattr(config, "N_TICK_CONFIRMATION", 0) or 0)

    base = StrongSignalStrategy(
        config=config,
        logger=logger,
        macd_fn=macd_fn,
        ema_fn=ema_fn,
        log_file=log_file,
        min_candles=min_candles,
    )

    strategy = base

    return strategy


def create_strong_signal_strategy(
    config: Any = Config,
    logger: Any = default_logger,
    macd_fn: Callable[[List[dict]], dict] = default_macd_fn,
    ema_fn: Callable[[List[dict]], Any] = default_ema_fn,
    log_file: Optional[str] = None,
    min_candles: Optional[int] = None,
):
    """Provider for DI wiring (does not start anything)."""
    return StrongSignalStrategy(
        config=config,
        logger=logger,
        macd_fn=macd_fn,
        ema_fn=ema_fn,
        log_file=log_file,
        min_candles=min_candles,
    )


class StrongSignalStrategy:
    """DI-ready Strong Signal strategy (MACD + EMA only)."""

    def __init__(
        self,
        config: Any = Config,
        logger: Any = default_logger,
        macd_fn: Callable[[List[dict]], dict] = default_macd_fn,
        ema_fn: Callable[[List[dict]], Any] = default_ema_fn,
        log_file: Optional[str] = None,
        min_candles: Optional[int] = None,
    ):
        self.config = config
        self.logger = logger
        self.macd_fn = macd_fn
        self.ema_fn = ema_fn
        self.log_file = log_file or getattr(config, "LOG_FILE", "signals_log.txt")
        self.min_candles = int(
            min_candles or getattr(config, "MIN_CANDLES_FOR_INDICATORS", 202) or 202
        )
        self.confidence_threshold = float(getattr(config, "CONFIDENCE_THRESHOLD", 0.5))

        # EMA Settings
        self.ema_period = int(getattr(config, "EMA_TREND_PERIOD", 50))

        # --- Stronger filters (config-driven) ---
        # Candle-close discipline
        self.use_closed_candles_only = bool(
            getattr(config, "USE_CLOSED_CANDLES_ONLY", True)
        )
        # If True, always drop the last candle (treat it as "forming") even if no flag exists.
        self.drop_last_candle_always = bool(
            getattr(config, "DROP_LAST_CANDLE_ALWAYS", False)
        )

        # Momentum filter (ATR-based)
        self.atr_period = int(getattr(config, "ENTRY_ATR_PERIOD", 14) or 14)
        # Require |close - prev_close| >= atr_move_mult * ATR
        self.atr_move_mult = float(getattr(config, "ENTRY_ATR_MOVE_MULT", 0.20) or 0.20)

        # Optional spread filter if candle dict includes spread/spread_points
        # 0 disables.
        self.max_spread_points = float(getattr(config, "MAX_SPREAD_POINTS", 0.0) or 0.0)

    # -----------------------
    # Helpers
    # -----------------------

    def _resolve_symbol(self, candles: List[dict]) -> str:
        symbol = (
            candles[-1].get("symbol")
            if candles and isinstance(candles[-1], dict)
            else None
        )
        if not symbol:
            symbol = self.config.SYMBOLS[0]
        return symbol

    def _is_candle_closed(self, candle: dict) -> bool:
        # Support multiple common flags; default to True if unknown (safer than blocking trading).
        for k in ("is_closed", "closed", "complete", "is_complete"):
            if k in candle:
                return bool(candle.get(k))
        return True

    def _candles_for_indicators(self, candles: List[dict]) -> List[dict]:
        if not candles:
            return candles

        if self.drop_last_candle_always:
            return candles[:-1] if len(candles) > 1 else []

        if self.use_closed_candles_only:
            last = candles[-1]
            if isinstance(last, dict) and not self._is_candle_closed(last):
                return candles[:-1] if len(candles) > 1 else []
        return candles

    def _min_candles_after_filtering(self) -> int:
        """
        Required number of candles AFTER applying closed-candle filtering.

        If DROP_LAST_CANDLE_ALWAYS=True and your collector provides exactly MIN_CANDLES_FOR_INDICATORS,
        we drop one and would otherwise always return 'waiting_for_closed_candle'.
        """
        n = int(self.min_candles or 0)
        if n <= 0:
            return 0
        if self.drop_last_candle_always:
            return max(1, n - 1)
        return n

    def _atr(self, candles: List[dict], period: int) -> Optional[float]:
        """
        ATR in raw price units. Expects candles to have: high, low, close.
        """
        if not candles or period <= 0:
            return None

        highs: List[float] = []
        lows: List[float] = []
        closes: List[float] = []

        for c in candles:
            try:
                highs.append(float(c.get("high")))
                lows.append(float(c.get("low")))
                closes.append(float(c.get("close")))
            except Exception:
                return None

        if len(closes) < period + 1:
            return None

        trs: List[float] = []
        start = len(closes) - period
        for i in range(start, len(closes)):
            h = highs[i]
            l = lows[i]
            prev_c = closes[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)

        if not trs:
            return None
        return sum(trs) / float(len(trs))

    def _spread_points_from_candle(self, candle: dict) -> Optional[float]:
        # Accept either already-in-points or raw spread; if raw, user should store points.
        v = candle.get("spread_points", None)
        if v is None:
            v = candle.get("spread", None)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    def _passes_entry_filters(
        self, *, candles: List[dict], raw_signal: str
    ) -> tuple[bool, str, dict]:
        if raw_signal not in ("buy", "sell"):
            return True, "not_applicable", {}

        if not candles or len(candles) < 2:
            return (
                False,
                "not_enough_candles",
                {"candles": len(candles) if candles else 0},
            )

        last = candles[-1]
        prev = candles[-2]

        if self.max_spread_points > 0:
            sp = (
                self._spread_points_from_candle(last)
                if isinstance(last, dict)
                else None
            )
            if sp is not None and sp > self.max_spread_points:
                return (
                    False,
                    "spread_too_high",
                    {"spread_points": sp, "max_spread_points": self.max_spread_points},
                )

        try:
            last_close = float(last.get("close"))
            prev_close = float(prev.get("close"))
        except Exception:
            return False, "bad_close_values", {}

        delta = last_close - prev_close
        if raw_signal == "buy" and delta <= 0:
            return False, "wrong_direction", {"delta": delta}
        if raw_signal == "sell" and delta >= 0:
            return False, "wrong_direction", {"delta": delta}

        atr = self._atr(candles, self.atr_period)
        if atr is None or atr <= 0:
            return True, "atr_unavailable", {}

        required = self.atr_move_mult * atr
        if abs(delta) < required:
            return (
                False,
                "atr_move_too_small",
                {
                    "delta": delta,
                    "atr": atr,
                    "required": required,
                    "atr_period": self.atr_period,
                    "atr_move_mult": self.atr_move_mult,
                },
            )

        return True, "ok", {"delta": delta, "atr": atr, "required": required}

    # -----------------------
    # Main
    # -----------------------

    def generate_signal(
        self, candles: List[dict], *, apply_entry_filters: bool = False
    ) -> dict:
        try:
            if not candles or len(candles) < self.min_candles:
                self.logger.error(
                    "Not enough data to calculate indicators. "
                    f"Need at least {self.min_candles} data points."
                )
                return {"error": "Not enough data for calculations"}

            symbol = self._resolve_symbol(candles)

            candles_i = self._candles_for_indicators(candles)
            min_after = self._min_candles_after_filtering()

            if not candles_i or len(candles_i) < min_after:
                return {
                    "final_signal": "hold",
                    "raw_signal": "hold",
                    "symbol": symbol,
                    "reason": "waiting_for_closed_candle",
                    "candles_in": len(candles) if candles else 0,
                    "candles_used": len(candles_i) if candles_i else 0,
                    "min_required_used": min_after,
                }

            # --- EMA Trend Logic ---
            ema_signal = self.ema_fn(candles_i, self.ema_period)
            ema_trend = (
                "bullish"
                if ema_signal == "buy"
                else "bearish" if ema_signal == "sell" else "neutral"
            )
            ema_strength = 1 if ema_signal in ("buy", "sell") else 0

            # --- MACD Logic ---
            macd_values = self.macd_fn(candles_i) or {}
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

            # Decision / confidence (raw)
            confidence = (ema_strength + macd_strength) / 2

            raw_signal = "hold"
            if (
                ema_trend == "bullish"
                and macd_trend == "bullish"
                and confidence >= self.confidence_threshold
            ):
                raw_signal = "buy"
            elif (
                ema_trend == "bearish"
                and macd_trend == "bearish"
                and confidence >= self.confidence_threshold
            ):
                raw_signal = "sell"

            # Stronger entry filters
            final_signal = raw_signal
            entry_reason = "not_applied"
            entry_meta: dict = {}

            if apply_entry_filters:
                entry_ok, entry_reason, entry_meta = self._passes_entry_filters(
                    candles=candles_i, raw_signal=raw_signal
                )
                if not entry_ok:
                    final_signal = "hold"

            # Logging
            try:
                extra = (
                    f", EntryFilter={entry_reason}"
                    if apply_entry_filters and raw_signal in ("buy", "sell")
                    else ""
                )
                self.logger.info(
                    f"Signal summary: Symbol={symbol}, EMA={ema_trend}, MACD={macd_trend}, "
                    f"Raw={raw_signal}, Final={final_signal}, Confidence={confidence:.2f}{extra}"
                )
                if (
                    apply_entry_filters
                    and raw_signal in ("buy", "sell")
                    and final_signal == "hold"
                ):
                    self.logger.info(f"EntryFilter details: {entry_meta}")
            except Exception:
                pass

            return {
                "final_signal": final_signal,
                "raw_signal": raw_signal,
                "confidence": confidence,
                "symbol": symbol,
                "entry_filter_reason": entry_reason,
                "entry_filter_meta": entry_meta,
            }
        except Exception as e:
            try:
                self.logger.error(f"Unexpected error: {str(e)}")
            except Exception:
                pass
            return {"error": str(e)}
