import threading
import time
import MetaTrader5 as mt5


def create_tick_collector(symbol="EURUSD", interval=1, on_tick=None):
    """
    Provider for DI wiring of TickCollector.
    Returns a TickCollector instance (does not start it).
    """
    return TickCollector(symbol=symbol, interval=interval, on_tick=on_tick)


class TickCollector:
    def __init__(self, symbol="EURUSD", interval=1, on_tick=None):
        self.symbol = symbol
        self.interval = interval  # seconds
        self.on_tick = on_tick  # callback: function(tick)
        self._running = False
        self._thread = None

    def set_callback(self, cb):
        """Set the callback to be called on every tick."""
        self.on_tick = cb

    def start(self, cb=None):
        """Start collecting ticks. Optionally set callback."""
        if cb is not None:
            self.set_callback(cb)
        self.start_collecting()

    def start_collecting(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._collect, daemon=True)
            self._thread.start()

    def stop(self):
        self.stop_collecting()

    def stop_collecting(self):
        self._running = False
        if self._thread:
            self._thread.join()

    def _collect(self):
        while self._running:
            try:
                tick = mt5.symbol_info_tick(self.symbol)
                if tick and self.on_tick:
                    self.on_tick(tick)
            except Exception as e:
                print(f"[TickCollector] Error fetching tick: {e}")
            time.sleep(self.interval)
