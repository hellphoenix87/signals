from logging import info
from typing import Optional, Any

import MetaTrader5 as mt5

from app.config.settings import Config


def create_broker(mode):
    """Provider for DI wiring of Broker."""
    return Broker(mode)


class Broker:
    def __init__(self, mode: str):
        self.mode = mode
        self.open_positions_sim: list[dict[str, Any]] = []

        # NEW: tickets for demo/backtest so ExitTrade can close by ticket
        self._sim_ticket_seq = 1

        # MT5 is required for live/backtest and also for demo if you want real ticks/info.
        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")

        info(f"Broker initialized in {self.mode} mode")

    # -----------------------------
    # Public trading API
    # -----------------------------

    def place_buy(self, symbol, lot, sl, tp, price=None):
        if self.mode == "backtest":
            return self._backtest_trade(symbol, "BUY", lot, sl, tp, price)
        if self.mode == "demo":
            return self._simulate_trade(symbol, "BUY", lot, sl, tp)
        return self._mt5_place_order(symbol, "BUY", lot, sl, tp)

    def place_sell(self, symbol, lot, sl, tp, price=None):
        if self.mode == "backtest":
            return self._backtest_trade(symbol, "SELL", lot, sl, tp, price)
        if self.mode == "demo":
            return self._simulate_trade(symbol, "SELL", lot, sl, tp)
        return self._mt5_place_order(symbol, "SELL", lot, sl, tp)

    def get_open_positions(self, symbol=None):
        if self.mode in ("demo", "backtest"):
            if symbol:
                return [p for p in self.open_positions_sim if p.get("symbol") == symbol]
            return self.open_positions_sim
        return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

    def close_position(
        self,
        *,
        ticket: Any,
        symbol: str,
        side: str,
        volume: Optional[float] = None,
        comment: str = "Exit",
    ):
        """
        Close an existing position.

        - ticket: MT5 position ticket (int-like)
        - symbol: symbol name
        - side: closing order side ("BUY" closes a SELL position; "SELL" closes a BUY position)
        - volume: if None, tries to read current position volume from MT5
        """
        if ticket is None or not symbol or not side:
            return None

        # Demo/backtest: remove simulated position by ticket
        if self.mode in ("demo", "backtest"):
            try:
                t = int(ticket)
            except Exception:
                return None

            before = len(self.open_positions_sim)
            self.open_positions_sim = [
                p for p in self.open_positions_sim if int(p.get("ticket", -1)) != t
            ]
            after = len(self.open_positions_sim)
            print(
                f"{self.mode} mode: closed ticket={ticket} symbol={symbol} removed={before - after} comment={comment}"
            )
            return True

        # Live: send opposite DEAL with position=ticket
        try:
            mt5.symbol_select(symbol, True)
        except Exception:
            pass

        # Resolve volume if not provided
        if volume is None:
            try:
                poss = mt5.positions_get(ticket=int(ticket))
                if poss:
                    volume = float(getattr(poss[0], "volume", 0.0) or 0.0)
            except Exception:
                volume = 0.0

        if volume is None or float(volume) <= 0:
            return None

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None

        side_u = str(side).upper()
        order_type = mt5.ORDER_TYPE_BUY if side_u == "BUY" else mt5.ORDER_TYPE_SELL
        price = (
            float(getattr(tick, "ask", 0.0) or 0.0)
            if side_u == "BUY"
            else float(getattr(tick, "bid", 0.0) or 0.0)
        )
        if price <= 0:
            return None
        price = self._normalize_price(symbol, price)

        # Normalize volume to broker constraints
        si = self.get_symbol_info(symbol)
        if si is None:
            return None
        step = float(si.volume_step)
        vmin = float(si.volume_min)
        vmax = float(si.volume_max)
        vol = max(vmin, min(vmax, round(float(volume) / step) * step))
        vol = float(f"{vol:.2f}")

        deviation = int(getattr(Config, "DEVIATION", 5) or 5)
        magic = int(getattr(Config, "MAGIC", 123456) or 123456)

        # Try multiple filling modes (same approach as _mt5_place_order)
        for filling_mode in (
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_RETURN,
            mt5.ORDER_FILLING_IOC,
        ):
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "position": int(ticket),  # IMPORTANT: closes this position (hedge-safe)
                "volume": vol,
                "type": order_type,
                "price": price,
                "deviation": deviation,
                "magic": magic,
                "comment": str(comment)[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }

            result = mt5.order_send(request)
            print(
                f"MT5 close result for {symbol} ticket={ticket} filling_mode={filling_mode}: {result}"
            )

            if result is None:
                continue

            if result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                return result

            if getattr(result, "comment", "") != "Unsupported filling mode":
                return result

        return None

    # -----------------------------
    # Simulation / backtest
    # -----------------------------

    def _simulate_trade(self, symbol, direction, lot, sl, tp):
        price = 1.0
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            price = tick.ask if direction == "BUY" else tick.bid

        ticket = self._sim_ticket_seq
        self._sim_ticket_seq += 1

        trade = {
            "ticket": ticket,
            "symbol": symbol,
            "direction": direction,
            "type": 0 if direction == "BUY" else 1,  # MT5-like
            "lot": float(lot),
            "volume": float(lot),
            "open_price": float(price),
            "price_open": float(price),  # for ExitTrade compatibility
            "sl": sl,
            "tp": tp,
            "profit": 0.0,
        }
        self.open_positions_sim.append(trade)
        print(
            f"Demo mode: {direction} {symbol} {lot} lots at {price} (ticket={ticket})"
        )
        return trade

    def _backtest_trade(self, symbol, direction, lot, sl, tp, price):
        if price is None:
            raise ValueError(
                "Backtest mode requires a historical price for simulation."
            )

        ticket = self._sim_ticket_seq
        self._sim_ticket_seq += 1

        trade = {
            "ticket": ticket,
            "symbol": symbol,
            "direction": direction,
            "type": 0 if direction == "BUY" else 1,  # MT5-like
            "lot": float(lot),
            "volume": float(lot),
            "open_price": float(price),
            "price_open": float(price),  # for ExitTrade compatibility
            "sl": sl,
            "tp": tp,
            "profit": 0.0,
        }
        self.open_positions_sim.append(trade)
        print(
            f"Backtest mode: {direction} {symbol} {lot} lots at {price} (ticket={ticket})"
        )
        return trade

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

        deviation = int(getattr(Config, "DEVIATION", 5) or 5)
        magic = int(getattr(Config, "MAGIC", 123456) or 123456)
        comment = str(
            getattr(Config, "MT5_COMMENT", "Placed by Python") or "Placed by Python"
        )[:31]

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
                "deviation": deviation,
                "magic": magic,
                "comment": comment,
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

            if result is not None and result.retcode in (
                mt5.TRADE_RETCODE_DONE,
                mt5.TRADE_RETCODE_PLACED,
            ):
                return result
            if result is not None and result.comment != "Unsupported filling mode":
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
