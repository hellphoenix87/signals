import datetime
from typing import Any, List, Optional, Union

from app.signals.strategies.base_signal_strategy import BaseSignalStrategy


class SpreadWaitEntryStrategy(BaseSignalStrategy):
    """
    Holds a buy/sell signal until a tick with a tight enough spread arrives,
    instead of entering (or skipping) at the signal candle's close.

    A zero-spread entry is born at breakeven (`is_break_even` is
    `profit >= 0`), so it can never hit the 30-tick pre-breakeven timeout --
    which is ~90% of this system's net loss, and mostly just the cost of
    crossing the spread. See docs/plans/in-progress/spread-wait-entry.md.

    Lifecycle: `generate_signal` turns a wrapped buy/sell into "hold" and
    marks it pending with a deadline (signal candle close + wait). Each
    `on_new_tick` either expires it (tick past the deadline) or confirms it
    (spread <= `max_spread_points`). The orchestrator executes whatever
    `get_confirmed_signal` returns. A tick without a spread never confirms:
    no spread reading, no entry.
    """

    def __init__(
        self,
        strategy: BaseSignalStrategy,
        max_spread_points: float,
        max_wait_seconds: float,
        entry_tf_seconds: int,
        logger: Optional[Any] = None,
    ):
        """
        Args:
            strategy: The wrapped strategy.
            max_spread_points: Largest spread (MT5 points) that confirms entry.
            max_wait_seconds: How long after the signal candle's close to wait.
            entry_tf_seconds: Entry timeframe length, to derive the candle close
                from the candle's open time.
            logger: Optional logger; defaults to the wrapped strategy's.
        """
        self.strategy = strategy
        self.max_spread_points = float(max_spread_points)
        self.max_wait_seconds = float(max_wait_seconds)
        self.entry_tf_seconds = int(entry_tf_seconds)
        self.logger = logger or getattr(strategy, "logger", None)
        self._pending: Optional[dict] = None
        self._candle_close: Optional[datetime.datetime] = None
        self._deadline: Optional[datetime.datetime] = None
        self._confirmed_signal: Optional[dict] = None

    def __getattr__(self, name):
        """Forward anything not defined here to the wrapped strategy, so this
        decorator stays transparent regardless of wrapper order."""
        return getattr(self.strategy, name)

    @staticmethod
    def _last_candle_time(candles: Union[List[dict], dict]) -> Optional[datetime.datetime]:
        if isinstance(candles, list) and candles:
            return candles[-1].get("time")
        if isinstance(candles, dict):
            return candles.get("time")
        return None

    def _log(self, msg: str) -> None:
        if self.logger:
            self.logger.info(f"[SpreadWait] {msg}")

    @property
    def is_waiting(self) -> bool:
        return self._pending is not None

    def generate_signal(self, candles, *args, **kwargs):
        result = self.strategy.generate_signal(candles, *args, **kwargs)
        if not isinstance(result, dict) or result.get("error"):
            return result
        if result.get("final_signal") not in ("buy", "sell"):
            return result

        candle_time = self._last_candle_time(candles)
        if candle_time is None:
            # Can't set a deadline without knowing when the candle closed; pass
            # the signal through untouched rather than drop it.
            return result
        self._pending = dict(result)
        self._candle_close = candle_time + datetime.timedelta(seconds=self.entry_tf_seconds)
        self._deadline = self._candle_close + datetime.timedelta(seconds=self.max_wait_seconds)
        self._confirmed_signal = None
        self._log(f"pending {result['final_signal']} until {self._deadline}")
        return {**result, "final_signal": "hold", "reason": "waiting_for_spread"}

    def on_new_tick(
        self,
        price: Optional[float] = None,
        spread_points: Optional[float] = None,
        tick_time: Optional[datetime.datetime] = None,
    ) -> None:
        """Expire or confirm the pending signal against one tick.

        `tick_time` is the tick's candle-frame datetime
        (`datetime.fromtimestamp(tick.time)`), the same basis as candle times.
        """
        if self._pending is None:
            return
        if tick_time is not None and self._deadline is not None and tick_time > self._deadline:
            self._log(f"spread_wait_expired ({self._pending.get('final_signal')})")
            self._clear()
            return
        if spread_points is None or float(spread_points) > self.max_spread_points:
            return
        waited = None
        if tick_time is not None and self._candle_close is not None:
            waited = (tick_time - self._candle_close).total_seconds()
        self._confirmed_signal = {
            **self._pending,
            "reason": "spread_wait_confirmed",
            "spread_wait_seconds": waited,
            "entry_spread_points": float(spread_points),
        }
        self._log(f"spread_wait_confirmed after {waited}s at spread {spread_points}")
        self._clear()

    def get_confirmed_signal(self, tick_time: Optional[datetime.datetime] = None):
        sig = self._confirmed_signal
        self._confirmed_signal = None
        return sig

    def _clear(self) -> None:
        self._pending = None
        self._candle_close = None
        self._deadline = None
