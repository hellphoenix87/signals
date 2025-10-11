from enum import Enum


class TradingMode(str, Enum):
    LIVE = "live"
    DEMO = "demo"
    BACKTEST = "backtest"
