import datetime
import threading
import time
import MetaTrader5 as mt5
from typing import Any, Optional
from app.config.settings import Config
from app.data.market_data import MarketData


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
        tf_seconds = self._timeframe_seconds()
        last_candle_time = None

        # Initial pull: get full window
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
                last_candle_time = candles[-1]["time"]
                print(
                    f"[{self.symbol}] Initial candle window: {last_candle_time} (count={len(candles)})"
                )
        except Exception as e:
            print(f"[LiveCandleCollector] Error fetching initial candles: {e}")

        while self._running:
            try:
                # Fetch only the latest closed candle
                new_candle_list = self.market_data.get_symbol_data(
                    self.symbol, self.timeframe, num_bars=1, closed_only=True
                )
                if not new_candle_list:
                    time.sleep(1)
                    continue
                new_candle = new_candle_list[0]
                new_candle_time = new_candle["time"]

                # Only append if it's a new candle
                if last_candle_time is None or new_candle_time > last_candle_time:
                    with self._lock:
                        self.latest_candles.append(new_candle)
                        if len(self.latest_candles) > self.count:
                            self.latest_candles.pop(0)
                    last_candle_time = new_candle_time
                    print(
                        f"[{self.symbol}] New candle: {new_candle_time} (count={len(self.latest_candles)})"
                    )
            except Exception as e:
                print(f"[LiveCandleCollector] Error fetching candles: {e}")

            time.sleep(1)  # Poll every second for new candle


def create_multi_timeframe_candle_collector(
    symbol: str = "EURUSD",
    timeframes=None,
    count=None,
):
    """
    Provider for DI wiring of MultiTimeframeCandleCollector.
    """
    return MultiTimeframeCandleCollector(
        symbol=symbol,
        timeframes=timeframes,
        count=count,
    )


