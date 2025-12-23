from app.config.settings import Config
from app.data.market_data import create_market_data
from app.execution.mode import TradingMode as Mode
from app.risk.risk_manager import create_risk_manager
from app.execution.broker import create_broker
from app.strategies.enter_trade import create_breakout_strategy
from app.services.helpers.trade_execution import create_trade_executor
from app.services.helpers.candles import create_live_candle_collector
from app.services.trade_services import SignalOrchestrator, create_orchestrator
from app.services.helpers.tick_collector import create_tick_collector
from app.strategies.exit_trade import create_exit_trade

# NEW
import MetaTrader5 as mt5
from app.services.oco_straddle import OCOStraddleManager


def _timeframe_seconds(tf: int) -> int:
    mapping = {
        mt5.TIMEFRAME_M1: 60,
        mt5.TIMEFRAME_M5: 300,
        mt5.TIMEFRAME_M15: 900,
        mt5.TIMEFRAME_M30: 1800,
        mt5.TIMEFRAME_H1: 3600,
        mt5.TIMEFRAME_D1: 86400,
    }
    return int(mapping.get(int(tf), 60))


md = create_market_data()
br = create_broker(Mode.LIVE)
rm = create_risk_manager(br)
strategy = create_breakout_strategy(md, rm, br)

# Prefer driving collectors from the first configured symbol
_drive_symbol = (getattr(Config, "SYMBOLS", None) or ["EURUSD"])[0]

tick = create_tick_collector(symbol=_drive_symbol, interval=1)

collector = create_live_candle_collector(
    symbol=_drive_symbol,
    timeframe=Config.TIMEFRAME,
    count=Config.MIN_CANDLES_FOR_INDICATORS,
    interval=_timeframe_seconds(Config.TIMEFRAME),
)

# NEW: shared OCO manager instance (must be shared with orchestrator so on_tick() runs)
oco_manager = None
if bool(getattr(Config, "OCO_ENABLED", False)):
    oco_manager = OCOStraddleManager(broker=br)

trade_executor = create_trade_executor(rm, br, md, oco_manager=oco_manager)
exit_trade = create_exit_trade(broker=br, risk_manager=rm)

signal_orchestrator: SignalOrchestrator = create_orchestrator(
    collector=collector,
    signal_generator=strategy,
    trading_service=trade_executor,
    tick_collector=tick,
    exit_trade=exit_trade,
    oco_manager=oco_manager,
)
