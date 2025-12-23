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
        MIN_SL_PIPS = 5

        # Ensure SL is in pips
        if sl_pips < MIN_SL_PIPS:
            print(
                f"SL pips too small for {symbol}, adjusting to minimum {MIN_SL_PIPS}."
            )
            sl_pips = MIN_SL_PIPS

        pip = self.broker.get_pip_size(
            symbol
        )  # <-- true pip size (price units per pip)
        contract_size = self.broker.get_lot_value(symbol)
        risk_amount = account_balance * (risk_percent / 100.0)

        # SL distance in price units
        sl_distance = float(sl_pips) * float(pip)

        lot = risk_amount / (sl_distance * contract_size) if sl_distance > 0 else 0.0

        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Failed to get symbol info for {symbol}")
            return 0.0

        # Clamp lot size to broker limits
        lot = max(min(lot, info.volume_max), info.volume_min)
        lot = round(lot / info.volume_step) * info.volume_step
        lot = float(f"{lot:.2f}")

        print(
            f"Calculated lot size for {symbol}: {lot} "
            f"(risk_amount={risk_amount}, sl_pips={sl_pips}, pip={pip}, contract_size={contract_size})"
        )
        return lot
