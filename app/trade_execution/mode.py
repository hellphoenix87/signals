from enum import Enum


class TradingMode(str, Enum):
    LIVE = "live"
    BACKTEST = "backtest"
