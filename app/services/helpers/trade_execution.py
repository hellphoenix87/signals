from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config.settings import Config


def create_trade_executor(
    risk_manager: Any, broker: Any, market_data: Any
) -> "TradeExecutor":
    """Provider for DI wiring of TradeExecutor."""
    return TradeExecutor(risk_manager, broker, market_data)


class TradeExecutor:
    def __init__(self, risk_manager: Any, broker: Any, market_data: Any):
        self.risk_manager = risk_manager
        self.broker = broker
        self.market_data = market_data
        self.daily_profit = 0
        self.last_reset = datetime.now()
        self._last_exit_attempt_at: Dict[Any, datetime] = {}

    # -------------------------
    # Entry execution
    # -------------------------

    def process_signal(self, signal: Any, candles: Any = None):
        """
        Accepts:
          - list[dict]
          - dict with {"signals": list[dict]}
          - dict (single signal)
        """
        if isinstance(signal, list):
            return self.execute_signals(signal, candles=candles)
        if isinstance(signal, dict) and isinstance(signal.get("signals"), list):
            return self.execute_signals(signal["signals"], candles=candles)
        if isinstance(signal, dict):
            return self.execute_signals([signal], candles=candles)
        return None

    def execute_signals(
        self, signals: Iterable[Dict[str, Any]], candles: Any = None
    ) -> None:
        _ = candles  # reserved for future sizing/sl/tp based on context
        print(f"TradeExecutor.execute_signals called at {datetime.now()}")

        any_actionable = False

        for s in signals or []:
            if not isinstance(s, dict):
                print(f"Skipping malformed signal (not a dict): {s!r}")
                continue

            symbol = s.get("symbol")
            if not symbol:
                print(f"Skipping malformed signal (missing symbol): {s!r}")
                continue

            direction = self._extract_direction(s)
            if direction is None:
                continue

            lot = self._extract_lot(symbol=str(symbol), signal=s)
            if lot is None:
                print(f"Skipping signal (could not determine lot): {s!r}")
                continue

            # Always calculate SL/TP here using config defaults if not present
            price = s.get("open_price") or s.get("price")
            sl_pips = s.get("sl_pips") or getattr(Config, "DEFAULT_SL_PIPS", 5.0)
            tp_pips = s.get("tp_pips") or getattr(Config, "DEFAULT_TP_PIPS", 50.0)

            calc = getattr(self.broker, "calculate_sl_tp_prices", None)
            if callable(calc) and price is not None:
                sl, tp = calc(
                    direction,
                    price,
                    sl_pips,
                    tp_pips,
                    symbol,
                    units="pips",
                )
            else:
                sl = None
                tp = None

            any_actionable = True
            print(f"Executing trade: {symbol} {direction} lot={lot} sl={sl} tp={tp}")

            if direction == "BUY":
                self.broker.place_buy(str(symbol), float(lot), sl, tp)
            else:
                self.broker.place_sell(str(symbol), float(lot), sl, tp)

        if not any_actionable:
            print("No actionable signals (buy/sell), no trades executed.")

    def _extract_direction(self, s: Dict[str, Any]) -> Optional[str]:
        """
        Returns "BUY" / "SELL" / None.
        """
        # Old format
        direction = s.get("direction")
        if isinstance(direction, str) and direction.strip():
            d = direction.strip().upper()
            if d in ("BUY", "SELL"):
                return d

        # New generator format(s)
        side = (
            s.get("final_signal")
            or s.get("signal")
            or s.get("side")
            or s.get("action")
            or "hold"
        )
        side = str(side).strip().lower()

        if side == "hold":
            return None
        if side == "buy":
            return "BUY"
        if side == "sell":
            return "SELL"

        print(f"Skipping malformed signal (unknown direction/side={side!r}): {s!r}")
        return None

    def _extract_lot(self, *, symbol: str, signal: Dict[str, Any]) -> Optional[float]:
        """
        Determine lot size:
          1) explicit lot in signal
          2) ask risk_manager via common method names
          3) fallback to config LOT_SIZE / DEFAULT_LOT / 0.01
        """
        lot = signal.get("lot")
        if lot is not None:
            try:
                return float(lot)
            except Exception:
                return None

        # Common risk manager method names (best-effort)
        for name in (
            "calculate_lot",
            "calculate_lot_size",
            "get_lot",
            "get_lot_size",
            "position_size",
            "compute_lot",
        ):
            fn = getattr(self.risk_manager, name, None)
            if callable(fn):
                try:
                    # try (symbol, signal)
                    try:
                        v = fn(symbol, signal)
                    except TypeError:
                        # try (symbol)
                        v = fn(symbol)
                    if v is not None:
                        return float(v)
                except Exception:
                    pass

        # Config fallback
        for k in ("LOT_SIZE", "DEFAULT_LOT", "MIN_LOT"):
            v = getattr(Config, k, None)
            if v is not None:
                try:
                    return float(v)
                except Exception:
                    pass

        return 0.01

    # -------------------------
    # Exit execution (used by hybrid ExitTrade)
    # -------------------------

    def execute_exit(self, action: Any):
        """
        Executes an exit action by closing the position via the broker.
        """
        import MetaTrader5 as mt5

        ticket = getattr(action, "ticket", None)
        volume = float(getattr(action, "volume", 0.0) or 0.0)
        symbol = getattr(action, "symbol", None)

        if ticket is None or not symbol or volume <= 0:
            return None

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
                "deviation": int(getattr(Config, "MAX_DEVIATION", 5) or 5),
                "magic": int(getattr(Config, "MAGIC_NUMBER", 123456) or 123456),
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
