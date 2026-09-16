from typing import Any, Callable, Optional
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
    traded hours. No such effect was found for the single-timeframe
    strategy, so this wrapper is a no-op for it in practice, not
    something that needs separate tuning per strategy.
    """

    def __init__(
        self,
        strategy: BaseSignalStrategy,
        blocked_hours_utc,
        time_extractor: Callable[[Any], Optional[Any]],
        utc_offset_hours: int,
    ):
        self.strategy = strategy
        self.blocked_hours_utc = set(blocked_hours_utc)
        self.time_extractor = time_extractor
        self.utc_offset_hours = int(utc_offset_hours)

    def __getattr__(self, name):
        """Forward anything not defined here (e.g. `on_new_tick`,
        `get_confirmed_signal`) to the wrapped strategy, so this decorator
        stays transparent regardless of where it sits in the wrapper chain
        -- e.g. wrapping `NTickConfirmedSignalStrategy` shouldn't hide its
        tick-confirmation interface from the orchestrator's `hasattr` checks."""
        return getattr(self.strategy, name)

    def generate_signal(self, candles, *args, **kwargs):
        result = self.strategy.generate_signal(candles, *args, **kwargs)
        if not isinstance(result, dict) or result.get("error"):
            return result

        current_time = self.time_extractor(candles)
        if current_time is None:
            return result

        utc_hour = (current_time.hour - self.utc_offset_hours) % 24
        if utc_hour in self.blocked_hours_utc:
            return {
                **result,
                "final_signal": "hold",
                "raw_signal": "hold",
                "session_filtered": True,
            }
        return result
