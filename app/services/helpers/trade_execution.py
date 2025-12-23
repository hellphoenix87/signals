from datetime import datetime, timedelta
import re
from app.config.settings import Config

# NEW
from app.services.oco_straddle import OCOStraddleManager


def create_trade_executor(risk_manager, broker, market_data, oco_manager=None):
    """Provider for DI wiring of TradeExecutor."""
    return TradeExecutor(risk_manager, broker, market_data, oco_manager=oco_manager)


class TradeExecutor:
    def __init__(self, risk_manager, broker, market_data, oco_manager=None):
        self.risk_manager = risk_manager
        self.broker = broker
        self.market_data = market_data
        self.daily_profit = 0
        self.last_reset = datetime.now()
        self._last_exit_attempt_at = {}

        # NEW: OCO manager (shared instance preferred)
        self.oco_manager = oco_manager

        # NEW: if not injected, create one (still works, but you should share it with orchestrator for on_tick())
        if self.oco_manager is None and bool(getattr(Config, "OCO_ENABLED", False)):
            self.oco_manager = OCOStraddleManager(broker=self.broker)

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

        if not signals:
            print("No actionable signals, no trades executed.")
            return None

        use_oco = (
            bool(getattr(Config, "OCO_ENABLED", False)) and self.oco_manager is not None
        )
        fallback_to_market = bool(getattr(Config, "OCO_FALLBACK_TO_MARKET", True))

        for s in signals:
            symbol = s.get("symbol")
            direction = (s.get("direction") or "").upper()
            lot = s.get("lot")

            if not symbol or direction not in ("BUY", "SELL") or lot is None:
                print(f"Skipping malformed signal: {s}")
                continue

            # If OCO enabled: place straddle instead of market order
            if use_oco:
                grp = self.oco_manager.place_straddle(
                    symbol=symbol,
                    volume=float(lot),
                    offset_pips=float(getattr(Config, "OCO_OFFSET_PIPS", 2.0) or 2.0),
                    expiry_seconds=int(
                        getattr(Config, "OCO_EXPIRY_SECONDS", 120) or 120
                    ),
                    comment_prefix=f"OCO_{direction}",
                )
                if grp is not None:
                    print(
                        f"[OCO] Placed straddle for {symbol} vol={lot} "
                        f"(buy_ticket={grp.buy_stop_ticket}, sell_ticket={grp.sell_stop_ticket}, id={grp.group_id})"
                    )
                    continue

                print(
                    f"[OCO] Failed to place straddle for {symbol} (dir={direction}, lot={lot})"
                )
                if not fallback_to_market:
                    continue  # skip trade entirely

            # Default: market execution (existing behavior)
            print(f"Executing trade: {symbol} {direction} lot={lot}")
            if direction == "BUY":
                self.broker.place_buy(symbol, lot, s.get("sl"), s.get("tp"))
            else:
                self.broker.place_sell(symbol, lot, s.get("sl"), s.get("tp"))

        return None

    def execute_exit(self, action) -> None:
        """
        Execute an ExitAction produced by ExitTrade.
        Expected fields: ticket, symbol, side ('buy'/'sell'), volume, reason
        """
        if action is None:
            return

        ticket = (
            getattr(action, "ticket", None)
            if not isinstance(action, dict)
            else action.get("ticket")
        )
        symbol = (
            getattr(action, "symbol", None)
            if not isinstance(action, dict)
            else action.get("symbol")
        )
        side = (
            getattr(action, "side", None)
            if not isinstance(action, dict)
            else action.get("side")
        )
        volume = (
            getattr(action, "volume", None)
            if not isinstance(action, dict)
            else action.get("volume")
        )
        reason = (
            getattr(action, "reason", "")
            if not isinstance(action, dict)
            else (action.get("reason") or "")
        )

        if ticket is None or not symbol or not side:
            print(f"[TradeExecutor] Skipping malformed exit action: {action}")
            return

        close_side = str(side).upper()  # BUY/SELL
        try:
            res = self.broker.close_position(
                ticket=ticket,
                symbol=symbol,
                side=close_side,
                volume=float(volume) if volume is not None else None,
                comment=f"Exit:{reason}" if reason else "Exit",
            )
            print(
                f"[TradeExecutor] Exit executed: ticket={ticket} {symbol} {close_side} vol={volume} reason={reason} res={res}"
            )
        except Exception as e:
            print(
                f"[TradeExecutor] Exit failed: ticket={ticket} {symbol} side={close_side} err={e}"
            )
            return
