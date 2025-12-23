from app.config.settings import Config
from app.services.helpers.signal_generation import StrongSignalStrategy
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
        self.strong_signal_strategy = StrongSignalStrategy()

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

        candle_count = getattr(Config, "CANDLE_COUNT", 500)

        for symbol in symbols:
            # Get candles (live or historical)
            if getattr(self.broker, "mode", None) == "backtest":
                candles = self.market_data.get_historical_candles(
                    symbol,
                    timeframe=self._mt5_timeframe(),
                    start_pos=1,
                    count=candle_count,
                )
            else:
                candles = self.market_data.get_symbol_data(
                    symbol,
                    timeframe=self._mt5_timeframe(),
                    num_bars=candle_count,
                    closed_only=True,
                )

            min_candles = getattr(Config, "MIN_CANDLES_FOR_INDICATORS", 200)
            if not candles or len(candles) < min_candles:
                print(
                    f"[{symbol}] Not enough candles: {len(candles)} (required: {min_candles})"
                )
                continue

            # Use StrongSignalStrategy to generate signal
            signal_result = self.strong_signal_strategy.generate_signal(candles)
            print(f"[{symbol}] Signal result: {signal_result}")
            final_signal = signal_result.get("final_signal")
            if final_signal not in ("buy", "sell"):
                print(f"[{symbol}] No trading signal generated")
                continue

            price = candles[-1]["close"]  # Use last candle's close price

            sl_pips, tp_pips = self._calculate_dynamic_sl_tp(candles, symbol)
            direction = "BUY" if final_signal == "buy" else "SELL"

            signals.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "price": price,
                    "sl_pips": sl_pips,
                    "tp_pips": tp_pips,
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
            # --- Minimum stop distance check (use Broker helper, avoids fallback mismatch) ---
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
            # --- End minimum stop distance check ---

            final_signals.append(
                {
                    "symbol": s["symbol"],
                    "direction": s["direction"],
                    "lot": lot,
                    "open_price": s["price"],
                    "sl": sl_price,
                    "tp": tp_price,
                    "profit": 0,
                }
            )
        print(f"Generated {len(final_signals)} trade signals")

        return final_signals

    def _can_trade_now(self, current_time, daily_profit):
        if daily_profit >= Config.DAILY_TARGET_PROFIT:
            return False
        if (
            current_time.time() < Config.SESSION_START_TIME
            or current_time.time() > Config.SESSION_END_TIME
        ):
            return False
        return True

    def _mt5_timeframe(self):

        tf = getattr(Config, "TIMEFRAME", mt5.TIMEFRAME_M1)

        # If Config.TIMEFRAME is already an MT5 constant, return it.
        if isinstance(tf, int):
            return tf

        # Otherwise accept strings like "M1", "M5", etc.
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
        """
        MarketData returns price distances (high-low). Convert to TRUE pips here.
        """
        sl_price_dist, _tp_price_dist = self.market_data.calculate_dynamic_sl_tp(
            candles
        )

        pip = self.broker.get_pip_size(symbol)  # price value of 1 pip
        sl_pips = float(sl_price_dist) / pip if pip else float(sl_price_dist)

        # Keep broker TP far away so internal ExitTrade can do the real exiting
        tp_pips = sl_pips * 10.0

        return sl_pips, tp_pips
