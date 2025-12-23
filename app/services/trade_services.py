import threading
import time
import datetime
import MetaTrader5 as mt5
from typing import Optional, Any


def create_orchestrator(
    collector,
    signal_generator: Any,
    trading_service: Optional[Any] = None,
    tick_collector: Optional[Any] = None,
    exit_trade: Optional[Any] = None,
) -> "SignalOrchestrator":
    """
    Provider that creates and returns a SignalOrchestrator.
    - collector: LiveCandleCollector instance (injected, not started)
    - signal_generator: callable(candles)->dict or object with generate_signal(candles)
    - trading_service: optional execution service with process_signal(signal, candles)
    """
    return SignalOrchestrator(
        collector=collector,
        signal_generator=signal_generator,
        trading_service=trading_service,
        tick_collector=tick_collector,
        exit_trade=exit_trade,
    )


class SignalOrchestrator:
    """
    Service-layer orchestrator (DI-ready).

    Responsibilities:
    - coordinate synchronization with broker/server time
    - read latest candles from injected LiveCandleCollector
    - call injected signal_generator to produce signals
    - log signals with exact time
    - delegate actionable signals to injected trading_service.process_signal
    - process ticks via injected tick_collector and feed exit_trade (if provided)
    """

    def __init__(
        self,
        collector,
        signal_generator: Any,
        trading_service: Optional[Any] = None,
        tick_collector: Optional[Any] = None,
        exit_trade: Optional[Any] = None,
    ):
        # dependencies injected, no side-effects here
        self.collector = collector
        self.signal_generator = signal_generator
        self.trading_service = trading_service
        self.exit_trade = exit_trade
        self.tick_collector = tick_collector

        self.latest_signal: Optional[dict] = None
        self._latest_tick: Any = None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        """Idempotent start: starts candle collector, tick collector, and orchestrator loop in background thread."""
        if self._running:
            return

        # start candle collector (expected to be idempotent)
        self.collector.start()

        # start tick collection so exit_trade can run without requiring /tick endpoint
        self._ensure_ticks_started()

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop orchestrator and collectors; join background thread with timeout."""
        if not self._running:
            return

        self._running = False

        # stop candle collector
        try:
            self.collector.stop()
        except Exception:
            pass

        # stop tick collector (best-effort)
        try:
            if self.tick_collector:
                stopper = getattr(self.tick_collector, "stop_collecting", None)
                if callable(stopper):
                    stopper()
                else:
                    stopper2 = getattr(self.tick_collector, "stop", None)
                    if callable(stopper2):
                        stopper2()
        except Exception:
            pass

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def is_running(self) -> bool:
        return self._running

    def get_latest_signal(self) -> dict:
        """Return last generated signal snapshot (signals list + candle_time) or generate on demand."""
        with self._lock:
            if self.latest_signal:
                return self.latest_signal

        candles = self.collector.get_latest_candles()
        if not candles or len(candles) < self.collector.count:
            return {"error": "Not enough candle data for signal generation"}

        candle_time = None
        try:
            last = candles[-1]
            candle_time = (
                last.get("time") or last.get("timestamp") or last.get("datetime")
                if isinstance(last, dict)
                else getattr(last, "time", None)
            )
        except Exception:
            pass

        # Prefer multi-symbol generator API if available
        sg = self.signal_generator
        gen_signals = getattr(sg, "generate_signals", None)
        if callable(gen_signals):
            account_balance = self._get_account_balance()
            signals = gen_signals(account_balance)
            snapshot = {"signals": signals, "candle_time": candle_time}
            with self._lock:
                self.latest_signal = snapshot
            return snapshot

        # Fallback to single-symbol generator API
        signal = self._call_signal_generator(candles)
        snapshot = {"signal": signal, "candle_time": candle_time}
        with self._lock:
            self.latest_signal = snapshot
        return snapshot

    def _ensure_ticks_started(self) -> None:
        """Attach tick callback and start tick collector if available."""
        if not self.tick_collector:
            return

        # attach callback
        if getattr(self.tick_collector, "on_tick", None) != self._store_tick:
            self.tick_collector.on_tick = self._store_tick

        # start collecting if not running
        if not getattr(self.tick_collector, "_running", False):
            starter = getattr(self.tick_collector, "start_collecting", None)
            if callable(starter):
                starter()

    def _store_tick(self, tick: Any) -> None:
        """TickCollector callback."""
        self._latest_tick = tick

        if not self.exit_trade:
            return

        actions = self.exit_trade.on_tick(tick)  # returns list[ExitAction]
        if not actions:
            return

        self._execute_exit_actions(actions)

    def _execute_exit_actions(self, actions: list[Any]) -> None:
        """
        Hand exit actions to execution layer.
        Expected:
        - trading_service.execute_exit(action), OR
        - trading_service.execute_trade(symbol, side, volume)
        """
        if not self.trading_service:
            return

        exec_exit = getattr(self.trading_service, "execute_exit", None)
        if callable(exec_exit):
            for action in actions:
                exec_exit(action)
            return

        exec_trade = getattr(self.trading_service, "execute_trade", None)
        if callable(exec_trade):
            for action in actions:
                exec_trade(symbol=action.symbol, side=action.side, volume=action.volume)
            return

        print(
            "[SignalOrchestrator] No supported exit execution method on trading_service"
        )

    def get_tick(self):
        """
        Debug/inspection:
        Ensure tick collection is running, then return latest tick.
        """
        self._ensure_ticks_started()
        return self._latest_tick

    def _run(self):
        """
        Main loop:
        - Tick exit logic runs per-tick via TickCollector callback (_store_tick).
        - Signal generation runs only when a NEW candle appears.
        """

        def _extract_candle_time(candle):
            if isinstance(candle, dict):
                return (
                    candle.get("time")
                    or candle.get("timestamp")
                    or candle.get("datetime")
                )
            return getattr(candle, "time", None)

        # Seed last_candle_time so we do NOT generate immediately on startup
        last_candle_time = None
        try:
            candles0 = self.collector.get_latest_candles()
            if candles0:
                last0 = candles0[-1]
                last_candle_time = _extract_candle_time(last0)
        except Exception:
            last_candle_time = None

        while self._running:
            try:
                candles = self.collector.get_latest_candles()
                if not candles or len(candles) < self.collector.count:
                    time.sleep(1)
                    continue

                last = candles[-1]
                candle_time = _extract_candle_time(last)

                if candle_time is None:
                    time.sleep(self.collector.interval or 60)
                    continue

                if candle_time == last_candle_time:
                    time.sleep(1)
                    continue
                last_candle_time = candle_time

                account_balance = self._get_account_balance()
                signals = self.signal_generator.generate_signals(account_balance)
                if not signals:
                    continue

                # store last signals snapshot
                with self._lock:
                    self.latest_signal = {
                        "signals": signals,
                        "candle_time": candle_time,
                    }

                # execute once per candle (process_signal already accepts list)
                if self.trading_service:
                    proc = getattr(self.trading_service, "process_signal", None)
                    if callable(proc):
                        try:
                            proc(signals, candles)
                        except Exception as e:
                            print(f"[Orchestrator] Error processing signals: {e}")

            except Exception as exc:
                print(f"[Orchestrator] unexpected error: {exc}")
                time.sleep(1)

    def _get_account_balance(self):
        # Implement this to fetch account balance from broker or risk manager
        # Example:
        getter = getattr(self.trading_service, "get_account_balance", None)
        if callable(getter):
            return getter()
        return 10000  # fallback default

    def _call_signal_generator(self, candles: list[dict]) -> dict:
        """
        Backward-compatible adapter for different signal_generator styles.

        Supports:
        - generator.generate_signal(candles) -> dict
        - generator(candles) -> dict
        """
        sg = self.signal_generator

        gen_signal = getattr(sg, "generate_signal", None)
        if callable(gen_signal):
            return gen_signal(candles)

        if callable(sg):
            return sg(candles)

        raise AttributeError(
            "signal_generator must implement generate_signal(candles) or be callable(candles)."
        )
