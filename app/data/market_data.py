import MetaTrader5 as mt5
from datetime import datetime


def create_market_data():
    """Provider for DI wiring of MarketData."""
    return MarketData()


class MarketData:
    def __init__(self):
        pass

    def scan_symbols(self, max_symbols=10):
        """Returns a filtered list of tradable symbols up to max_symbols."""
        all_symbols = [info.name for info in mt5.symbols_get()]
        available = [sym for sym in all_symbols if mt5.symbol_info(sym) is not None]
        return available[:max_symbols]

    def get_symbol_tick(self, symbol):
        """Retrieve current market tick data for a symbol."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"Failed to get tick information for {symbol}")
            return None
        return tick

    def get_account_info(self):
        """Retrieve account info like balance, equity, margin."""
        account_info = mt5.account_info()
        if account_info is None:
            print("Failed to get account information")
            return None
        return account_info

    def get_symbol_data(self, symbol, timeframe, num_bars, *, closed_only: bool = True):
        """Fetch recent OHLC data for a given symbol and timeframe."""
        self._ensure_symbol_selected(symbol)

        # MT5: start_pos=0 is the current (forming) bar, start_pos=1 is the last CLOSED bar.
        start_pos = 1 if closed_only else 0

        rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, num_bars)
        if rates is None:
            raise RuntimeError(f"Failed to fetch data for symbol: {symbol}")
        return self._rates_to_dict_list(rates)

    def get_historical_candles(
        self, symbol, timeframe, start_pos, count, verbose: bool = False
    ):
        """
        Fetch historical candles.
        """
        self._ensure_symbol_selected(symbol)
        rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)
        if rates is None or len(rates) == 0:
            if verbose:
                print(
                    f"Failed to fetch historical data for {symbol} ({timeframe}) count={count}"
                )
                print("MT5 error:", mt5.last_error())
            return []

        candles = self._rates_to_dict_list(rates)

        # Only print when explicitly asked
        if verbose:
            print(f"[{symbol}] get_historical_candles fetched: {len(candles)} candles")

        return candles

    def calculate_dynamic_sl_tp(self, candles, risk_reward_ratio=1.2):
        """Calculate dynamic stop-loss and take-profit based on opening range."""
        sl, tp = self._calculate_opening_range_sl_tp(candles, risk_reward_ratio)
        return sl, tp

    def _ensure_symbol_selected(self, symbol):
        """Ensure the symbol is in Market Watch before fetching data."""
        if not mt5.symbol_select(symbol, True):
            raise ValueError(f"Failed to select symbol {symbol}")

    def _rates_to_dict_list(self, rates):
        """Convert MetaTrader5 rates array to a list of dictionaries."""
        return [
            {
                "time": datetime.fromtimestamp(rate["time"]),
                "open": float(rate["open"]),
                "high": float(rate["high"]),
                "low": float(rate["low"]),
                "close": float(rate["close"]),
                "tick_volume": int(rate["tick_volume"]),
            }
            for rate in rates
        ]

    def _calculate_opening_range_sl_tp(self, candles, risk_reward_ratio=1.2):
        """Compute opening range-based SL and TP."""
        period = 1  # first candle by default
        opening_candles = candles[:period]
        high = max(c["high"] for c in opening_candles)
        low = min(c["low"] for c in opening_candles)
        sl = high - low
        tp = sl * risk_reward_ratio
        return sl, tp
