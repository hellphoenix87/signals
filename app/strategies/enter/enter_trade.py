from app.config.settings import Config
import MetaTrader5 as mt5


def create_enter_trade(market_data, risk_manager, broker, trade_executor):
    """Provider for DI wiring of EnterTrade."""
    return EnterTrade(market_data, risk_manager, broker, trade_executor)


class EnterTrade:
    """
    Handles trade entry logic after a valid signal is received.
    Calculates lot size, SL/TP, and places the trade via TradeExecutor.
    """

    def __init__(self, market_data, risk_manager, broker, trade_executor):
        self.market_data = market_data
        self.risk_manager = risk_manager
        self.broker = broker
        self.trade_executor = trade_executor

    def enter_trade(self, signal: dict, account_balance: float):
        """
        Places a trade using the provided signal dict via TradeExecutor.
        Signal dict must contain: symbol, direction, price, sl_pips, tp_pips.
        """
        symbol = signal.get("symbol")
        direction = signal.get("direction")
        price = signal.get("price")
        sl_pips = signal.get("sl_pips")
        tp_pips = signal.get("tp_pips")

        if not all([symbol, direction, price, sl_pips, tp_pips]):
            print(f"[EnterTrade] Missing required signal fields: {signal}")
            return None

        # Calculate lot size
        lot = self.risk_manager.calculate_lot_size(
            account_balance,
            sl_pips,
            symbol_price=price,
            symbol=symbol,
            risk_percent=Config.LOT_RISK_PERCENT,
        )
        if lot <= 0:
            print(f"[EnterTrade] Calculated lot size is zero for {symbol}")
            return None

        # Calculate SL/TP prices
        sl_price, tp_price = self.broker.calculate_sl_tp_prices(
            direction,
            price,
            sl_pips,
            tp_pips,
            symbol,
            units="pips",
        )

        # Enforce minimum stop distance
        min_stop = self.broker.get_min_stop_distance(symbol)
        if abs(price - sl_price) < min_stop:
            sl_price = price + min_stop if direction == "SELL" else price - min_stop
        if abs(price - tp_price) < min_stop:
            tp_price = price - min_stop if direction == "SELL" else price + min_stop

        # Prepare signal for TradeExecutor
        trade_signal = {
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "lot": lot,
            "sl_pips": sl_pips,
            "tp_pips": tp_pips,
            "sl": sl_price,
            "tp": tp_price,
        }

        # Place the trade via TradeExecutor
        result = self.trade_executor.process_signal(trade_signal)
        print(
            f"[EnterTrade] Placed trade via TradeExecutor: {symbol} {direction} lot={lot} SL={sl_price} TP={tp_price}"
        )
        return result

    def _calculate_dynamic_sl_tp(self, candles, symbol: str):
        """
        Utility to calculate dynamic SL/TP pip distances from market data.
        """
        sl_price_dist, _tp_price_dist = self.market_data.calculate_dynamic_sl_tp(
            candles
        )
        pip = self.broker.get_pip_size(symbol)
        sl_pips = float(sl_price_dist) / pip if pip else float(sl_price_dist)
        tp_pips = sl_pips * 10.0
        return sl_pips, tp_pips
