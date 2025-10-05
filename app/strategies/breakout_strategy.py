from app.config.settings import Config


class BreakoutStrategy:
    def __init__(self, market_data, risk_manager, broker):
        self.market_data = market_data
        self.risk_manager = risk_manager
        self.broker = broker
        self._last_scanned_symbols = []

    def get_last_scanned_symbols(self):
        return self._last_scanned_symbols

    def generate_signals(self, account_balance):
        """
        Scan symbols, detect breakouts, calculate lot sizes and SL/TP prices.
        Returns a list of trade signals (dicts).
        """
        # 1. Pick symbols dynamically
        symbols = self.market_data.scan_symbols(Config.MAX_SYMBOLS)
        self._last_scanned_symbols = symbols
        signals = []

        # 2. Analyze each symbol
        for symbol in symbols:
            tick = self.market_data.get_symbol_tick(symbol)
            if tick is None:
                continue
            price = tick.ask

            candles = self.market_data.get_symbol_data(
                symbol, timeframe=self._mt5_timeframe()
            )
            sl_pips, tp_pips = self._calculate_dynamic_sl_tp(candles)
            direction = self._check_breakout(price, candles)
            if direction is None:
                continue

            signals.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "price": price,
                    "sl_pips": sl_pips,
                    "tp_pips": tp_pips,
                }
            )

        # 3. Deduplicate by symbol
        unique_signals = list({s["symbol"]: s for s in signals}.values())

        # 4. Spread risk across all signals and calculate lot size
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
                symbol=s["symbol"],  # Pass symbol for pip calculation
                risk_percent=risk_per_signal,
            )
            if lot <= 0:
                continue

            # Convert SL/TP from pips → absolute prices
            sl_price, tp_price = self.broker.calculate_sl_tp_prices(
                s["direction"], s["price"], s["sl_pips"], s["tp_pips"], s["symbol"]
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
        import MetaTrader5 as mt5

        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
        }
        return mapping.get(Config.TIMEFRAME, mt5.TIMEFRAME_H1)

    def _calculate_dynamic_sl_tp(self, candles):
        return self.market_data.calculate_dynamic_sl_tp(candles)

    def _check_breakout(self, price, candles):
        high, low = self._opening_range(candles)
        buffer = Config.BREAKOUT_BUFFER_PIPS * 0.0001
        if price > high + buffer:
            return "BUY"
        elif price < low - buffer:
            return "SELL"
        return None

    def _opening_range(self, candles):
        period = Config.OPENING_RANGE_PERIOD
        opening_candles = candles[:period]
        high = max(c["high"] for c in opening_candles)
        low = min(c["low"] for c in opening_candles)
        return high, low
