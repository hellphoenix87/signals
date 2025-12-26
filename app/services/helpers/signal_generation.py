from __future__ import annotations

from collections import deque
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime

import MetaTrader5 as mt5

from app.config.settings import Config
from app.utils.configure_logging import logger as default_logger
from app.signals.sma_crossover import generate_sma_signal as default_sma_fn
from app.signals.macd import calculate_macd as default_macd_fn
from app.signals.rsi import calculate_rsi as default_rsi_fn


def create_signal_strategy(
    config: Any = Config,
    logger: Any = default_logger,
    sma_fn: Callable[[List[dict]], Any] = default_sma_fn,
    macd_fn: Callable[[List[dict]], dict] = default_macd_fn,
    rsi_fn: Callable[[List[dict]], Any] = default_rsi_fn,
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
        sma_fn=sma_fn,
        macd_fn=macd_fn,
        rsi_fn=rsi_fn,
        log_file=log_file,
        min_candles=min_candles,
    )
    if use_multi:
        tf_bias = getattr(config, "TF_BIAS", mt5.TIMEFRAME_M15)
        tf_confirm = getattr(config, "TF_CONFIRM", mt5.TIMEFRAME_M5)
        tf_entry = getattr(config, "TF_ENTRY", mt5.TIMEFRAME_M1)
        strategy = MultiTimeframeStrongSignalStrategy(
            base=base, tf_bias=tf_bias, tf_confirm=tf_confirm, tf_entry=tf_entry
        )
    else:
        strategy = base

    if use_n_tick and n_ticks > 1:
        strategy = NTickConfirmedSignalStrategy(strategy, n_ticks=n_ticks)

    return strategy


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


def create_multi_timeframe_signal_strategy(
    base: "StrongSignalStrategy",
    tf_bias: int = mt5.TIMEFRAME_M15,
    tf_confirm: int = mt5.TIMEFRAME_M5,
    tf_entry: int = mt5.TIMEFRAME_M1,
):
    """Provider: wraps StrongSignalStrategy to produce a multi-timeframe decision."""
    return MultiTimeframeStrongSignalStrategy(
        base=base, tf_bias=tf_bias, tf_confirm=tf_confirm, tf_entry=tf_entry
    )


