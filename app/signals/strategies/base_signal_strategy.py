from abc import ABC, abstractmethod


class BaseSignalStrategy(ABC):
    @abstractmethod
    def generate_signal(self, *args, **kwargs):
        pass

    # Shared helpers can go here
    def _resolve_symbol(self, candles, config):
        symbol = (
            candles[-1].get("symbol")
            if candles and isinstance(candles[-1], dict)
            else None
        )
        if not symbol:
            symbol = config.SYMBOLS[0]
        return symbol
