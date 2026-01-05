from app.exit_strategies.exit_shared import (
    PosState,
    is_break_even,
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

        symbol = pos_symbol(position)
        side = pos_side(position)
        ticket = pos_ticket(position)
        entry = pos_entry(position)
        volume = pos_volume(position)

        if not symbol or not side or ticket is None or entry is None or volume is None:
            return None

        profit = getattr(position, "profit", None)
        if profit is None:
            profit = 0.0

        be_arming_ticks = int(getattr(self.config, "EXIT_BE_ARMING_TICKS", 20))
        drop_profit = -5  # Exit if profit drops to -5 or lower

        # State init
        if not hasattr(state, "be_armed"):
            state.be_armed = False
            state.be_arming_ticks = 0
            state.was_unprofitable_after_be = False
            state.was_profitable_after_unprofit = False

        # 1. During first N ticks, exit if profit drops to -0.20 or lower
        if not state.be_armed and state.be_arming_ticks < be_arming_ticks:
            state.be_arming_ticks += 1

            if profit <= drop_profit:
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="profit_drop",
                )
            # If BE reached (profit >= 0), arm BE
            if is_break_even(position):
                state.be_armed = True
                state.was_profitable_after_unprofit = False
                state.was_unprofitable_after_be = False
                return None
            """
            # If N ticks passed and BE not reached, exit
            if state.be_arming_ticks >= be_arming_ticks:
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="failed_to_reach_be",
                )
            return None
            """

        # 2. After BE is reached
        if state.be_armed:
            drop_profit_after_be = -5  # Exit if profit drops to -0.30 or lower after BE
            # Track if profit moves to unprofit after BE
            if profit < 0.0:
                if not state.was_unprofitable_after_be:
                    state.was_unprofitable_after_be = True
                    state.unprofit_profit = profit
                # If profit drops to -0.05 or lower after BE, exit
                if profit <= drop_profit_after_be:
                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason="profit_drop_after_be",
                    )
            # If profit returns to BE or above after being unprofitable, exit immediately

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
