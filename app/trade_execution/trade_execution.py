from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, Iterable, List, Optional

from app.config.settings import Config
from app.exit_strategies.exit_shared import pos_ticket, pos_profit


def create_trade_executor(
    risk_manager: Any, broker: Any, market_data: Any
) -> "TradeExecutor":
    """Provider for DI wiring of TradeExecutor."""
    return TradeExecutor(risk_manager, broker, market_data)


class TradeExecutor:
    """Executes entry and exit orders for confirmed signals, via `broker`.

    Position size and SL/TP distances are driven by `risk_manager` and
    `Config`; a live price/balance are read from `market_data` whenever a
    signal doesn't supply its own.
    """

    def __init__(self, risk_manager: Any, broker: Any, market_data: Any):
        self.risk_manager = risk_manager
        self.broker = broker
        self.market_data = market_data
        self.daily_profit = 0.0
        self.last_reset = datetime.now()
        self._last_exit_attempt_at: Dict[Any, datetime] = {}
        self._daily_cap_logged: Optional[str] = None

    def process_signal(self, signal: Any, candles: Any = None):
        """Normalize `signal` (a list, a `{"signals": [...]}` dict, or a
        single signal dict) into a list and execute each one."""
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
        """Place a market order for every actionable buy/sell signal.

        For each signal: resolve a price (the signal's own `open_price`/
        `price` if given, else the current live tick), resolve SL/TP pip
        distances (the signal's own, else `Config.DEFAULT_SL_PIPS`/
        `DEFAULT_TP_PIPS`), and resolve lot size (the signal's own `lot` if
        given, else `risk_manager.calculate_lot_size` using the live account
        balance and `Config.LOT_RISK_PERCENT`). A signal is skipped (not
        aborting the rest of the batch) if its direction, price, or lot
        can't be resolved.
        """
        print(f"TradeExecutor.execute_signals called at {datetime.now()}")

        cap_reason = self._daily_cap_reason()
        if cap_reason is not None:
            if self._daily_cap_logged != cap_reason:
                print(f"Daily cap reached ({cap_reason}); no new entries until reset.")
                self._daily_cap_logged = cap_reason
            return

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

            if not self._spread_ok(str(symbol)):
                print(f"Skipping signal (spread too wide): {s!r}")
                continue

            price = self._resolve_price(symbol=str(symbol), direction=direction, signal=s)
            if price is None:
                print(f"Skipping signal (no price available): {s!r}")
                continue

            sl_pips = float(s.get("sl_pips") or getattr(Config, "DEFAULT_SL_PIPS", 5.0))
            tp_pips = float(s.get("tp_pips") or getattr(Config, "DEFAULT_TP_PIPS", 50.0))

            lot = self._resolve_lot(
                symbol=str(symbol), price=price, sl_pips=sl_pips, signal=s
            )
            if lot is None or lot <= 0:
                print(f"Skipping signal (could not determine lot): {s!r}")
                continue

            sl, tp = self.broker.calculate_sl_tp_prices(
                direction, price, sl_pips, tp_pips, symbol, units="pips"
            )

            any_actionable = True
            print(f"Executing trade: {symbol} {direction} lot={lot} sl={sl} tp={tp}")

            if direction == "BUY":
                self.broker.place_buy(str(symbol), float(lot), sl, tp)
            else:
                self.broker.place_sell(str(symbol), float(lot), sl, tp)

        if not any_actionable:
            print("No actionable signals (buy/sell), no trades executed.")

    def _extract_direction(self, s: Dict[str, Any]) -> Optional[str]:
        """Return "BUY"/"SELL"/`None`, reading `direction` if present, else
        falling back to `final_signal`/`signal`/`side`/`action`."""
        direction = s.get("direction")
        if isinstance(direction, str) and direction.strip():
            d = direction.strip().upper()
            if d in ("BUY", "SELL"):
                return d

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

    def _spread_ok(self, symbol: str) -> bool:
        """Return whether `symbol`'s current live spread is within
        `Config.MAX_SPREAD_POINTS` (in MT5 points, via `broker.get_point_size`).

        Fails open (returns `True`) if the gate is disabled (`<=0`, the
        default) or if a live tick/point size can't be resolved -- a
        transient data gap shouldn't block trading any more than it
        already does elsewhere in this class.
        """
        max_spread_points = float(getattr(Config, "MAX_SPREAD_POINTS", 0) or 0)
        if max_spread_points <= 0:
            return True

        tick = self.market_data.get_symbol_tick(symbol)
        if tick is None:
            return True

        point_size = self.broker.get_point_size(symbol)
        if not point_size:
            return True

        spread_points = (float(tick.ask) - float(tick.bid)) / float(point_size)
        return spread_points <= max_spread_points

    def _resolve_price(
        self, *, symbol: str, direction: str, signal: Dict[str, Any]
    ) -> Optional[float]:
        """Return the signal's own price if given, else the live tick's ask
        (BUY) or bid (SELL) via `market_data`; `None` if neither is available."""
        price = signal.get("open_price") or signal.get("price")
        if price is not None:
            return float(price)

        tick = self.market_data.get_symbol_tick(symbol)
        if tick is None:
            return None
        return float(tick.ask if direction == "BUY" else tick.bid)

    def _resolve_lot(
        self, *, symbol: str, price: float, sl_pips: float, signal: Dict[str, Any]
    ) -> Optional[float]:
        """Return the signal's own `lot` if given, else a risk-percentage
        lot size from `risk_manager`, sized against
        `Config.RISK_SIZING_BALANCE_OVERRIDE` if set, else the live account
        balance."""
        lot = signal.get("lot")
        if lot is not None:
            return float(lot)

        return self.risk_manager.calculate_lot_size(
            self._resolve_sizing_balance(),
            sl_pips,
            symbol_price=price,
            symbol=symbol,
            risk_percent=getattr(Config, "LOT_RISK_PERCENT", 1.0),
        )

    def _resolve_sizing_balance(self) -> float:
        """Return the balance to size against: `Config.RISK_SIZING_BALANCE_OVERRIDE`
        if set, else the live MT5 account balance. Shared by lot sizing and
        the daily max-loss cap so both risk calculations use the same basis."""
        override_balance = getattr(Config, "RISK_SIZING_BALANCE_OVERRIDE", None)
        if override_balance is not None:
            return float(override_balance)

        account_info = self.market_data.get_account_info()
        return float(account_info.balance) if account_info is not None else 0.0

    def _maybe_reset_daily(self) -> None:
        """Reset `daily_profit` (and the cap-logged flag) at a day boundary."""
        now = datetime.now()
        if now.date() != self.last_reset.date():
            self.daily_profit = 0.0
            self.last_reset = now
            self._daily_cap_logged = None

    def _accumulate_daily_pnl(self, amount: float) -> None:
        self._maybe_reset_daily()
        self.daily_profit += float(amount)

    def _daily_cap_reason(self) -> Optional[str]:
        """Return why new entries should stop today (`"daily_target_reached"`,
        `"daily_max_loss_reached"`), or `None` if neither cap has been hit.
        Gates new entries only -- existing open positions are unaffected."""
        self._maybe_reset_daily()

        target = float(getattr(Config, "DAILY_TARGET_PROFIT", 0) or 0)
        if target > 0 and self.daily_profit >= target:
            return "daily_target_reached"

        max_risk_percent = float(getattr(Config, "DAILY_MAX_RISK_PERCENT", 0) or 0)
        if max_risk_percent > 0:
            max_loss = self._resolve_sizing_balance() * (max_risk_percent / 100.0)
            if max_loss > 0 and self.daily_profit <= -max_loss:
                return "daily_max_loss_reached"

        return None

    def execute_exit(self, action: Any):
        """Close a position by ticket, via a real MT5 closing order.

        Debounced per ticket (`Config`-independent, hardcoded 2s) so a
        protective-exit loop firing every tick doesn't spam `order_send`
        for the same position while its close order is already in flight.
        """
        import MetaTrader5 as mt5

        ticket = getattr(action, "ticket", None)
        volume = float(getattr(action, "volume", 0.0) or 0.0)
        symbol = getattr(action, "symbol", None)

        if ticket is None or not symbol or volume <= 0:
            return None

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
            candidate_ticket = getattr(pos, "ticket", None) or (
                pos.get("ticket") if isinstance(pos, dict) else None
            )
            if candidate_ticket == ticket:
                position = pos
                break

        if not position:
            print(f"Position with ticket {ticket} not found for exit.")
            return None

        realized_profit = pos_profit(position)

        pos_type = getattr(position, "type", None)
        if pos_type is None:
            print(f"Cannot determine position type for ticket {ticket}; aborting exit.")
            return None

        close_is_sell = int(pos_type) == 0
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
                if realized_profit is not None:
                    self._accumulate_daily_pnl(realized_profit)
                return result

            if getattr(result, "comment", "") != "Unsupported filling mode":
                return result

        return None

    def close_all_trades(self) -> Dict[str, List[Any]]:
        """Close every open position on the account (panic-button semantics).

        Retrieves all open positions from the broker (unfiltered by symbol or
        magic number -- this closes everything on the account, including
        positions opened manually or by another EA, matching the endpoint's
        "close all" name) and attempts to close each one via
        broker.close_position(), skipping any position where pos_ticket
        returns None.

        A failure closing one position (whether close_position returns False
        or raises) does not stop the rest from being attempted -- this is
        meant to work as a best-effort panic button, not an all-or-nothing
        transaction. Returns {"closed": [...], "failed": [{"ticket", "error"}, ...]}
        so the caller can tell which positions, if any, are still open.
        """
        closed: List[Any] = []
        failed: List[Dict[str, Any]] = []
        positions = self.broker.get_open_positions()
        for pos in positions or []:
            ticket = pos_ticket(pos)
            if ticket is None:
                continue
            realized_profit = pos_profit(pos)
            try:
                ok = self.broker.close_position(ticket=ticket)
            except Exception as exc:
                failed.append({"ticket": ticket, "error": repr(exc)})
                continue
            if ok is False:
                failed.append({"ticket": ticket, "error": "close_position returned False"})
            else:
                closed.append(ticket)
                if realized_profit is not None:
                    self._accumulate_daily_pnl(realized_profit)
        return {"closed": closed, "failed": failed}

    def _safe_mt5_comment(self, text: str, *, max_len: int = 31) -> str:
        """Sanitize `text` to MT5's comment constraints (<= 31 chars, ASCII-ish)."""
        s = str(text or "")
        s = s.encode("ascii", "ignore").decode("ascii")
        s = re.sub(r"[^A-Za-z0-9 _:\-\.]", "", s)
        s = s.strip()
        if not s:
            s = "EXIT"
        return s[:max_len]
