from app.config.settings import Config
from app.services.helpers.signal_generation import (
    StrongSignalStrategy,
    MultiTimeframeStrongSignalStrategy,
)
import MetaTrader5 as mt5


def create_breakout_strategy(market_data, risk_manager, broker):
    """Provider for DI wiring of BreakoutStrategy."""
    return BreakoutStrategy(market_data, risk_manager, broker)


class BreakoutStrategy:
    def __init__(self, market_data, risk_manager, broker):
        self.market_data = market_data
        self.risk_manager = risk_manager
        self.broker = broker
        self._last_scanned_symbols = []

        # Base per-timeframe strategy
        self.strong_signal_strategy = StrongSignalStrategy()

        # Multi-timeframe wrapper (bias/confirm/entry)
        self.tf_bias = int(getattr(Config, "TF_BIAS", mt5.TIMEFRAME_M15))
        self.tf_confirm = int(getattr(Config, "TF_CONFIRM", mt5.TIMEFRAME_M5))
        self.tf_entry = int(getattr(Config, "TF_ENTRY", mt5.TIMEFRAME_M1))

        self.mtf_signal_strategy = MultiTimeframeStrongSignalStrategy(
            base=self.strong_signal_strategy,
            tf_bias=self.tf_bias,
            tf_confirm=self.tf_confirm,
            tf_entry=self.tf_entry,
        )

    def get_last_scanned_symbols(self):
        return self._last_scanned_symbols

    def generate_signals(self, account_balance):
        # Check daily profit before generating signals
        daily_profit = 0
        if hasattr(self.broker, "get_daily_profit"):
            daily_profit = self.broker.get_daily_profit()
        elif hasattr(self.risk_manager, "get_daily_profit"):
            daily_profit = self.risk_manager.get_daily_profit()

        if daily_profit >= Config.DAILY_TARGET_PROFIT:
            print("Daily target profit reached, no new trades will be generated.")
            return []

        symbols = Config.SYMBOLS[: Config.MAX_SYMBOLS]
        self._last_scanned_symbols = symbols
        signals = []

        candle_count = int(getattr(Config, "CANDLE_COUNT", 500) or 500)
        min_candles = int(getattr(Config, "MIN_CANDLES_FOR_INDICATORS", 200) or 200)

        for symbol in symbols:
            # --- Fetch candles for each TF ---
            candles_by_tf: dict[int, list[dict]] = {}
            for tf in (self.tf_entry, self.tf_confirm, self.tf_bias):
                if getattr(self.broker, "mode", None) == "backtest":
                    cs = self.market_data.get_historical_candles(
                        symbol,
                        timeframe=int(tf),
                        start_pos=1,
                        count=candle_count,
                    )
                else:
                    cs = self.market_data.get_symbol_data(
                        symbol,
                        timeframe=int(tf),
                        num_bars=candle_count,
                        closed_only=True,
                    )

                if not cs or len(cs) < min_candles:
                    print(
                        f"[{symbol}] Not enough candles tf={tf}: {len(cs) if cs else 0} (required: {min_candles})"
                    )
                    candles_by_tf = {}
                    break

                candles_by_tf[int(tf)] = cs

            if not candles_by_tf:
                continue

            # --- Multi-timeframe signal ---
            signal_result = self.mtf_signal_strategy.generate_signal(candles_by_tf)
            print(f"[{symbol}] MTF Signal result: {signal_result}")

            final_signal = signal_result.get("final_signal")
            if final_signal not in ("buy", "sell"):
                print(f"[{symbol}] No trading signal generated")
                continue

            # Use ENTRY TF candles for price + SL/TP calculations
            entry_candles = candles_by_tf.get(self.tf_entry, []) or []
            if not entry_candles:
                continue

            price = entry_candles[-1]["close"]

            sl_pips, tp_pips = self._calculate_dynamic_sl_tp(entry_candles, symbol)
            direction = "BUY" if final_signal == "buy" else "SELL"

            signals.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "price": price,
                    "sl_pips": sl_pips,
                    "tp_pips": tp_pips,
                    # pass HTF context forward (so orchestrator can feed exit_trade.update_bias)
                    "m15_bias": signal_result.get("m15_bias"),
                    "m5_confirm": signal_result.get("m5_confirm"),
                    "m1_entry": signal_result.get("m1_entry"),
                    "confidence": signal_result.get("confidence"),
                }
            )

        unique_signals = list({s["symbol"]: s for s in signals}.values())
        total_signals = len(unique_signals)
        if total_signals == 0:
            return []

        risk_per_signal = Config.LOT_RISK_PERCENT / total_signals
        final_signals = []

        for s in unique_signals:
            lot = self.risk_manager.calculate_lot_size(
                account_balance,
                s["sl_pips"],
                symbol_price=s["price"],
                symbol=s["symbol"],
                risk_percent=risk_per_signal,
            )
            if lot <= 0:
                continue

            sl_price, tp_price = self.broker.calculate_sl_tp_prices(
                s["direction"],
                s["price"],
                s["sl_pips"],
                s["tp_pips"],
                s["symbol"],
                units="pips",
            )

            min_stop = self.broker.get_min_stop_distance(s["symbol"])

            if abs(s["price"] - sl_price) < min_stop:
                sl_price = (
                    s["price"] + min_stop
                    if s["direction"] == "SELL"
                    else s["price"] - min_stop
                )

            if abs(s["price"] - tp_price) < min_stop:
                tp_price = (
                    s["price"] - min_stop
                    if s["direction"] == "SELL"
                    else s["price"] + min_stop
                )

            final_signals.append(
                {
                    "symbol": s["symbol"],
                    "direction": s["direction"],
                    "lot": lot,
                    "open_price": s["price"],
                    "sl": sl_price,
                    "tp": tp_price,
                    "profit": 0,
                    # keep HTF context (optional)
                    "m15_bias": s.get("m15_bias"),
                    "m5_confirm": s.get("m5_confirm"),
                    "m1_entry": s.get("m1_entry"),
                    "confidence": s.get("confidence"),
                }
            )

        print(f"Generated {len(final_signals)} trade signals")
        return final_signals

    def _mt5_timeframe(self):
        tf = getattr(Config, "TIMEFRAME", mt5.TIMEFRAME_M1)
        if isinstance(tf, int):
            return tf
        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "D1": mt5.TIMEFRAME_D1,
        }
        return mapping.get(str(tf).upper(), mt5.TIMEFRAME_M1)

    def _calculate_dynamic_sl_tp(self, candles, symbol: str):
        sl_price_dist, _tp_price_dist = self.market_data.calculate_dynamic_sl_tp(
            candles
        )
        pip = self.broker.get_pip_size(symbol)
        sl_pips = float(sl_price_dist) / pip if pip else float(sl_price_dist)
        tp_pips = sl_pips * 10.0
        return sl_pips, tp_pips
