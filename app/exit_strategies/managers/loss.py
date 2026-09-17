from app.exit_strategies.exit_shared import (
    PosState,
    is_break_even,
    pos_profit,
    pos_symbol,
    pos_side,
    pos_ticket,
    pos_entry,
    pos_volume,
)


def get_tick_value(tick, key):
    if isinstance(tick, dict):
        return tick.get(key)
    return getattr(tick, key, None)


class LossExitManager:
    def __init__(
        self,
        config,
        broker,
        risk_manager,
        get_min_profit_pips,
        pips_to_price,
        exit_action,
    ):
        self.config = config
        self.broker = broker
        self.risk_manager = risk_manager
        self._get_min_profit_pips = get_min_profit_pips
        self._pips_to_price = pips_to_price
        self._exit_action = exit_action

    def check_exit_on_tick(self, position, tick, state: PosState):
        """Protective exit check for one position/tick.

        Before break-even is armed: exits immediately once the configured
        pre-breakeven soft SL is breached (`reason="profit_drop"`) --
        `config.max_loss_money`/`max_loss_price`/`max_loss_pips` are three
        unit choices for the same cap (see `_pre_be_soft_sl_hit`); arms break-even the moment
        `is_break_even(position)` is true. If the arming window
        (`config.be_arming_ticks`) expires without ever reaching break-even,
        force-closes (`reason="failed_to_reach_be"`) -- unless *this exact
        tick* is the one reaching break-even, in which case it arms instead
        of closing a position that just turned profitable. A configured
        `be_arming_ticks <= 0` disables this forced-timeout safety net
        entirely (the arming window never "expires"), rather than force-
        closing every position on tick 1 the way an unguarded `0` would.

        After arming: exits if profit drops back to `-$5` or lower
        (`reason="profit_drop_after_be"`); if profit went negative after
        arming and then recovers into `0 < profit < $0.05`, exits with
        `reason="be_recovered_after_unprofit"` to lock in a marginal win
        rather than let it round-trip again.
        """
        symbol = pos_symbol(position)
        side = pos_side(position)
        ticket = pos_ticket(position)
        entry = pos_entry(position)
        volume = pos_volume(position)

        if not symbol or not side or ticket is None or entry is None or volume is None:
            return None

        profit = pos_profit(position)
        if profit is None:
            profit = 0.0

        price = get_tick_value(tick, "bid") if side == "buy" else get_tick_value(tick, "ask")

        be_arming_ticks = int(getattr(self.config, "be_arming_ticks", 20))

        if not hasattr(state, "be_armed"):
            state.be_armed = False
            state.be_arming_ticks = 0
            state.was_unprofitable_after_be = False
            state.was_profitable_after_unprofit = False

        if not state.be_armed and (
            be_arming_ticks <= 0 or state.be_arming_ticks < be_arming_ticks
        ):
            state.be_arming_ticks += 1

            if self._pre_be_soft_sl_hit(
                profit=profit, price=price, entry=entry, side=side, symbol=symbol
            ):
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="profit_drop",
                )
            if is_break_even(position):
                state.be_armed = True
                state.was_profitable_after_unprofit = False
                state.was_unprofitable_after_be = False
            return None

        if not state.be_armed and be_arming_ticks > 0 and state.be_arming_ticks >= be_arming_ticks:
            if is_break_even(position):
                state.be_armed = True
                state.was_profitable_after_unprofit = False
                state.was_unprofitable_after_be = False
                return None
            return self._exit_action(
                ticket=ticket,
                symbol=symbol,
                position_side=side,
                volume=volume,
                reason="failed_to_reach_be",
            )

        if state.be_armed:
            drop_profit_after_be = -5
            if profit < 0.0:
                if not state.was_unprofitable_after_be:
                    state.was_unprofitable_after_be = True
                    state.unprofit_profit = profit
                if profit <= drop_profit_after_be:
                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason="profit_drop_after_be",
                    )

            if state.was_unprofitable_after_be:
                if 0.0 < profit < 0.05:
                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason="be_recovered_after_unprofit",
                    )
            return None

    def _pre_be_soft_sl_hit(
        self, *, profit: float, price, entry: float, side: str, symbol: str
    ) -> bool:
        """Return whether the pre-breakeven soft SL has been breached, via
        whichever of `config.max_loss_money`/`max_loss_price`/`max_loss_pips`
        is configured (`> 0`) -- three unit choices for the same cap. If more
        than one is set, exits on whichever fires first (the tightest
        configured cap wins). All three at `0` (the "disabled" convention
        used throughout this codebase) means no pre-breakeven soft SL at
        all -- protection then relies solely on the broker-side SL and the
        breakeven-timeout force-close.
        """
        max_loss_money = float(getattr(self.config, "max_loss_money", 0.0) or 0.0)
        if max_loss_money > 0 and profit <= -max_loss_money:
            return True

        if price is None or entry is None:
            return False

        adverse_distance = (entry - price) if side == "buy" else (price - entry)

        max_loss_price = float(getattr(self.config, "max_loss_price", 0.0) or 0.0)
        if max_loss_price > 0 and adverse_distance >= max_loss_price:
            return True

        max_loss_pips = float(getattr(self.config, "max_loss_pips", 0.0) or 0.0)
        if max_loss_pips > 0:
            max_loss_price_from_pips = (
                self._pips_to_price(symbol=symbol, pips=max_loss_pips) or 0.0
            )
            if max_loss_price_from_pips > 0 and adverse_distance >= max_loss_price_from_pips:
                return True

        return False
