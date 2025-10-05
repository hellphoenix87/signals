import MetaTrader5 as mt5


class Broker:
    def __init__(self, demo_mode=True):
        self.demo_mode = demo_mode
        self.open_positions_sim = []  # For paper trading simulation

        if not demo_mode:
            if not mt5.initialize():
                raise RuntimeError("MT5 initialization failed")

    def place_buy(self, symbol, lot, sl, tp):
        if self.demo_mode:
            self._simulate_trade(symbol, "BUY", lot, sl, tp)
        else:
            self._mt5_place_order(symbol, "BUY", lot, sl, tp)

    def place_sell(self, symbol, lot, sl, tp):
        if self.demo_mode:
            self._simulate_trade(symbol, "SELL", lot, sl, tp)
        else:
            self._mt5_place_order(symbol, "SELL", lot, sl, tp)

    def get_open_positions(self, symbol=None):
        if self.demo_mode:
            if symbol:
                return [p for p in self.open_positions_sim if p["symbol"] == symbol]
            return self.open_positions_sim
        else:
            return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

    def close_all_positions(self):
        if self.demo_mode:
            print("Paper trading: closing all positions")
            self.open_positions_sim.clear()
        else:
            positions = mt5.positions_get()
            if positions:
                for pos in positions:
                    mt5.order_close(
                        pos.ticket, pos.volume, mt5.symbol_info_tick(pos.symbol).bid, 5
                    )

    def calculate_sl_tp_prices(self, direction, price, sl_pips, tp_pips, symbol):
        point = self._get_symbol_point(symbol)
        if direction == "BUY":
            sl_price = price - sl_pips * point
            tp_price = price + tp_pips * point
        else:
            sl_price = price + sl_pips * point
            tp_price = price - tp_pips * point
        return sl_price, tp_price

    def get_lot_value(self, symbol):
        info = mt5.symbol_info(symbol)
        if info is None:
            return 100_000  # fallback
        return info.trade_contract_size  # usually 100k for Forex

    def _simulate_trade(self, symbol, direction, lot, sl, tp):
        """Simulate a trade in paper trading mode."""
        price = 1.0  # default placeholder, can use last tick
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
        print(f"Paper trading: {direction} {symbol} {lot} lots at {price}")

    def _get_symbol_point(self, symbol):
        info = mt5.symbol_info(symbol)
        if info is None:
            return 0.0001  # fallback
        if "JPY" in symbol:
            return 0.01
        return info.point
