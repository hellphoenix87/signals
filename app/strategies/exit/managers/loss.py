from app.strategies.exit.exit_shared import (
    PosState,
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

        price = (
            get_tick_value(tick, "bid")
            if side == "buy"
            else get_tick_value(tick, "ask")
        )
        pip_value = self._pips_to_price(symbol=symbol, pips=1) or 0.0001
        be_distance = float(getattr(self.config, "be_distance_pips", 3.0))
        be_arming_ticks = int(getattr(self.config, "be_arming_ticks", 10))
        drop_pips = 5.0  # hardcoded, or use config if you want

        be_price = float(entry) + (
            be_distance * pip_value if side == "buy" else -be_distance * pip_value
        )

        # State init
        if not hasattr(state, "be_armed"):

            state.be_armed = False
            state.be_arming_ticks = 0
            state.was_unprofitable_after_be = False
            state.was_profitable_after_unprofit = False

        # 1. During first 10 ticks, exit if price drops 5 pips from entry
        if not state.be_armed and state.be_arming_ticks < be_arming_ticks:
            state.be_arming_ticks += 1
            loss_pips = abs((price - entry) / pip_value)

            if (side == "buy" and price < entry - drop_pips * pip_value) or (
                side == "sell" and price > entry + drop_pips * pip_value
            ):

                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="drop_5_pips_before_be",
                )
            # If BE reached, arm BE
            if (side == "buy" and price >= be_price) or (
                side == "sell" and price <= be_price
            ):

                state.be_armed = True
                state.was_profitable_after_unprofit = False
                state.was_unprofitable_after_be = False
                return None
            # If 10 ticks passed and BE not reached, exit
            if state.be_arming_ticks >= be_arming_ticks:

                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="failed_to_reach_be",
                )
            return None

        # 2. After BE is reached
        if state.be_armed:

            # Track if price moves to unprofit after BE
            if (side == "buy" and price < entry) or (side == "sell" and price > entry):
                if not state.was_unprofitable_after_be:

                    state.was_unprofitable_after_be = True
                    state.unprofit_price = price
                # If price drops 5 pips from entry after BE, exit
                if (side == "buy" and price < entry - drop_pips * pip_value) or (
                    side == "sell" and price > entry + drop_pips * pip_value
                ):

                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason="drop_5_pips_after_be",
                    )
            # If price moves back to BE or profit after being unprofitable, exit immediately
            if state.was_unprofitable_after_be:
                if (side == "buy" and price >= be_price) or (
                    side == "sell" and price <= be_price
                ):

                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason="be_recovered_after_unprofit",
                    )
            return None
