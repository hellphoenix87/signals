import datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo
from app.signals.strategies.base_signal_strategy import BaseSignalStrategy


class SessionFilteredSignalStrategy(BaseSignalStrategy):
    """
    Wraps another strategy and forces "hold" during configured blocked
    UTC hours, regardless of the underlying signal.

    Motivated by backtest analysis (docs/test-results/session-filter-analysis.md):
    MTF's win rate is far worse during 08:00-18:59 UTC (London open
    through the NY session) than outside it, reproducibly across two
    independent windows -- likely because its bias/confirm/ADX gating
    gets whipsawed by the faster, choppier moves in the most heavily-
    traded hours. For single-timeframe the fixed target/stop proxy found
    no effect, but the full-lifecycle W3 smoke test found the OPPOSITE:
    the blocked hours were the better STF trades -- see Test M in
    docs/test-results/pre-registrations-2026-09-22.md.
    """

    def __init__(
        self,
        strategy: BaseSignalStrategy,
        blocked_hours_utc,
        time_extractor: Callable[[Any], Optional[Any]],
        utc_offset_hours: Optional[int] = None,
        broker_timezone: Optional[str] = None,
    ):
        """
        Args:
            strategy: The wrapped strategy.
            blocked_hours_utc: UTC hours (0-23) during which signals are held.
            time_extractor: Pulls the candle-frame datetime out of `candles`.
            utc_offset_hours: Fixed candle-frame-minus-UTC offset. Used only
                when `broker_timezone` is not given.
            broker_timezone: IANA zone of the broker server's wall clock
                (e.g. "Europe/Athens"). When given, each timestamp is
                converted to UTC with that date's DST rules, instead of one
                offset frozen at startup -- which goes stale across a DST
                change and is wrong for every backtest candle on the other
                side of one.
        """
        if broker_timezone is None and utc_offset_hours is None:
            raise ValueError("SessionFilteredSignalStrategy needs utc_offset_hours or broker_timezone")
        self.strategy = strategy
        self.blocked_hours_utc = set(blocked_hours_utc)
        self.time_extractor = time_extractor
        self.utc_offset_hours = int(utc_offset_hours) if utc_offset_hours is not None else None
        self.broker_tz = ZoneInfo(broker_timezone) if broker_timezone else None

    def __getattr__(self, name):
        """Forward anything not defined here (e.g. `on_new_tick`,
        `get_confirmed_signal`, or a backtest script's `_waiting` probe) to
        the wrapped strategy, so this decorator stays transparent
        regardless of where it sits in the wrapper chain -- e.g. wrapping
        `NTickConfirmedSignalStrategy` shouldn't hide its tick-confirmation
        interface just because this sits outside it."""
        return getattr(self.strategy, name)

    def generate_signal(self, candles, *args, **kwargs):
        result = self.strategy.generate_signal(candles, *args, **kwargs)
        if not isinstance(result, dict) or result.get("error"):
            return result

        current_time = self.time_extractor(candles)
        if self._is_blocked(current_time):
            return {
                **result,
                "final_signal": "hold",
                "raw_signal": "hold",
                "session_filtered": True,
            }
        return result

    def get_confirmed_signal(self, tick_time: Optional[datetime.datetime] = None):
        """Delegate to the wrapped strategy's `get_confirmed_signal` (if it
        has one) and veto the result during blocked hours -- otherwise
        confirmed signals reach `SignalOrchestrator._on_tick` (which calls
        this directly, not through `generate_signal`) with no session-hour
        check at all, since `__getattr__` alone would just proxy straight
        through to the wrapped strategy's confirmation.

        `tick_time` is the confirming tick's own candle-frame datetime
        (`datetime.fromtimestamp(tick.time)`, the same basis
        `get_broker_utc_offset_hours` and `generate_signal`'s
        `time_extractor` already use) -- not wall-clock `datetime.now()`,
        since that's a different, unrelated time base. `None` (a caller
        that doesn't pass it) fails open: a confirmation can't be
        hour-checked without knowing when it happened, and this codebase's
        convention is to fail toward "don't block" on missing data rather
        than raise or silently drop a real signal.
        """
        get_confirmed = getattr(self.strategy, "get_confirmed_signal", None)
        if not callable(get_confirmed):
            return None
        confirmed = get_confirmed()
        if not confirmed or not isinstance(confirmed, dict):
            return confirmed
        if self._is_blocked(tick_time):
            return None
        return confirmed

    def _is_blocked(self, current_time: Optional[datetime.datetime]) -> bool:
        if current_time is None:
            return False
        return self._utc_hour(current_time) in self.blocked_hours_utc

    def _utc_hour(self, frame_time: datetime.datetime) -> int:
        """Map a candle-frame datetime to its true UTC hour.

        Candle-frame times are `datetime.fromtimestamp(mt5_epoch)`, and an MT5
        epoch encodes the broker's *wall clock* as if it were UTC. So
        `.timestamp()` recovers the epoch, reading it back as UTC gives the
        broker wall clock, and localising that to the broker's zone gives
        the real instant.
        """
        if self.broker_tz is None:
            return (frame_time.hour - self.utc_offset_hours) % 24
        broker_wall = datetime.datetime.fromtimestamp(frame_time.timestamp(), tz=datetime.timezone.utc)
        real = broker_wall.replace(tzinfo=self.broker_tz).astimezone(datetime.timezone.utc)
        return real.hour
