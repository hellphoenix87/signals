from datetime import datetime, timedelta
from app.config.settings import Config


def create_trade_executor(risk_manager, broker, market_data):
    """Provider for DI wiring of TradeExecutor."""
    return TradeExecutor(risk_manager, broker, market_data)


class TradeExecutor:
    def __init__(self, risk_manager, broker, market_data):
        self.risk_manager = risk_manager
        self.broker = broker
        self.market_data = market_data
        self.daily_profit = 0
        self.last_reset = datetime.now()
        self.candle_counter = 0  # Add this attribute

    def execute_signals(self, signals):
        print(f"TradeExecutor.execute_signals called at {datetime.now()}")

        # Increment candle counter each time signals are processed
        self.candle_counter += 1

        # Skip trades for first 2-3 candles
        if self.candle_counter <= 3:
            print(
                f"Skipping trades for candle {self.candle_counter} (stabilizing indicators)"
            )
            return

        if signals:
            for s in signals:
                print(f"Executing trade: {s['symbol']} {s['direction']} lot={s['lot']}")
                if s["direction"] == "BUY":
                    self.broker.place_buy(s["symbol"], s["lot"], s["sl"], s["tp"])
                else:
                    self.broker.place_sell(s["symbol"], s["lot"], s["sl"], s["tp"])
        else:
            print("No actionable signals, no trades executed.")

        self.daily_profit = self._calculate_daily_profit()
        if self.daily_profit >= Config.DAILY_TARGET_PROFIT:
            self._close_all_trades()
            print("Daily target reached, stopping trades for today.")
