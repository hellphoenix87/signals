from app.exit_strategies.exit_shared import (
    PosState,
    pos_entry,
    pos_side,
    pos_symbol,
    pos_ticket,
    pos_volume,
)


def get_tick_value(tick, key):
    if isinstance(tick, dict):
        return tick.get(key)
    return getattr(tick, key, None)


class FixedPipExitManager:
    """Fixed target/stop in pips from the entry price -- the exit of the M5
    RSI-fade system (docs/plans/in-progress/entry-timing.md). Replaces
    breakeven arming, the post-BE cap and the staircase entirely when
    `config.fixed_pips_enabled`.

    Buys are judged on the bid and sells on the ask (the side a position
    closes on), the same convention the signal lab screened. Exits fire on the
    first tick at or past a level, so a fill can land slightly beyond it; the
    broker-side SL/TP set at the same levels (TradeExecutor) is the backstop.
    """

    def __init__(self, config, broker, pips_to_price, exit_action):
        self.config = config
        self.broker = broker
        self._pips_to_price = pips_to_price
        self._exit_action = exit_action

    def check_exit_on_tick(self, position, tick, state: PosState):
        symbol = pos_symbol(position)
        side = pos_side(position)
        ticket = pos_ticket(position)
        entry = pos_entry(position)
        volume = pos_volume(position)
        if not symbol or not side or ticket is None or entry is None or volume is None:
            return None

        price = get_tick_value(tick, "bid") if side == "buy" else get_tick_value(tick, "ask")
        if price is None:
            return None
        target = self._pips_to_price(symbol=symbol, pips=float(getattr(self.config, "fixed_target_pips", 5.0)))
        stop = self._pips_to_price(symbol=symbol, pips=float(getattr(self.config, "fixed_stop_pips", 5.0)))
        if not target or not stop:
            return None

        move = (float(price) - float(entry)) * (1.0 if side == "buy" else -1.0)
        if move >= target - 1e-12:
            reason = "fixed_target"
        elif move <= -stop + 1e-12:
            reason = "fixed_stop"
        else:
            return None
        return self._exit_action(
            ticket=ticket, symbol=symbol, position_side=side, volume=volume, reason=reason
        )
