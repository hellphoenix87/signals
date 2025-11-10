import threading
import time
import MetaTrader5 as mt5
from app.config.settings import Config
from app.data.market_data import MarketData
import datetime


def create_live_candle_collector(
    symbol: str = "EURUSD",
    timeframe=None,
    count=None,
    interval: int = 60,
):
    """
    Provider for DI wiring of LiveCandleCollector.
    Returns a LiveCandleCollector instance (does not start it).
    """
    return LiveCandleCollector(
        symbol=symbol,
        timeframe=timeframe,
        count=count,
        interval=interval,
    )


class LiveCandleCollector:
    def __init__(self, symbol="EURUSD", timeframe=None, count=None, interval=None):
        self.symbol = symbol
        self.timeframe = timeframe or Config.TIMEFRAME
        self.count = count or Config.MIN_CANDLES_FOR_INDICATORS
        self.interval = interval or 60  # seconds, default for M1
        self.market_data = MarketData()
        self.latest_candles = []
        self._running = False
        self._thread = None

    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._collect, daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

    def _collect(self):
        last_candle_time = None
        while self._running:
            try:
                candles = self.market_data.get_historical_candles(
                    self.symbol,
                    timeframe=self.timeframe,
                    start_pos=0,
                    count=self.count,
                )
                self.latest_candles = candles
                if candles and (
                    last_candle_time is None or candles[-1]["time"] != last_candle_time
                ):
                    print(
                        f"[{self.symbol}] get_historical_candles fetched: {len(candles)} candles"
                    )
                    last_candle_time = candles[-1]["time"]
            except Exception as e:
                print(f"[LiveCandleCollector] Error fetching candles: {e}")

            tick = mt5.symbol_info_tick("EURUSD")
            if tick:
                server_time = datetime.datetime.fromtimestamp(tick.time)
                seconds_to_next_minute = 60 - server_time.second
                sleep_time = max(1, seconds_to_next_minute)
                time.sleep(sleep_time)
            else:
                # fallback if tick is None
                time.sleep(60)

    def get_latest_candles(self):
        return self.latest_candles
