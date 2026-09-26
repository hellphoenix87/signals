"""Composition root: builds every collaborator and wires one `SignalOrchestrator`
per symbol in `Config.SYMBOLS`, eagerly, at import time, in `Mode.LIVE`. Each
symbol's collector, strategy and exits use its own config (`config_for`)."""

from app.config.settings import Config
from app.config.symbols import config_for
from app.data.market_data import create_market_data
from app.trade_execution.mode import TradingMode as Mode
from app.risk.risk_manager import create_risk_manager
from app.trade_execution.broker import create_broker
from app.trade_execution.trade_execution import create_trade_executor
from app.data.candles import create_candle_collector
from app.services.trade_services import create_orchestrator
from app.data.tick_collector import create_tick_collector
from app.exit_strategies.exit_trade import create_exit_trade, ExitTradeConfig
from app.signals.signal_generation import strategy_factory
import MetaTrader5 as mt5

md = create_market_data()
br = create_broker(Mode.LIVE)
rm = create_risk_manager(br)

tf_entry = int(getattr(Config, "TF_ENTRY", mt5.TIMEFRAME_M1))
tf_confirm = int(getattr(Config, "TF_CONFIRM", mt5.TIMEFRAME_M5))
tf_bias = int(getattr(Config, "TF_BIAS", mt5.TIMEFRAME_M15))

trade_executor = create_trade_executor(rm, br, md)

orchestrators = {}
for symbol in getattr(Config, "SYMBOLS", ["EURUSD"]):
    cfg = config_for(symbol)
    collector = create_candle_collector(
        symbol=symbol,
        tf_entry=tf_entry,
        tf_confirm=tf_confirm,
        tf_bias=tf_bias,
        count=int(cfg.MIN_CANDLES_FOR_INDICATORS) + 1,
        config=cfg,
    )
    tick = create_tick_collector(symbol=symbol, interval=0.1)
    exit_trade = create_exit_trade(
        broker=br, risk_manager=rm, config=ExitTradeConfig.for_config(cfg)
    )
    signal_generator = strategy_factory(config=cfg, symbol=symbol)
    orchestrator = create_orchestrator(
        collector=collector,
        signal_generator=signal_generator,
        trade_executor=trade_executor,
        tick_collector=tick,
        exit_trade=exit_trade,
    )
    orchestrators[symbol] = orchestrator


def get_market_data():
    """Return the shared `MarketData` singleton."""
    return md


def get_broker():
    """Return the shared `Broker` singleton."""
    return br


def get_trade_executor():
    """Return the shared `TradeExecutor` singleton."""
    return trade_executor


def get_orchestrators():
    """Return the `{symbol: SignalOrchestrator}` dict."""
    return orchestrators
