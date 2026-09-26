import MetaTrader5 as mt5

from app.config.symbols import config_for


def create_risk_manager(broker):
    """Provider for DI wiring of RiskManager."""
    return RiskManager(broker)


class RiskManager:
    """Computes position size from account risk percentage and stop distance."""

    def __init__(self, broker):
        self.broker = broker

    def calculate_lot_size(
        self, account_balance, sl_pips, symbol_price, symbol, risk_percent
    ):
        """Return a lot size sized so a full stop-loss hit risks `risk_percent`
        of `account_balance`, clamped to the symbol's volume min/max/step.

        `sl_pips` is floored to the symbol's `MIN_SL_PIPS` so an unrealistically
        tight stop can't inflate the computed lot size.
        """
        min_sl_pips = float(getattr(config_for(symbol), "MIN_SL_PIPS", 5.0) or 5.0)
        if sl_pips < min_sl_pips:
            print(
                f"SL pips too small for {symbol}, adjusting to minimum {min_sl_pips}."
            )
            sl_pips = min_sl_pips

        pip = self.broker.get_pip_size(symbol)
        contract_size = self.broker.get_lot_value(symbol)
        risk_amount = account_balance * (risk_percent / 100.0)

        sl_distance = float(sl_pips) * float(pip)

        lot = risk_amount / (sl_distance * contract_size) if sl_distance > 0 else 0.0

        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Failed to get symbol info for {symbol}")
            return 0.0

        lot = max(min(lot, info.volume_max), info.volume_min)
        lot = round(lot / info.volume_step) * info.volume_step
        lot = float(f"{lot:.2f}")

        print(
            f"Calculated lot size for {symbol}: {lot} "
            f"(risk_amount={risk_amount}, sl_pips={sl_pips}, pip={pip}, contract_size={contract_size})"
        )
        return lot
