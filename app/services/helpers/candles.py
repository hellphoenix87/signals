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
        self.interval = interval or 60

        self.market_data = MarketData()
        self.latest_candles = []

        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Idempotent start."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._collect, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background collection thread."""
        self._running = False
        t = self._thread
        self._thread = None
        if t:
            t.join(timeout=5)

    def get_latest_candles(self):
        """Return a snapshot of the latest candles (thread-safe)."""
        with self._lock:
            return list(self.latest_candles)

    def _timeframe_seconds(self) -> int:
        # Minimal mapping; extend if you use other timeframes.
        tf = self.timeframe
        mapping = {
            mt5.TIMEFRAME_M1: 60,
            mt5.TIMEFRAME_M5: 5 * 60,
            mt5.TIMEFRAME_M15: 15 * 60,
            mt5.TIMEFRAME_M30: 30 * 60,
            mt5.TIMEFRAME_H1: 60 * 60,
        }
        return int(mapping.get(tf, self.interval or 60))

    def _collect(self):
        last_candle_time = None
        tf_seconds = self._timeframe_seconds()

        while self._running:
            try:
                candles = self.market_data.get_historical_candles(
                    self.symbol,
                    timeframe=self.timeframe,
                    start_pos=1,
                    count=self.count,
                    verbose=False,
                )

                with self._lock:
                    self.latest_candles = candles

                if candles:
                    # candles MUST be chronological for this to work (oldest->newest)
                    newest_time = candles[-1]["time"]
                    if last_candle_time is None or newest_time != last_candle_time:
                        print(
                            f"[{self.symbol}] New candle: {newest_time} (count={len(candles)})"
                        )
                        last_candle_time = newest_time

            except Exception as e:
                print(f"[LiveCandleCollector] Error fetching candles: {e}")

            tick = mt5.symbol_info_tick(self.symbol)
            if tick and getattr(tick, "time", None):
                # Align to next bar boundary using server epoch seconds
                now_epoch = int(tick.time)
                seconds_to_next_bar = tf_seconds - (now_epoch % tf_seconds)
                sleep_time = max(
                    1, int(seconds_to_next_bar) + 1
                )  # +1s to let bar close
                time.sleep(sleep_time)
            else:
                time.sleep(tf_seconds)
