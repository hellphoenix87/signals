from logging import info
import MetaTrader5 as mt5

from app.config.settings import Config


def create_broker(mode):
    """Provider for DI wiring of Broker."""
    return Broker(mode)


class Broker:
    def __init__(self, mode: str):
        self.mode = mode
        self.open_positions_sim = []

        # MT5 is required for live/backtest and also for demo if you want real ticks/info.
        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")

        info(f"Broker initialized in {self.mode} mode")

    # -----------------------------
    # Public trading API
    # -----------------------------

    def place_buy(self, symbol, lot, sl, tp, price=None):
        if self.mode == "backtest":
            self._backtest_trade(symbol, "BUY", lot, sl, tp, price)
        elif self.mode == "demo":
            self._simulate_trade(symbol, "BUY", lot, sl, tp)
        else:
            return self._mt5_place_order(symbol, "BUY", lot, sl, tp)

    def place_sell(self, symbol, lot, sl, tp, price=None):
        if self.mode == "backtest":
            self._backtest_trade(symbol, "SELL", lot, sl, tp, price)
        elif self.mode == "demo":
            self._simulate_trade(symbol, "SELL", lot, sl, tp)
        else:
            return self._mt5_place_order(symbol, "SELL", lot, sl, tp)

    def get_open_positions(self, symbol=None):
        if self.mode in ("demo", "backtest"):
            if symbol:
                return [p for p in self.open_positions_sim if p["symbol"] == symbol]
            return self.open_positions_sim
        return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

    # -----------------------------
    # Simulation / backtest
    # -----------------------------

    def _simulate_trade(self, symbol, direction, lot, sl, tp):
        price = 1.0
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            price = tick.ask if direction == "BUY" else tick.bid

        trade = {
            "symbol": symbol,
            "direction": direction,
            "lot": lot,
            "open_price": price,
            "sl": sl,
            "tp": tp,
            "profit": 0.0,
        }
        self.open_positions_sim.append(trade)
        print(f"Demo mode: {direction} {symbol} {lot} lots at {price}")

    def _backtest_trade(self, symbol, direction, lot, sl, tp, price):
        if price is None:
            raise ValueError(
                "Backtest mode requires a historical price for simulation."
            )

        trade = {
            "symbol": symbol,
            "direction": direction,
            "lot": lot,
            "open_price": price,
            "sl": sl,
            "tp": tp,
            "profit": 0.0,
        }
        self.open_positions_sim.append(trade)
        print(f"Backtest mode: {direction} {symbol} {lot} lots at {price}")

    # -----------------------------
    # Symbol helpers
    # -----------------------------

    def get_symbol_info(self, symbol):
        return mt5.symbol_info(symbol)

    def get_point_size(self, symbol: str) -> float:
        """Returns MT5 'point' (minimum price increment)."""
        si = self.get_symbol_info(symbol)
        if si is None:
            # Conservative FX fallback
            return 0.00001 if "JPY" not in symbol else 0.001
        return float(si.point)

    def get_pip_size(self, symbol: str) -> float:
        """
        Returns the price value of 1 pip.
        Common FX:
          - 5 digits => pip = 10 * point
          - 3 digits => pip = 10 * point
          - otherwise => pip = point
        """
        si = self.get_symbol_info(symbol)
        if si is None:
            return 0.0001 if "JPY" not in symbol else 0.01

        digits = getattr(si, "digits", None)
        point = float(si.point)
        if digits in (3, 5):
            return point * 10.0
        return point

    def get_min_stop_distance(self, symbol: str) -> float:
        """Returns minimum SL/TP distance in price units."""
        si = self.get_symbol_info(symbol)
        if si is None:
            # Fallback: 2 points
            return 2.0 * self.get_point_size(symbol)
        return float(si.trade_stops_level) * float(si.point)

    # Backward-compatible name (CONSISTENT: returns point size only)
    def _get_symbol_point(self, symbol):
        return self.get_point_size(symbol)

    def _digits(self, symbol: str) -> int:
        si = self.get_symbol_info(symbol)
        if si is None:
            return 5
        return int(getattr(si, "digits", 5))

    def _normalize_price(self, symbol: str, value: float) -> float:
        """Round to symbol digits to avoid MT5 'Invalid stops' from float noise."""
        digits = self._digits(symbol)
        return float(f"{float(value):.{digits}f}")

    # -----------------------------
    # MT5 execution
    # -----------------------------

    def _mt5_place_order(self, symbol, direction, lot, sl, tp):
        si = self.get_symbol_info(symbol)
        if si is None:
            print(f"Symbol {symbol} not found")
            return None

        # Normalize volume to broker constraints
        step = float(si.volume_step)
        vmin = float(si.volume_min)
        vmax = float(si.volume_max)
        lot = max(vmin, min(vmax, round(float(lot) / step) * step))
        lot = float(f"{lot:.2f}")

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"Failed to get tick for {symbol}")
            return None

        # Use live tick price
        price = tick.ask if direction == "BUY" else tick.bid
        price = self._normalize_price(symbol, price)

        # Normalize "no stops" to None
        sl = None if sl in (None, 0, 0.0) else self._normalize_price(symbol, float(sl))
        tp = None if tp in (None, 0, 0.0) else self._normalize_price(symbol, float(tp))

        min_dist = float(self.get_min_stop_distance(symbol))

        # Enforce minimum distance (price units) against live tick
        if sl is not None and abs(price - sl) < min_dist:
            print(f"SL too close for {symbol}, adjusting")
            sl = price - min_dist if direction == "BUY" else price + min_dist
            sl = self._normalize_price(symbol, sl)

        if tp is not None and abs(price - tp) < min_dist:
            print(f"TP too close for {symbol}, adjusting")
            tp = price + min_dist if direction == "BUY" else price - min_dist
            tp = self._normalize_price(symbol, tp)

        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

        for filling_mode in (
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_RETURN,
            mt5.ORDER_FILLING_IOC,
        ):
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": price,
                "deviation": 5,
                "magic": 123456,
                "comment": "Placed by Python",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }
            if sl is not None:
                request["sl"] = sl
            if tp is not None:
                request["tp"] = tp

            result = mt5.order_send(request)
            print(
                f"MT5 order result for {symbol} with filling_mode {filling_mode}: {result}"
            )

            if result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                return result
            if result.comment != "Unsupported filling mode":
                return result

        print(f"All filling modes failed for {symbol}")
        return None

    # -----------------------------
    # Misc helpers used by risk/strategy
    # -----------------------------

    def get_lot_value(self, symbol, price=None):
        si = self.get_symbol_info(symbol)
        if si and hasattr(si, "trade_contract_size"):
            return float(si.trade_contract_size)
        return 100000.0

    def calculate_sl_tp_prices(
        self,
        direction,
        price,
        sl_pips,
        tp_pips,
        symbol,
        units: str = "pips",  # <-- was "points"
    ):
        """
        Convert SL/TP distances to absolute prices.

        units:
          - "points" (distance units are MT5 points)
          - "pips"   (distance units are true pips, uses get_pip_size)
        """
        if units not in ("points", "pips"):
            raise ValueError("units must be 'points' or 'pips'")

        # Safety clamp so upstream "min SL pips" actually applies to real orders
        if units == "pips":
            min_sl = float(getattr(Config, "MIN_SL_PIPS", 5.0) or 5.0)
            sl_pips = max(float(sl_pips), min_sl)
            tp_pips = max(0.0, float(tp_pips))

        step = (
            self.get_point_size(symbol)
            if units == "points"
            else self.get_pip_size(symbol)
        )

        sl_distance = float(sl_pips) * float(step)
        tp_distance = float(tp_pips) * float(step)

        if direction == "BUY":
            sl_price = float(price) - sl_distance
            tp_price = float(price) + tp_distance
        else:  # SELL
            sl_price = float(price) + sl_distance
            tp_price = float(price) - tp_distance

        sl_price = self._normalize_price(symbol, sl_price)
        tp_price = self._normalize_price(symbol, tp_price)
        return sl_price, tp_price