class StrongSignalStrategy:
    """DI-ready Strong Signal strategy (no signal confirmation; uses stronger entry filters)."""

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
        self.min_candles = int(
            min_candles or getattr(config, "MIN_CANDLES_FOR_INDICATORS", 202) or 202
        )
        self.confidence_threshold = float(getattr(config, "CONFIDENCE_THRESHOLD", 0.5))

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

            # SMA
            sma_signal = self.sma_fn(candles_i)
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

            # RSI (informational)
            rsi_values = self.rsi_fn(candles_i)
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

            # Decision / confidence (raw)
            confidence = (sma_strength + macd_strength) / 2

            raw_signal = "hold"
            if (
                sma_trend == "bullish"
                and macd_trend == "bullish"
                and confidence >= self.confidence_threshold
            ):
                raw_signal = "buy"
            elif (
                sma_trend == "bearish"
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
                    f"Signal summary: Symbol={symbol}, SMA={sma_trend}, MACD={macd_trend}, RSI={rsi_trend}, "
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


class MultiTimeframeStrongSignalStrategy:
    """
    Multi-timeframe gating:
      - tf_bias (default M15) sets directional bias
      - tf_confirm (default M5) confirms
      - tf_entry (default M1) triggers the entry

    Output is a single final_signal ("buy"/"sell"/"hold") plus per-TF signals.
    """

    def __init__(
        self,
        *,
        base: StrongSignalStrategy,
        tf_bias: int = mt5.TIMEFRAME_M15,
        tf_confirm: int = mt5.TIMEFRAME_M5,
        tf_entry: int = mt5.TIMEFRAME_M1,
    ):
        self.base = base
        self.tf_bias = int(tf_bias)
        self.tf_confirm = int(tf_confirm)
        self.tf_entry = int(tf_entry)

    def generate_signal(self, candles_by_tf: Dict[int, List[dict]]) -> dict:
        # Accept collectors that key by int (1/5/15) OR by strings ("m1"/"m5"/"m15"/"1"/"5"/"15")
        lower_map: Dict[str, Any] = {
            str(k).lower(): v for k, v in (candles_by_tf or {}).items()
        }

        def _get(tf: int) -> List[dict]:
            if candles_by_tf and tf in candles_by_tf:
                return candles_by_tf.get(tf, []) or []
            # string versions
            v = lower_map.get(str(tf).lower())
            if v is not None:
                return v or []
            v = lower_map.get(f"m{int(tf)}")
            if v is not None:
                return v or []
            return []

        c_bias = _get(self.tf_bias)
        c_conf = _get(self.tf_confirm)
        c_entry = _get(self.tf_entry)

        s_bias = (
            self.base.generate_signal(c_bias, apply_entry_filters=False)
            if c_bias
            else {"final_signal": "hold", "raw_signal": "hold"}
        )
        s_conf = (
            self.base.generate_signal(c_conf, apply_entry_filters=False)
            if c_conf
            else {"final_signal": "hold", "raw_signal": "hold"}
        )
        s_entry = (
            self.base.generate_signal(c_entry, apply_entry_filters=True)
            if c_entry
            else {"final_signal": "hold", "raw_signal": "hold"}
        )

        # Resolve symbol early so early-returns are not "missing symbol"
        symbol = (
            (s_entry.get("symbol") if isinstance(s_entry, dict) else None)
            or (s_conf.get("symbol") if isinstance(s_conf, dict) else None)
            or (s_bias.get("symbol") if isinstance(s_bias, dict) else None)
            or self.base.config.SYMBOLS[0]
        )

        # If any timeframe returns an error or is waiting for a closed candle -> hold
        for s in (s_bias, s_conf, s_entry):
            if isinstance(s, dict) and s.get("error"):
                return {
                    "symbol": symbol,
                    "final_signal": "hold",
                    "raw_signal": "hold",
                    "reason": "tf_error",
                    "details": {"m15": s_bias, "m5": s_conf, "m1": s_entry},
                }
            if isinstance(s, dict) and s.get("reason") == "waiting_for_closed_candle":
                return {
                    "symbol": symbol,
                    "final_signal": "hold",
                    "raw_signal": "hold",
                    "reason": "waiting_for_closed_candle",
                    "details": {"m15": s_bias, "m5": s_conf, "m1": s_entry},
                }

        # Bias/confirm should be directional (use raw), entry should be executable (use final)
        bias = (s_bias.get("raw_signal", "hold") or "hold").lower()
        confirm = (s_conf.get("raw_signal", "hold") or "hold").lower()
        entry = (s_entry.get("final_signal", "hold") or "hold").lower()

        # --- Pullback logic: require pullback_completed for entry ---
        c_entry = _get(self.tf_entry)
        pullback_ok = self._pullback_completed(c_entry) if c_entry else False

        final_signal = "hold"
        if bias == "buy" and confirm == "buy" and entry == "buy" and pullback_ok:
            final_signal = "buy"
        elif bias == "sell" and confirm == "sell" and entry == "sell" and pullback_ok:
            final_signal = "sell"

        return {
            "symbol": symbol,
            "final_signal": final_signal,
            "raw_signal": final_signal,
            "confidence": float(s_entry.get("confidence", 0.0) or 0.0),
            "m15_bias": bias,
            "m5_confirm": confirm,
            "m1_entry": entry,
            "pullback_completed": pullback_ok,
            "details": {"m15": s_bias, "m5": s_conf, "m1": s_entry},
        }

    def _pullback_completed(self, candles: list[dict]) -> bool:
        # Example: last close above 20-period SMA after being below it
        closes = [c["close"] for c in candles if "close" in c]
        if len(closes) < 21:
            return False
        sma20 = sum(closes[-20:]) / 20
        was_below = any(c < sma20 for c in closes[-25:-20])
        now_above = closes[-1] > sma20
        return was_below and now_above


class NTickConfirmedSignalStrategy:
    """
    Wraps a base signal strategy to add n-tick confirmation logic.
    - Only returns actionable buy/sell signals after n consecutive favorable (or unfavorable) ticks.
    - Orchestrator should call generate_signal only on new closed candles.
    - All tick logic is handled internally via on_new_tick.
    """

    def __init__(
        self,
        base_strategy,
        n_ticks=3,
        min_pip_move=0.0,
        max_spread_points=None,
        liquidity_check_after_ntick=None,
        config=None,
        logger=None,
    ):
        self.base = base_strategy
        self.n_ticks = n_ticks
        self.min_pip_move = min_pip_move
        self.max_spread_points = max_spread_points
        self.config = config
        self.logger = logger or getattr(base_strategy, "logger", None)
        if config is not None and hasattr(config, "LIQUIDITY_CHECK_AFTER_NTICK"):
            self.liquidity_check_after_ntick = bool(
                getattr(config, "LIQUIDITY_CHECK_AFTER_NTICK")
            )
        elif liquidity_check_after_ntick is not None:
            self.liquidity_check_after_ntick = bool(liquidity_check_after_ntick)
        else:
            self.liquidity_check_after_ntick = True

        self._pending_signal = None
        self._pending_entry_price = None
        self._tick_results = []
        self._waiting = False
        self._last_signal = None
        self._last_m1_signal_id = None
        self._confirmed_signal = None

    def on_new_tick(self, price: float, spread_points: Optional[float] = None):
        """
        Called on every tick. Handles n-tick confirmation logic.
        """
        if not self._waiting or self._pending_signal not in ("buy", "sell"):
            return

        if self.logger:
            self.logger.info(
                f"[NTick] on_new_tick: price={price}, spread_points={spread_points}, waiting={self._waiting}, pending_signal={self._pending_signal}"
            )

        # Optional spread filter (before tick confirmation)
        if not self.liquidity_check_after_ntick:
            if self.max_spread_points is not None and spread_points is not None:
                if spread_points > self.max_spread_points:
                    self._tick_results = []
                    if self.logger:
                        self.logger.info(
                            f"[NTick] Spread too high: {spread_points}, resetting tick results."
                        )
                    return

        # Determine if tick is favorable
        favorable = False
        if self._pending_signal == "buy":
            favorable = price > (self._pending_entry_price or 0) + self.min_pip_move
        elif self._pending_signal == "sell":
            favorable = price < (self._pending_entry_price or 0) - self.min_pip_move

        self._tick_results.append(favorable)
        if len(self._tick_results) > self.n_ticks:
            self._tick_results.pop(0)

        if self.logger:
            self.logger.info(
                f"[NTick] Tick: price={price}, entry_price={self._pending_entry_price}, favorable={favorable}, tick_results={self._tick_results}"
            )

        if len(self._tick_results) == self.n_ticks:
            all_fav = all(self._tick_results)
            all_unfav = not any(self._tick_results)
            if all_fav:
                if self.logger:
                    self.logger.info(
                        f"[NTick] {self.n_ticks} consecutive favorable ticks: confirming {self._pending_signal}."
                    )
                self._confirmed_signal = {
                    **(self._last_signal or {}),
                    "final_signal": self._pending_signal,
                    "reason": f"{self.n_ticks}_consecutive_favorable_ticks",
                }
                self._reset()
            elif all_unfav:
                opposite = "sell" if self._pending_signal == "buy" else "buy"
                if self.logger:
                    self.logger.info(
                        f"[NTick] {self.n_ticks} consecutive unfavorable ticks: confirming {opposite}."
                    )
                self._confirmed_signal = {
                    **(self._last_signal or {}),
                    "final_signal": opposite,
                    "reason": f"{self.n_ticks}_consecutive_unfavorable_ticks_opposite_trade",
                }
                self._reset()
            else:
                if self.logger:
                    self.logger.info(
                        f"[NTick] Mixed ticks, clearing counter and continuing n-tick confirmation."
                    )
                self._tick_results = []

    def generate_signal(self, candles: list[dict], *args, **kwargs):
        """
        Called on each new closed candle by the orchestrator.
        Returns a buffered confirmed signal if n-tick confirmation was achieved since the last candle,
        otherwise returns a hold signal.
        """
        if self.logger:
            self.logger.info(
                f"[NTick] generate_signal called. Confirmed signal buffer: {self._confirmed_signal}"
            )

        # 1. Return buffered confirmed signal if present
        if self._confirmed_signal is not None:
            sig = self._confirmed_signal
            self._confirmed_signal = None
            return sig

        # 2. Get base signal for this candle
        signal = self.base.generate_signal(candles, *args, **kwargs)
        raw_signal = signal.get("final_signal", "hold")
        last_candle = candles[-1] if candles else None
        last_close = last_candle.get("close") if last_candle else None
        m1_signal_id = last_candle.get("time") if last_candle else None
        spread_points = last_candle.get("spread_points") if last_candle else None

        # 3. Hard reset on new M1 candle
        if m1_signal_id != self._last_m1_signal_id:
            if self.logger:
                self.logger.info(
                    f"[NTick] Hard reset on new M1 signal: {m1_signal_id} (was {self._last_m1_signal_id})"
                )
            self._reset()
            self._last_m1_signal_id = m1_signal_id

        # 4. Start n-tick confirmation if new buy/sell signal
        if raw_signal in ("buy", "sell") and self._pending_signal != raw_signal:
            self._pending_signal = raw_signal
            self._pending_entry_price = last_close
            self._tick_results = []
            self._waiting = True
            self._last_signal = signal
            if self.logger:
                self.logger.info(
                    f"[NTick] New pending signal: {raw_signal}, entry_price={last_close}, waiting for {self.n_ticks} ticks."
                )
            return {
                **signal,
                "final_signal": "hold",
                "reason": f"waiting_for_{self.n_ticks}_tick_confirmation",
            }

        # 5. If n-tick confirmation is running, but not yet confirmed, keep returning hold
        if self._waiting:
            return {
                **signal,
                "final_signal": "hold",
                "reason": f"waiting_for_{self.n_ticks}_tick_confirmation",
            }

        # 6. If no actionable signal, or after reset
        if raw_signal not in ("buy", "sell"):
            if self.logger:
                self.logger.info(f"[NTick] Raw signal not buy/sell, resetting.")
            self._reset()
        return {
            **signal,
            "final_signal": "hold",
            "reason": "waiting_for_tick_confirmation",
        }

    def get_confirmed_signal(self):
        sig = self._confirmed_signal
        self._confirmed_signal = None
        return sig

    def _reset(self):
        if self.logger:
            self.logger.info(f"[NTick] Resetting internal state.")
        self._pending_signal = None
        self._pending_entry_price = None
        self._tick_results = []
        self._waiting = False
        self._last_signal = None
