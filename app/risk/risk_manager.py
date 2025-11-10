import MetaTrader5 as mt5


def create_risk_manager(broker):
    """Provider for DI wiring of RiskManager."""
    return RiskManager(broker)


class RiskManager:
    def __init__(self, broker):
        self.broker = broker
        self.daily_risk_used = 0.0

    def reset_daily_risk(self):
        self.daily_risk_used = 0.0

    def calculate_lot_size(
        self, account_balance, sl_pips, symbol_price, symbol, risk_percent
    ):
        """Calculate lot size based on risk percent and SL pips for a given symbol."""
        print(
            f"Calculating lot size for {symbol} with SP {symbol_price} SL {sl_pips} pips and risk {risk_percent}%"
        )

        point = self.broker._get_symbol_point(symbol)
        risk_amount = account_balance * (risk_percent / 100)
        sl_distance = sl_pips * self.broker.get_lot_value(symbol)
        lot = risk_amount / sl_distance if sl_distance != 0 else 0

        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Failed to get symbol info for {symbol}")
            return 0

        lot = max(min(lot, info.volume_max), info.volume_min)

        return lot
