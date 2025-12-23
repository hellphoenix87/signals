from datetime import datetime, timedelta
import re
from app.config.settings import Config


def create_trade_executor(risk_manager, broker, market_data):
    """Provider for DI wiring of TradeExecutor."""
    return TradeExecutor(risk_manager, broker, market_data)


class TradeExecutor:
    def __init__(self, risk_manager, broker, market_data):
        self.risk_manager = risk_manager
        self.broker = broker
        self.market_data = market_data
        self.daily_profit = 0
        self.last_reset = datetime.now()
        self._last_exit_attempt_at = {}

    def process_signal(self, signal, candles=None):
        if isinstance(signal, list):
            return self.execute_signals(signal)
        if isinstance(signal, dict) and isinstance(signal.get("signals"), list):
            return self.execute_signals(signal["signals"])
        if isinstance(signal, dict):
            return self.execute_signals([signal])
        return None

    def execute_signals(self, signals):
        print(f"TradeExecutor.execute_signals called at {datetime.now()}")
        if signals:
            for s in signals:
                symbol = s.get("symbol")
                direction = s.get("direction")
                lot = s.get("lot")
                if not symbol or not direction or lot is None:
                    print(f"Skipping malformed signal: {s}")
                    continue
                print(f"Executing trade: {symbol} {direction} lot={lot}")
                if direction.upper() == "BUY":
                    self.broker.place_buy(symbol, lot, s.get("sl"), s.get("tp"))
                else:
                    self.broker.place_sell(symbol, lot, s.get("sl"), s.get("tp"))
        else:
            print("No actionable signals, no trades executed.")

    def execute_exit(self, action):
        """
        Executes an exit action by closing the position via the broker.
        """
        import MetaTrader5 as mt5

        ticket = action.ticket
        volume = float(action.volume)
        symbol = action.symbol

        # Debounce: do not spam order_send every tick for the same ticket
        now = datetime.now()
        last_try = self._last_exit_attempt_at.get(ticket)
        if last_try and (now - last_try).total_seconds() < 2.0:
            return None
        self._last_exit_attempt_at[ticket] = now

        positions = self.broker.get_open_positions(symbol)
        if not positions:
            print(
                f"Position with ticket {ticket} not found for exit (no open positions)."
            )
            return None

        position = None
        for pos in positions:
            pos_ticket = getattr(pos, "ticket", None) or (
                pos.get("ticket") if isinstance(pos, dict) else None
            )
            if pos_ticket == ticket:
                position = pos
                break

        if not position:
            print(f"Position with ticket {ticket} not found for exit.")
            return None

        # MT5: position.type -> 0=BUY, 1=SELL
        pos_type = getattr(position, "type", None)
        if pos_type is None:
            print(f"Cannot determine position type for ticket {ticket}; aborting exit.")
            return None

        close_is_sell = int(pos_type) == 0  # close BUY with SELL
        order_type = mt5.ORDER_TYPE_SELL if close_is_sell else mt5.ORDER_TYPE_BUY

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(
                f"Failed to get tick for {symbol} while exiting ticket {ticket}. MT5 error: {mt5.last_error()}"
            )
            return None

        price = tick.bid if close_is_sell else tick.ask
        try:
            price = self.broker._normalize_price(symbol, float(price))
        except Exception:
            price = float(price)

        filling_modes = (
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_RETURN,
            mt5.ORDER_FILLING_IOC,
        )

        for filling in filling_modes:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "position": ticket,
                "price": price,
                "deviation": 5,
                "magic": 123456,
                # "comment": "...",  # OMIT: some brokers/terminals reject comments
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }

            result = mt5.order_send(request)
            if result is None:
                print(
                    f"Exit order_send returned None for {symbol} ticket {ticket} filling={filling}. MT5 error: {mt5.last_error()}"
                )
                continue

            print(
                f"Exit order result for {symbol} ticket {ticket} filling={filling}: {result}"
            )

            if result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                return result

            if getattr(result, "comment", "") != "Unsupported filling mode":
                return result

        return None

    def _safe_mt5_comment(self, text: str, *, max_len: int = 31) -> str:
        """
        MT5/brokers often require comment <= 31 chars and ASCII-ish.
        """
        s = str(text or "")
        s = s.encode("ascii", "ignore").decode("ascii")  # drop non-ascii
        s = re.sub(r"[^A-Za-z0-9 _:\-\.]", "", s)  # keep a conservative set
        s = s.strip()
        if not s:
            s = "EXIT"
        return s[:max_len]
