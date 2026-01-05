from app.config.settings import Config
from app.data.market_data import create_market_data
from app.trade_execution.mode import TradingMode as Mode
from app.risk.risk_manager import create_risk_manager
from app.trade_execution.broker import create_broker
from app.trade_execution.trade_execution import create_trade_executor
from app.data.candles import create_candle_collector
from app.services.trade_services import create_orchestrator
from app.data.tick_collector import create_tick_collector
from app.trade_execution.helpers.prepare_trade import create_enter_trade
from app.exit_strategies.exit_trade import create_exit_trade
from app.signals.signal_generation import create_signal_strategy
import MetaTrader5 as mt5

md = create_market_data()
br = create_broker(Mode.LIVE)
rm = create_risk_manager(br)

tf_entry = int(getattr(Config, "TF_ENTRY", mt5.TIMEFRAME_M1))
tf_confirm = int(getattr(Config, "TF_CONFIRM", mt5.TIMEFRAME_M5))
tf_bias = int(getattr(Config, "TF_BIAS", mt5.TIMEFRAME_M15))

trade_executor = create_trade_executor(rm, br, md)
enter_trade = create_enter_trade(md, rm, br, trade_executor)  # <-- Wire TradeExecutor

orchestrators = {}
for symbol in getattr(Config, "SYMBOLS", ["EURUSD"]):
    collector = create_candle_collector(
        symbol=symbol,
        tf_entry=tf_entry,
        tf_confirm=tf_confirm,
        tf_bias=tf_bias,
        count=int(Config.MIN_CANDLES_FOR_INDICATORS) + 1,
        config=Config,
    )
    tick = create_tick_collector(symbol=symbol, interval=0.1)
    exit_trade = create_exit_trade(broker=br, risk_manager=rm)
    signal_generator = create_signal_strategy(config=Config)
    orchestrator = create_orchestrator(
        collector=collector,
        signal_generator=signal_generator,
        trading_service=trade_executor,
        tick_collector=tick,
        exit_trade=exit_trade,
        broker=br,
        enter_trade=enter_trade,
    )
    orchestrators[symbol] = orchestrator

# For backward compatibility, export the first orchestrator as signal_orchestrator
signal_orchestrator = next(iter(orchestrators.values()))

# Export orchestrators dict, trade_executor, br, rm, md for endpoints
