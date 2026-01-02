from app.strategies.exit.exit_shared import (
    PosState,
    is_break_even,
    pos_entry,
    pos_profit,
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

        # --- Only apply trailing logic if BE is armed by LossExitManager ---
        if not getattr(state, "be_armed", False):
            return None

        # --- Trailing Logic: Pip-based trailing with breach and timeout ---
        pip_size = self._pips_to_price(symbol=symbol, pips=1) or 0.0001
        pip_gain = (
            (price - entry) / pip_size if side == "buy" else (entry - price) / pip_size
        )

        # Prevent trailing exit if actual position profit is not positive
        actual_profit = pos_profit(position)
        if actual_profit is not None and actual_profit <= 0:
            return None

        # Track the best pip gain seen so far
        if not hasattr(state, "best_pip_gain"):
            state.best_pip_gain = pip_gain
            state.breach_ticks = 0

        if pip_gain > state.best_pip_gain:
            state.best_pip_gain = pip_gain
            state.breach_ticks = 0

        breach_threshold_pips = 0.4  # Immediate exit if breached by more than 0.4 pips
        breach_tick_limit = 5  # Wait up to 5 ticks for recovery

        if 0.00 < pip_gain < state.best_pip_gain:
            # Immediate exit if breach is too large
            if state.best_pip_gain - pip_gain >= breach_threshold_pips:
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="trailing_breach_gt_0.4pip",
                )
            # Start or increment breach tick countdown
            state.breach_ticks = getattr(state, "breach_ticks", 0) + 1
            # If pip gain recovers, reset countdown
            if pip_gain >= state.best_pip_gain:
                state.breach_ticks = 0
            # Exit if breach lasts too long
            """
            elif state.breach_ticks >= breach_tick_limit:
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="trailing_breach_timeout",
                )
            """
        else:
            state.breach_ticks = 0  # No breach, reset

        state.prev_price = float(price)
        state.ticks_seen += 1
        return None
