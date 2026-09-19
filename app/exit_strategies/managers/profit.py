from app.exit_strategies.exit_shared import (
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
    """Read `key` from a tick, whether it's a dict or an object."""
    if isinstance(tick, dict):
        return tick.get(key)
    return getattr(tick, key, None)


class ProfitExitManager:
    """Profit-taking exit checks, on both the tick path (`check_exit_on_tick`,
    never HTF-gated -- this is the fast/protective path) and the candle-close
    path (`check_exit_on_candle_close`, HTF-gated -- suppressed while the
    higher-timeframe bias still supports the position's direction)."""

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

    def _trail_gap(self, peak: float) -> float:
        """Post-breakeven trailing-profit giveback allowance for a given
        peak profit: `max(config.trail_gap_floor_money, config.trail_gap_pct
        * peak)` -- a percentage-of-peak gap with a dollar floor so a small
        peak isn't stopped by trivial noise. See `Config.EXIT_TRAIL_GAP_PCT`
        for the data behind this shape."""
        floor = float(getattr(self.config, "trail_gap_floor_money", 2.0) or 2.0)
        pct = float(getattr(self.config, "trail_gap_pct", 0.6) or 0.6)
        return max(floor, pct * peak)

    def check_exit_on_tick(self, position, tick, state: PosState):
        """Arm break-even, track best-profit-seen, and exit if profit pulls
        back more than the trailing gap (`_trail_gap`, a percentage of peak
        with a dollar floor) from that peak
        (`reason="trailing_breach_pct_of_peak"`). The trail only actively
        enforces once the gap-adjusted trigger is positive -- a peak still
        smaller than its own gap leaves the trail inactive rather than
        force-exiting on ordinary noise near breakeven."""
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

        if not getattr(state, "be_armed", False):
            if is_break_even(position):
                state.be_armed = True
                state.be_armed_tick = state.ticks_seen
                state.be_armed_price = price
            else:
                state.ticks_seen += 1
                state.prev_price = float(price)
                return None

        profit = pos_profit(position)
        if profit is None:
            profit = 0.0

        if not hasattr(state, "best_profit"):
            state.best_profit = profit
            state.breach_ticks = 0

        if profit > state.best_profit:
            state.best_profit = profit
            state.breach_ticks = 0

        trigger = state.best_profit - self._trail_gap(state.best_profit)

        if 0.00 < profit < state.best_profit:
            if trigger > 0.0 and profit <= trigger:
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="trailing_breach_pct_of_peak",
                )
            state.breach_ticks = getattr(state, "breach_ticks", 0) + 1
            if profit >= state.best_profit:
                state.breach_ticks = 0
        else:
            state.breach_ticks = 0

        state.prev_price = float(price)
        state.ticks_seen += 1
        return None

    def check_exit_on_candle_close(self, position, close_price, state: PosState):
        """Same break-even-arming/best-profit-tracking logic as
        `check_exit_on_tick`, reading a candle's `close_price` in place of a
        tick's bid/ask, gated on `_htf_allows_profit_exit`."""
        if not getattr(self.config, "profit_exits_on_candle_close", False):
            return None

        symbol = pos_symbol(position)
        side = pos_side(position)
        ticket = pos_ticket(position)
        entry = pos_entry(position)
        volume = pos_volume(position)

        if not symbol or not side or ticket is None or entry is None or volume is None:
            return None

        if not self._htf_allows_profit_exit(symbol=symbol, position_side=side):
            return None

        price = float(close_price)

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

        if not getattr(state, "be_armed", False):
            if is_break_even(position):
                state.be_armed = True
                state.be_armed_tick = state.ticks_seen
                state.be_armed_price = price
            else:
                state.ticks_seen += 1
                state.prev_price = float(price)
                return None

        profit = pos_profit(position)
        if profit is None:
            profit = 0.0

        if not hasattr(state, "best_profit"):
            state.best_profit = profit
            state.breach_ticks = 0

        if profit > state.best_profit:
            state.best_profit = profit
            state.breach_ticks = 0

        trigger = state.best_profit - self._trail_gap(state.best_profit)

        if 0.00 < profit < state.best_profit:
            if trigger > 0.0 and profit <= trigger:
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="trailing_breach_pct_of_peak",
                )
            state.breach_ticks = getattr(state, "breach_ticks", 0) + 1
            if profit >= state.best_profit:
                state.breach_ticks = 0
        else:
            state.breach_ticks = 0

        state.prev_price = float(price)
        state.ticks_seen += 1
        return None
