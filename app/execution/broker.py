from logging import info
import MetaTrader5 as mt5


def create_broker(mode):
    """Provider for DI wiring of Broker."""
    return Broker(mode)


class Broker:
    def __init__(self, mode):
        self.mode = mode
        self.open_positions_sim = []

        if self.mode in ("live", "backtest"):
            if not mt5.initialize():
                raise RuntimeError("MT5 initialization failed")
        info(f"Broker initialized in {self.mode} mode")

    def place_buy(self, symbol, lot, sl, tp, price=None):
        if self.mode == "backtest":
            self._backtest_trade(symbol, "BUY", lot, sl, tp, price)
        elif self.mode == "demo":
            self._simulate_trade(symbol, "BUY", lot, sl, tp)
        else:
            self._mt5_place_order(symbol, "BUY", lot, sl, tp)

    def place_sell(self, symbol, lot, sl, tp, price=None):
        if self.mode == "backtest":
            self._backtest_trade(symbol, "SELL", lot, sl, tp, price)
        elif self.mode == "demo":
            self._simulate_trade(symbol, "SELL", lot, sl, tp)
        else:
            self._mt5_place_order(symbol, "SELL", lot, sl, tp)

    def get_open_positions(self, symbol=None):
        if self.mode == "demo" or self.mode == "backtest":
            if symbol:
                return [p for p in self.open_positions_sim if p["symbol"] == symbol]
            return self.open_positions_sim
        else:
            import MetaTrader5 as mt5

            return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

    def _simulate_trade(self, symbol, direction, lot, sl, tp):
        # DEMO mode: use current tick price
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
        # BACKTEST mode: use provided historical price
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

    def _get_symbol_point(self, symbol):
        info = mt5.symbol_info(symbol)
        if info is None:
            return 0.0001  # fallback
        if "JPY" in symbol:
            return 0.01
        return info.point

    def _mt5_place_order(self, symbol, direction, lot, sl, tp):
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Symbol {symbol} not found")
            return None

        step = info.volume_step
        lot = max(info.volume_min, min(info.volume_max, round(lot / step) * step))
        lot = float(f"{lot:.2f}")

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"Failed to get tick for {symbol}")
            return None
        price = tick.ask if direction == "BUY" else tick.bid

        min_dist = info.trade_stops_level * info.point
        if sl and abs(price - sl) < min_dist:
            print(f"SL too close for {symbol}, adjusting")
            sl = price - min_dist if direction == "BUY" else price + min_dist
        if tp and abs(price - tp) < min_dist:
            print(f"TP too close for {symbol}, adjusting")
            tp = price + min_dist if direction == "BUY" else price - min_dist

        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

        for filling_mode in [
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_RETURN,
            mt5.ORDER_FILLING_IOC,
        ]:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 5,
                "magic": 123456,
                "comment": "Placed by Python",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }
            result = mt5.order_send(request)
            print(
                f"MT5 order result for {symbol} with filling_mode {filling_mode}: {result}"
            )
            if (
                result.retcode == mt5.TRADE_RETCODE_DONE
                or result.retcode == mt5.TRADE_RETCODE_PLACED
            ):
                return result
            if result.comment != "Unsupported filling mode":
                return result
        print(f"All filling modes failed for {symbol}")
        return None
