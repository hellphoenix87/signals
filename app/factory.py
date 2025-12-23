from app.config.settings import Config
from app.data.market_data import create_market_data
from app.execution.mode import TradingMode as Mode
from app.risk.risk_manager import create_risk_manager
from app.execution.broker import create_broker
from app.strategies.enter_trade import create_breakout_strategy
from app.services.helpers.trade_execution import create_trade_executor
from app.services.helpers.candles import (
    create_live_candle_collector,
)
from app.services.trade_services import SignalOrchestrator, create_orchestrator
from app.services.helpers.tick_collector import create_tick_collector
from app.strategies.exit_trade import create_exit_trade

md = create_market_data()
br = create_broker(Mode.LIVE)
rm = create_risk_manager(br)
strategy = create_breakout_strategy(md, rm, br)
tick = create_tick_collector(symbol="EURUSD", interval=1)

collector = create_live_candle_collector(
    symbol="EURUSD",
    timeframe=Config.TIMEFRAME,
    count=Config.MIN_CANDLES_FOR_INDICATORS,
    interval=60,
)

trade_executor = create_trade_executor(rm, br, md)
exit_trade = create_exit_trade(broker=br, risk_manager=rm)
signal_orchestrator: SignalOrchestrator = create_orchestrator(
    collector=collector,
    signal_generator=strategy,
    trading_service=trade_executor,
    tick_collector=tick,
    exit_trade=exit_trade,
)
