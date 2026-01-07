from typing import Any, Dict, List
import MetaTrader5 as mt5
from app.signals.strategies.base_signal_strategy import BaseSignalStrategy
from app.signals.strategies.strong_signal_strategy import StrongSignalStrategy


class MultiTimeframeStrongSignalStrategy(BaseSignalStrategy):
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
