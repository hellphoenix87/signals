from app.strategies.exit.exit_shared import (
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


class ProfitExitManager:
    def __init__(
        self,
        config,
        broker,
        get_min_profit_pips,
        dynamic_buffer,
        htf_allows_profit_exit,
        pips_to_price,
        is_favorable_vs_anchor,
        exit_action,
    ):
        self.config = config
        self.broker = broker
        self._get_min_profit_pips = get_min_profit_pips
        self._dynamic_buffer = dynamic_buffer
        self._htf_allows_profit_exit = htf_allows_profit_exit
        self._pips_to_price = pips_to_price
        self._is_favorable_vs_anchor = is_favorable_vs_anchor
        self._exit_action = exit_action

    def _should_apply_htf_gating(self):
        return bool(getattr(self.config, "htf_filter_enabled", False))

    def check_exit_on_tick(self, position, tick, state: PosState):
        if not getattr(self.config, "profit_exits_on_tick", True):
            return None

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

        min_profit_pips = self._get_min_profit_pips(symbol)
        min_profit_price = (
            self._pips_to_price(symbol=symbol, pips=min_profit_pips) or 0.0
        )

        # --- State Initialization ---
        if state is None:
            state = PosState(
                anchor=float(price),
                prev_price=float(price),
                ticks_seen=0,
                ever_favorable=False,
                unfavorable_ticks=0,
                anchor_close=float(price),
                prev_close=float(price),
                closes_seen=0,
            )

        # --- Break-Even Arming ---
        be_distance = float(getattr(self.config, "be_distance_pips", 3.0))
        pip_value = self._pips_to_price(symbol=symbol, pips=1) or 0.0001
        be_price = float(entry) + (
            be_distance * pip_value if side == "buy" else -be_distance * pip_value
        )
        if not getattr(state, "be_armed", False):
            if (side == "buy" and price >= be_price) or (
                side == "sell" and price <= be_price
            ):
                state.be_armed = True
                state.be_armed_tick = state.ticks_seen
                state.be_armed_price = price
            else:
                state.ticks_seen += 1
                state.prev_price = float(price)
                return None

        # --- Stale Trade Before Trailing ---
        trail_start_distance = float(getattr(self.config, "trail_start_pips", 6.0))
        trail_start_price = float(entry) + (
            trail_start_distance * pip_value
            if side == "buy"
            else -trail_start_distance * pip_value
        )
        if not getattr(state, "trailing_active", False):
            stale_tick_limit = int(getattr(self.config, "stale_tick_limit", 20))
            if (side == "buy" and price < trail_start_price) or (
                side == "sell" and price > trail_start_price
            ):
                state.stale_ticks = getattr(state, "stale_ticks", 0) + 1
                if state.stale_ticks >= stale_tick_limit:
                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason="stale_trade_before_trailing",
                    )
                state.prev_price = float(price)
                state.ticks_seen += 1
                return None
            else:
                state.trailing_active = True
                state.best_price = price
                state.trailing_start_tick = state.ticks_seen

        # --- Trailing Logic ---
        if not hasattr(state, "best_price"):
            state.best_price = price
        if (side == "buy" and price > state.best_price) or (
            side == "sell" and price < state.best_price
        ):
            state.best_price = price

        trail_distance = float(getattr(self.config, "trail_distance_pips", 6.0))
        trail_buffer = float(getattr(self.config, "buffer_pips", 2.0))
        trailing_stop = (
            state.best_price - (trail_distance * pip_value)
            if side == "buy"
            else state.best_price + (trail_distance * pip_value)
        )
        buffer_zone = (
            state.best_price - ((trail_distance - trail_buffer) * pip_value)
            if side == "buy"
            else state.best_price + ((trail_distance - trail_buffer) * pip_value)
        )

        # --- Buffer Zone ---
        if (side == "buy" and buffer_zone >= price > trailing_stop) or (
            side == "sell" and buffer_zone <= price < trailing_stop
        ):
            state.buffer_ticks = getattr(state, "buffer_ticks", 0) + 1
            buffer_tick_limit = int(getattr(self.config, "buffer_tick_limit", 10))
            if state.buffer_ticks >= buffer_tick_limit:
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="stale_in_buffer_zone",
                )
            state.prev_price = float(price)
            state.ticks_seen += 1
            return None
        else:
            state.buffer_ticks = 0

        # --- Trailing Stop Breach ---
        if (side == "buy" and price <= trailing_stop) or (
            side == "sell" and price >= trailing_stop
        ):
            return self._exit_action(
                ticket=ticket,
                symbol=symbol,
                position_side=side,
                volume=volume,
                reason="trailing_stop_breach",
            )

        # --- Deep Reversal Guard ---
        extra_reversal_guard_pips = float(
            getattr(self.config, "extra_reversal_guard_pips", 2.0)
        )
        reversal_guard = (
            trailing_stop - (extra_reversal_guard_pips * pip_value)
            if side == "buy"
            else trailing_stop + (extra_reversal_guard_pips * pip_value)
        )
        if (side == "buy" and price <= reversal_guard) or (
            side == "sell" and price >= reversal_guard
        ):
            return self._exit_action(
                ticket=ticket,
                symbol=symbol,
                position_side=side,
                volume=volume,
                reason="deep_reversal_guard_exit",
            )

        state.prev_price = float(price)
        state.ticks_seen += 1
        return None
