from typing import Any, Dict, List, Optional
import MetaTrader5 as mt5
from app.signals.strategies.base_signal_strategy import BaseSignalStrategy
from app.signals.indicators.adx import calculate_adx


class MultiTimeframeStrongSignalStrategy(BaseSignalStrategy):
    """
    Multi-timeframe confluence, weighted rather than a strict three-way AND:

      - tf_bias (default M15): a trend-context vote, neutralized if ADX
        (measured on the same timeframe) is below `MTF_ADX_MIN_STRENGTH` --
        a moving-average bias in a genuine chop is noise, not trend.
      - tf_confirm (default M5): a second, independent vote.
      - tf_entry (default M1): the precise-timing vote.

    Each layer runs its own strategy instance (so each can use a genuinely
    different indicator, rather than the same one at three resolutions).
    A hard veto fires if the (possibly ADX-neutralized) bias vote and the
    entry vote are both directional and disagree -- the immediate M1 read
    is trusted over a higher-timeframe trend it directly contradicts.
    Otherwise, each layer contributes +/-its configured weight (0 if
    neutral) to a summed score, compared against `MTF_SCORE_THRESHOLD` to
    decide direction. A direction-aware pullback-vs-SMA20 gate still
    applies on top of that direction, since it answers a different
    question -- entry timing within the chosen direction, not which
    direction.
    """

    def __init__(
        self,
        *,
        bias_strategy: Any,
        confirm_strategy: Any,
        entry_strategy: Any,
        tf_bias: int = mt5.TIMEFRAME_M15,
        tf_confirm: int = mt5.TIMEFRAME_M5,
        tf_entry: int = mt5.TIMEFRAME_M1,
        config: Optional[Any] = None,
    ):
        self.bias_strategy = bias_strategy
        self.confirm_strategy = confirm_strategy
        self.entry_strategy = entry_strategy
        self.tf_bias = int(tf_bias)
        self.tf_confirm = int(tf_confirm)
        self.tf_entry = int(tf_entry)
        self.config = config

        self.adx_period = int(getattr(config, "MTF_ADX_PERIOD", 14))
        self.adx_min_strength = float(getattr(config, "MTF_ADX_MIN_STRENGTH", 20.0))
        self.bias_weight = float(getattr(config, "MTF_BIAS_WEIGHT", 0.5))
        self.confirm_weight = float(getattr(config, "MTF_CONFIRM_WEIGHT", 0.3))
        self.entry_weight = float(getattr(config, "MTF_ENTRY_WEIGHT", 0.2))
        self.score_threshold = float(getattr(config, "MTF_SCORE_THRESHOLD", 0.6))

    def generate_signal(self, candles_by_tf: Dict[int, List[dict]]) -> dict:
        """Combine per-timeframe signals into one final_signal.

        `candles_by_tf` may key its timeframes by int (1/5/15) or by string
        ("m1"/"m5"/"m15"/"1"/"5"/"15") -- both forms are accepted. `symbol`
        is resolved before any early return, so a `tf_error` hold still
        carries the symbol it applies to.
        """
        lower_map: Dict[str, Any] = {
            str(k).lower(): v for k, v in (candles_by_tf or {}).items()
        }

        def _get(tf: int) -> List[dict]:
            if candles_by_tf and tf in candles_by_tf:
                return candles_by_tf.get(tf, []) or []
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
            self.bias_strategy.generate_signal(c_bias)
            if c_bias
            else {"final_signal": "hold", "raw_signal": "hold"}
        )
        s_conf = (
            self.confirm_strategy.generate_signal(c_conf)
            if c_conf
            else {"final_signal": "hold", "raw_signal": "hold"}
        )
        s_entry = (
            self.entry_strategy.generate_signal(c_entry)
            if c_entry
            else {"final_signal": "hold", "raw_signal": "hold"}
        )

        symbol = (
            (s_entry.get("symbol") if isinstance(s_entry, dict) else None)
            or (s_conf.get("symbol") if isinstance(s_conf, dict) else None)
            or (s_bias.get("symbol") if isinstance(s_bias, dict) else None)
            or getattr(self.config, "SYMBOLS", ["EURUSD"])[0]
        )

        for s in (s_bias, s_conf, s_entry):
            if isinstance(s, dict) and s.get("error"):
                return {
                    "symbol": symbol,
                    "final_signal": "hold",
                    "raw_signal": "hold",
                    "reason": "tf_error",
                    "details": {"m15": s_bias, "m5": s_conf, "m1": s_entry},
                }

        bias = (s_bias.get("raw_signal", "hold") or "hold").lower()
        confirm = (s_conf.get("raw_signal", "hold") or "hold").lower()
        entry = (s_entry.get("final_signal", "hold") or "hold").lower()

        adx_value = calculate_adx(c_bias, period=self.adx_period) if c_bias else 0.0
        adx_ok = adx_value >= self.adx_min_strength
        effective_bias = bias if adx_ok else "hold"

        vetoed = (
            effective_bias in ("buy", "sell")
            and entry in ("buy", "sell")
            and effective_bias != entry
        )

        direction = "hold"
        if not vetoed:
            score = 0.0
            score += self.bias_weight if effective_bias == "buy" else 0.0
            score -= self.bias_weight if effective_bias == "sell" else 0.0
            score += self.confirm_weight if confirm == "buy" else 0.0
            score -= self.confirm_weight if confirm == "sell" else 0.0
            score += self.entry_weight if entry == "buy" else 0.0
            score -= self.entry_weight if entry == "sell" else 0.0

            if score >= self.score_threshold:
                direction = "buy"
            elif score <= -self.score_threshold:
                direction = "sell"

        pullback_ok = (
            self._pullback_completed(c_entry, direction)
            if c_entry and direction in ("buy", "sell")
            else False
        )
        final_signal = direction if pullback_ok else "hold"

        return {
            "symbol": symbol,
            "final_signal": final_signal,
            "raw_signal": final_signal,
            "confidence": float(s_entry.get("confidence", 0.0) or 0.0),
            "m15_bias": bias,
            "m5_confirm": confirm,
            "m1_entry": entry,
            "adx": adx_value,
            "pullback_completed": pullback_ok,
            "details": {"m15": s_bias, "m5": s_conf, "m1": s_entry},
        }

    def _pullback_completed(self, candles: list[dict], direction: str) -> bool:
        """Return whether price completed a pullback in `direction`'s favor
        against the 20-period SMA: for "buy", price was below the SMA in
        the recent past and has since closed back above it; for "sell",
        the mirror -- price was above the SMA and has since closed back
        below it. Previously this only ever checked the bullish shape
        regardless of `direction`, which silently gated every sell signal
        on an unrelated bullish pattern (see
        docs/test-results/mtf-entry-indicator-ablation.md)."""
        closes = [c["close"] for c in candles if "close" in c]
        if len(closes) < 21:
            return False
        sma20 = sum(closes[-20:]) / 20
        if direction == "sell":
            was_above = any(c > sma20 for c in closes[-25:-20])
            now_below = closes[-1] < sma20
            return was_above and now_below
        was_below = any(c < sma20 for c in closes[-25:-20])
        now_above = closes[-1] > sma20
        return was_below and now_above
