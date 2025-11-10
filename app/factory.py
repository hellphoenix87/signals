from app.config.settings import Config
from app.data.market_data import create_market_data
from app.execution.mode import TradingMode as Mode
from app.risk.risk_manager import create_risk_manager
from app.execution.broker import create_broker
from app.strategies.breakout_strategy import create_breakout_strategy
from app.services.helpers.trade_execution import create_trade_executor
from app.services.helpers.live_collector import (
    create_live_candle_collector,
)  # <-- import here
from app.services.signal_orchestrator import create_orchestrator

md = create_market_data()
br = create_broker(Mode.BACKTEST)
rm = create_risk_manager(br)
strategy = create_breakout_strategy(md, rm, br)

collector = create_live_candle_collector(  # <-- use new provider
    symbol="EURUSD",
    timeframe=Config.TIMEFRAME,
    count=Config.MIN_CANDLES_FOR_INDICATORS,
    interval=60,
)

trade_executor = create_trade_executor(rm, br, md)
signal_orchestrator = create_orchestrator(
    collector=collector,
    signal_generator=strategy.strong_signal_strategy,
    trading_service=trade_executor,
)
