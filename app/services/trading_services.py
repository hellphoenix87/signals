from datetime import datetime, timedelta
from app.config.settings import Config


class TradingService:
    def __init__(self, strategy, risk_manager, broker, market_data):
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.broker = broker
        self.market_data = market_data
        self.daily_profit = 0
        self.last_reset = datetime.now()

    def tick(self):
        print(f"TradingService.tick called at {datetime.now()}")
        """Single iteration of trading logic."""
        now = datetime.now()
        self._reset_daily_if_needed(now)

        account_info = self.market_data.get_account_info()
        account_balance = account_info.balance if account_info else 10000

        # 1. Generate signals with risk-spread
        signals = self.strategy.generate_signals(account_balance)
        print(f"Generated {len(signals)} signals")

        # 2. Execute signals via broker (moved here)
        for s in signals:
            print(f"Executing trade: {s['symbol']} {s['direction']} lot={s['lot']}")
            if s["direction"] == "BUY":
                self.broker.place_buy(s["symbol"], s["lot"], s["sl"], s["tp"])
            else:
                self.broker.place_sell(s["symbol"], s["lot"], s["sl"], s["tp"])

        # 3. Update daily profit
        self.daily_profit = self._calculate_daily_profit()

        # 4. Stop trading if daily target reached
        if self.daily_profit >= Config.DAILY_TARGET_PROFIT:
            self._close_all_trades()
            print("Daily target reached, stopping trades for today.")

    def _reset_daily_if_needed(self, now):
        if now - self.last_reset >= timedelta(days=1):
            self.daily_profit = 0
            self.risk_manager.reset_daily_risk()
            self.last_reset = now

    def _calculate_daily_profit(self):
        positions = self.broker.get_open_positions()
        if getattr(self.broker, "mode", None) in ("demo", "backtest"):
            # Simulated positions are dicts
            profit = sum(pos["profit"] for pos in positions)
        else:
            # Real MT5 positions are objects
            profit = sum(pos.profit for pos in positions)
        return profit
