import threading
import time
import datetime
import MetaTrader5 as mt5
from typing import Optional, Any
from app.services.live_collector import LiveCandleCollector


def create_collector(
    symbol: str, timeframe, count: int, interval: int = 60
) -> LiveCandleCollector:
    """Factory that returns a LiveCandleCollector instance (does not start it)."""
    return LiveCandleCollector(
        symbol=symbol, timeframe=timeframe, count=count, interval=interval
    )


def create_orchestrator(
    collector: LiveCandleCollector,
    signal_generator: Any,
    trading_service: Optional[Any] = None,
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
    """

    def __init__(
        self,
        collector: LiveCandleCollector,
        signal_generator: Any,
        trading_service: Optional[Any] = None,
    ):
        # dependencies injected, no side-effects here
        self.collector = collector
        self.signal_generator = signal_generator
        self.trading_service = trading_service

        self.latest_signal: Optional[dict] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        """Idempotent start: starts collector and orchestrator loop in background thread."""
        if self._running:
            return
        # start collector (collector.start expected to be idempotent)
        self.collector.start()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop orchestrator and collector; join background thread with timeout."""
        if not self._running:
            return
        self._running = False
        try:
            self.collector.stop()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def is_running(self) -> bool:
        return self._running

    def get_latest_signal(self) -> dict:
        """Return last generated signal or generate one on demand (non-blocking quick check)."""
        with self._lock:
            if self.latest_signal:
                return self.latest_signal

        # on-demand generation if no cached signal
        candles = self.collector.get_latest_candles()
        if not candles or len(candles) < self.collector.count:
            return {"error": "Not enough candle data for signal generation"}
        signal = self._call_signal_generator(candles)
        with self._lock:
            self.latest_signal = signal
        return signal

    def _call_signal_generator(self, candles: list) -> dict:
        """Support callable generators and objects with generate_signal(candles)."""
        if callable(self.signal_generator):
            return self.signal_generator(candles)
        gen = getattr(self.signal_generator, "generate_signal", None)
        if callable(gen):
            return gen(candles)
        raise TypeError(
            "signal_generator must be callable or provide generate_signal(candles)"
        )

    def _run(self):
        """Main loop: sync with server time, read collector candles, generate signals, log and delegate execution."""
        while self._running:
            try:
                # synchronize to next candle using broker tick time (preferred)
                tick = None
                try:
                    tick = mt5.symbol_info_tick(self.collector.symbol)
                except Exception:
                    tick = None

                if tick and getattr(tick, "time", None):
                    server_time = datetime.datetime.fromtimestamp(tick.time)
                    seconds_to_next = 60 - server_time.second
                    sleep_time = max(1, seconds_to_next)
                    time.sleep(sleep_time)
                else:
                    time.sleep(self.collector.interval or 60)

                candles = self.collector.get_latest_candles()
                if not candles or len(candles) < self.collector.count:
                    continue

                signal = self._call_signal_generator(candles)
                with self._lock:
                    self.latest_signal = signal

                # determine log time (prefer tick time)
                log_time = None
                try:
                    tick = mt5.symbol_info_tick(self.collector.symbol)
                    if tick and getattr(tick, "time", None):
                        log_time = datetime.datetime.fromtimestamp(tick.time).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                except Exception:
                    log_time = None
                if not log_time:
                    log_time = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

                entry = f"{log_time} | {self.collector.symbol} | {signal}\n"
                try:
                    with open("orchestrator_signals.log", "a") as f:
                        f.write(entry)
                except Exception:
                    # avoid crashing orchestrator on file errors
                    pass

                # delegate actionable signals to trading service
                final = signal.get("final_signal")
                if final in ("buy", "sell") and self.trading_service:
                    proc = getattr(self.trading_service, "process_signal", None)
                    if callable(proc):
                        try:
                            proc(signal, candles)
                        except Exception as e:
                            # keep orchestrator alive on errors from trading service
                            print(f"[Orchestrator] Error processing signal: {e}")

            except Exception as exc:
                # log and backoff briefly to avoid tight-error loops
                print(f"[Orchestrator] unexpected error: {exc}")
                time.sleep(1)