# NEW
class MultiTimeframeCandleCollector:
    """
    Collects candles for multiple MT5 timeframes for a single symbol.

    - Uses GTC polling aligned to each timeframe close (server epoch).
    - get_latest_candles() returns dict[timeframe] -> list[candles]
    """

    def __init__(self, symbol="EURUSD", timeframes=None, count=None):
        self.symbol = symbol
        self.timeframes = list(
            timeframes
            or getattr(
                Config,
                "TIMEFRAMES",
                [mt5.TIMEFRAME_M1, mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15],
            )
        )
        self.count = int(count or Config.MIN_CANDLES_FOR_INDICATORS)

        self.market_data = MarketData()
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        self._latest_by_tf: dict[int, list[dict]] = {tf: [] for tf in self.timeframes}
        self._last_bar_time_by_tf: dict[int, Any] = {tf: None for tf in self.timeframes}

        # schedule: next epoch second when we should refresh each tf
        self._next_due_by_tf: dict[int, int] = {tf: 0 for tf in self.timeframes}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._collect, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        t = self._thread
        self._thread = None
        if t:
            t.join(timeout=5)

    def get_latest_candles(self, timeframe: int | None = None):
        """
        - If timeframe is None: returns dict[tf] -> candles
        - Else: returns candles list for that timeframe
        """
        with self._lock:
            if timeframe is None:
                return {tf: list(cs) for tf, cs in self._latest_by_tf.items()}
            return list(self._latest_by_tf.get(timeframe, []))

    def _timeframe_seconds(self, tf: int) -> int:
        mapping = {
            mt5.TIMEFRAME_M1: 60,
            mt5.TIMEFRAME_M5: 5 * 60,
            mt5.TIMEFRAME_M15: 15 * 60,
            mt5.TIMEFRAME_M30: 30 * 60,
            mt5.TIMEFRAME_H1: 60 * 60,
        }
        return int(mapping.get(tf, 60))

    def _align_next_due(self, now_epoch: int, tf: int) -> int:
        tf_seconds = self._timeframe_seconds(tf)
        # next bar boundary + 1 second to allow bar to close
        return int(now_epoch - (now_epoch % tf_seconds) + tf_seconds + 1)

    def _candle_time_to_epoch(self, t: Any) -> Optional[int]:
        if t is None:
            return None
        if isinstance(t, (int, float)):
            return int(t)

        if isinstance(t, datetime.datetime):
            # IMPORTANT: treat naive datetimes as UTC (not local machine time)
            if t.tzinfo is None:
                t = t.replace(tzinfo=datetime.timezone.utc)
            else:
                t = t.astimezone(datetime.timezone.utc)
            return int(t.timestamp())

        if isinstance(t, datetime.date):
            # date without time -> midnight UTC best-effort
            return int(
                datetime.datetime(
                    t.year, t.month, t.day, tzinfo=datetime.timezone.utc
                ).timestamp()
            )

        try:
            ts = getattr(t, "timestamp", None)
            if callable(ts):
                return int(ts())
        except Exception:
            return None
        return None

    def _stamp_is_closed(self, candles: list[dict], tf: int, now_epoch: int) -> None:
        tf_seconds = self._timeframe_seconds(tf)
        for c in candles or []:
            if not isinstance(c, dict):
                continue
            te = self._candle_time_to_epoch(c.get("time"))
            if te is None:
                # if unknown, don't block trading (match your strategy’s default)
                c.setdefault("is_closed", True)
                continue
            c["is_closed"] = bool((te + tf_seconds) <= now_epoch)

    def _collect(self):
        for tf in self.timeframes:
            self._next_due_by_tf[tf] = 0

        while self._running:
            wall_epoch = int(time.time())
            tick = mt5.symbol_info_tick(self.symbol)
            tick_epoch = int(getattr(tick, "time", 0) or 0)

            now_epoch = wall_epoch
            stamp_epoch = tick_epoch or wall_epoch

            for tf in self.timeframes:
                if now_epoch < int(self._next_due_by_tf.get(tf, 0) or 0):
                    continue

                try:
                    candles = self.market_data.get_historical_candles(
                        self.symbol,
                        timeframe=tf,
                        start_pos=1,
                        count=self.count,
                        verbose=False,
                    )
                    for c in candles:
                        c["symbol"] = self.symbol
                    if candles:
                        self._stamp_is_closed(candles, tf, stamp_epoch)

                    with self._lock:
                        self._latest_by_tf[tf] = candles

                    if candles:
                        newest_closed_time = None
                        for c in reversed(candles):
                            if isinstance(c, dict) and c.get("is_closed") is True:
                                newest_closed_time = c.get("time")
                                break

                        newest_time = newest_closed_time or candles[-1].get("time")
                        last_time = self._last_bar_time_by_tf.get(tf)

                        # NEW: detect time jumps
                        last_ep = self._candle_time_to_epoch(last_time)
                        new_ep = self._candle_time_to_epoch(newest_time)
                        tf_s = self._timeframe_seconds(tf)
                        if (
                            last_ep is not None
                            and new_ep is not None
                            and (new_ep - last_ep) > tf_s
                        ):
                            print(
                                f"[{self.symbol}] TF={tf} candle jump: last={last_time} new={newest_time} "
                                f"(delta={new_ep - last_ep}s, tick_epoch={tick_epoch}, wall_epoch={wall_epoch})"
                            )

                        if last_time is None or newest_time != last_time:
                            print(
                                f"[{self.symbol}] New candle tf={tf}: {newest_time} (count={len(candles)})"
                            )
                            self._last_bar_time_by_tf[tf] = newest_time

                except Exception as e:
                    print(
                        f"[MultiTimeframeCandleCollector] Error fetching candles tf={tf}: {e}"
                    )

                self._next_due_by_tf[tf] = self._align_next_due(now_epoch, tf)

            next_due = (
                min(self._next_due_by_tf.values())
                if self._next_due_by_tf
                else (now_epoch + 1)
            )
            time.sleep(max(1, int(next_due - now_epoch)))


def create_candle_collector(
    symbol: str = "EURUSD",
    tf_entry=None,
    tf_confirm=None,
    tf_bias=None,
    count=None,
    config: Any = Config,
):
    """
    Factory: returns either single-timeframe or multi-timeframe candle collector based on config.
    """
    use_multi = getattr(config, "USE_MULTI_TIMEFRAME_SIGNALS", False)
    if use_multi:
        # Use all provided timeframes, or config defaults
        timeframes = [tf for tf in [tf_entry, tf_confirm, tf_bias] if tf is not None]
        if not timeframes:
            timeframes = [
                getattr(config, "TF_ENTRY", mt5.TIMEFRAME_M1),
                getattr(config, "TF_CONFIRM", mt5.TIMEFRAME_M5),
                getattr(config, "TF_BIAS", mt5.TIMEFRAME_M15),
            ]
        return create_multi_timeframe_candle_collector(
            symbol=symbol,
            timeframes=timeframes,
            count=count,
        )
    else:
        tf = tf_entry or getattr(config, "TF_ENTRY", mt5.TIMEFRAME_M1)
        return create_live_candle_collector(
            symbol=symbol,
            timeframe=tf,
            count=count,
        )
