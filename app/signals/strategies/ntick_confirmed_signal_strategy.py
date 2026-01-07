from typing import Optional, List, Any
from app.signals.strategies.base_signal_strategy import BaseSignalStrategy


class NTickConfirmedSignalStrategy(BaseSignalStrategy):
    """
    Wraps a base signal strategy to add n-tick confirmation logic.
    Only returns actionable buy/sell signals after n consecutive favorable ticks.
    """

    def __init__(
        self,
        base_strategy: BaseSignalStrategy,
        n_ticks: int = 3,
        min_pip_move: float = 0.0,
        max_spread_points: Optional[float] = None,
        liquidity_check_after_ntick: Optional[bool] = None,
        config: Optional[Any] = None,
        logger: Optional[Any] = None,
    ):
        self.base = base_strategy
        self.n_ticks = n_ticks
        self.min_pip_move = min_pip_move
        self.max_spread_points = max_spread_points
        self.config = config
        self.logger = logger or getattr(base_strategy, "logger", None)
        if config is not None and hasattr(config, "LIQUIDITY_CHECK_AFTER_NTICK"):
            self.liquidity_check_after_ntick = bool(
                getattr(config, "LIQUIDITY_CHECK_AFTER_NTICK")
            )
        elif liquidity_check_after_ntick is not None:
            self.liquidity_check_after_ntick = bool(liquidity_check_after_ntick)
        else:
            self.liquidity_check_after_ntick = True

        self._pending_signal = None
        self._pending_entry_price = None
        self._tick_results = []
        self._waiting = False
        self._last_signal = None
        self._last_m1_signal_id = None
        self._confirmed_signal = None

    def on_new_tick(self, price: float, spread_points: Optional[float] = None):
        if not self._waiting or self._pending_signal not in ("buy", "sell"):
            return

        if self.logger:
            self.logger.info(
                f"[NTick] on_new_tick: price={price}, spread_points={spread_points}, waiting={self._waiting}, pending_signal={self._pending_signal}"
            )

        # Optional spread filter (before tick confirmation)
        if not self.liquidity_check_after_ntick:
            if self.max_spread_points is not None and spread_points is not None:
                if spread_points > self.max_spread_points:
                    self._tick_results = []
                    if self.logger:
                        self.logger.info(
                            f"[NTick] Spread too high: {spread_points}, resetting tick results."
                        )
                    self._last_tick_price = None
                    return

        favorable = False
        movement = 0.0
        if not hasattr(self, "_last_tick_price") or self._last_tick_price is None:
            self._last_tick_price = self._pending_entry_price

        movement = price - self._last_tick_price
        if self._pending_signal == "buy":
            favorable = movement >= self.min_pip_move
        elif self._pending_signal == "sell":
            favorable = movement <= -self.min_pip_move

        if favorable:
            self._tick_results.append(True)
            self._last_tick_price = price
            if self.logger:
                self.logger.info(
                    f"[NTick] Favorable tick: movement={movement}, tick_results={self._tick_results}"
                )
            if len(self._tick_results) == self.n_ticks:
                if self.logger:
                    self.logger.info(
                        f"[NTick] {self.n_ticks} consecutive favorable ticks: confirming {self._pending_signal}."
                    )
                self._confirmed_signal = {
                    **(self._last_signal or {}),
                    "final_signal": self._pending_signal,
                    "reason": f"{self.n_ticks}_consecutive_favorable_ticks",
                    "entry_price": price,
                }
                self._reset()
        else:
            if self.logger:
                self.logger.info(
                    f"[NTick] Unfavorable tick: movement={movement}, resetting tick counter."
                )
            self._tick_results = []
            self._last_tick_price = self._pending_entry_price

    def generate_signal(self, candles: List[dict], *args, **kwargs):
        if self.logger:
            self.logger.info(
                f"[NTick] generate_signal called. Confirmed signal buffer: {self._confirmed_signal}"
            )

        # 1. Return buffered confirmed signal if present
        if self._confirmed_signal is not None:
            sig = self._confirmed_signal
            self._confirmed_signal = None
            return sig

        # 2. Get base signal for this candle
        signal = self.base.generate_signal(candles, *args, **kwargs)
        raw_signal = signal.get("final_signal", "hold")
        last_candle = candles[-1] if candles else None
        last_close = last_candle.get("close") if last_candle else None
        m1_signal_id = last_candle.get("time") if last_candle else None

        # 3. Hard reset on new M1 candle
        if m1_signal_id != self._last_m1_signal_id:
            if self.logger:
                self.logger.info(
                    f"[NTick] Hard reset on new M1 signal: {m1_signal_id} (was {self._last_m1_signal_id})"
                )
            self._reset()
            self._last_m1_signal_id = m1_signal_id

        # 4. Start n-tick confirmation if new buy/sell signal
        if raw_signal in ("buy", "sell") and self._pending_signal != raw_signal:
            self._pending_signal = raw_signal
            self._pending_entry_price = last_close
            self._tick_results = []
            self._waiting = True
            self._last_signal = signal
            if self.logger:
                self.logger.info(
                    f"[NTick] New pending signal: {raw_signal}, entry_price={last_close}, waiting for {self.n_ticks} ticks."
                )
            return {
                **signal,
                "final_signal": "hold",
                "reason": f"waiting_for_{self.n_ticks}_tick_confirmation",
            }

        # 5. If n-tick confirmation is running, but not yet confirmed, keep returning hold
        if self._waiting:
            return {
                **signal,
                "final_signal": "hold",
                "reason": f"waiting_for_{self.n_ticks}_tick_confirmation",
            }

        # 6. If no actionable signal, or after reset
        if raw_signal not in ("buy", "sell"):
            if self.logger:
                self.logger.info(f"[NTick] Raw signal not buy/sell, resetting.")
            self._reset()
        return {
            **signal,
            "final_signal": "hold",
            "reason": "waiting_for_tick_confirmation",
        }

    def get_confirmed_signal(self):
        sig = self._confirmed_signal
        self._confirmed_signal = None
        return sig

    def _reset(self):
        if self.logger:
            self.logger.info(f"[NTick] Resetting internal state.")
        self._pending_signal = None
        self._pending_entry_price = None
        self._tick_results = []
        self._waiting = False
        self._last_signal = None
