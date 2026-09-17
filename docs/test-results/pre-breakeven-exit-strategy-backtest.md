# Pre-Breakeven Exit Strategy Backtest

**Question**: now that `LossExitManager`'s pre-breakeven soft SL actually reads `Config.EXIT_MAX_LOSS_MONEY` (fixed at `$5.0`, see `wire-pre-be-soft-sl`), how does this part of the exit strategy actually behave against real historical ticks?

**This is the first full exit-strategy-aware backtest in this project.** Every prior report evaluated *signal quality* via a fixed target/stop proxy (`scripts/backtest_signals.py`) -- explicitly not a trade simulation, since `TradingMode.BACKTEST`'s mark-to-market has been a stub since day one. This one is different: it takes real entries from the live MTF signal generator and feeds real historical ticks through the actual, production `LossExitManager.check_exit_on_tick` -- the real code, not a proxy for it. Scope is deliberately narrow: only the **pre-breakeven phase** (the three-layer loss management discussed in this session) -- it stops as soon as a position reaches break-even, since what happens after that (profit-taking/trailing) is a separate, not-yet-validated mechanism (see the exit-strategy code review from this same session: the trailing logic uses a hardcoded `$0.04` pullback, unrelated to `Config`).

## Method

New `scripts/backtest_exit_strategy.py`. For each buy/sell signal from `strategy_factory(config=Config)` (MTF, the live default) over real M1/M5/M15 history, opens a simulated position at the first real tick at/after the signal's candle close (entry price = that tick's ask for buy / bid for sell, matching `TradeExecutor._resolve_price`'s own convention), then replays up to 500 real historical ticks (`mt5.copy_ticks_from`) through the actual `LossExitManager.check_exit_on_tick` (same object graph `create_exit_trade` builds for live trading), computing running position profit from `(price - entry) * lot * contract_size` each tick (lot fixed at `0.2`, matching the current `$1000` sizing basis; contract size from the real `broker.get_lot_value`). Records one of:

- **`soft_sl`** -- the pre-breakeven soft SL fired (`reason="profit_drop"`, now `Config.EXIT_MAX_LOSS_MONEY=5.0`)
- **`timed_out`** -- `EXIT_BE_ARMING_TICKS` (90) expired without reaching breakeven (`reason="failed_to_reach_be"`) -- always a small loss strictly between `-$5` and `$0`, since a tick actually crossing `-$5` would have triggered `soft_sl` first
- **`reached_be`** -- breakeven armed within the window; the pre-breakeven phase ends here, not simulated further
- **`exhausted`** -- ran out of the 500 fetched ticks before either resolved (a data-fetch limit of this script, not a real trading outcome)

## Results

| Window | Trades | `soft_sl` | `timed_out` | `reached_be` | Avg ticks to `reached_be` |
|---|---|---|---|---|---|
| Window 1 (most recent 4 weeks) | 149 | 2 (1.3%), avg -$5.20 | 12 (8.1%), avg -$1.25 | 135 (90.6%) | 7.0 |
| Window 2 (preceding, non-overlapping 4 weeks) | 116 | 2 (1.7%), avg -$5.80 | 1 (0.9%), avg -$1.00 | 113 (97.4%) | 3.0 |

**Highly consistent across both independent windows** -- a much tighter agreement than any of the entry-signal win-rate numbers in this project's other reports (which ranged 36.7%-49.2% across the same two windows). That consistency itself is informative: this mechanism's behavior is far more stable than the entry signal's own directional edge.

## Findings

1. **Breakeven is reached almost immediately for the overwhelming majority of trades (90.6%-97.4%), in an average of 3-7 ticks.** This is a direct, measurable consequence of this account's confirmed near-zero real spread (`zero-spread-retune.md`): with almost no spread to overcome, "profit >= $0" is a trivially low bar the moment price moves even slightly favorably (or the market is flat and the tiny bid/ask gap closes). On a real broker with a meaningful spread, this bar would be materially harder to clear and this distribution would likely look different -- this result is specific to this account's conditions, not a universal property of the strategy.

2. **The soft SL we just wired barely ever fires (1.3%-1.7% of trades).** The overwhelming majority of trades that don't reach breakeven immediately still recover to it before either the soft SL or the 90-tick timeout catches them. This means the `$5` vs. the previously-configured `$10` distinction discussed earlier in this session affects a very small fraction of trades in practice, under current conditions -- not because the layer is unimportant, but because it's rarely the layer that ends up mattering.

3. **`timed_out` trades lose modestly, not catastrophically** (avg -$1.00 to -$1.25, well inside the `-$5` soft-SL band by construction). The 90-tick arming window is doing real work for a small minority of trades (0.9%-8.1%) that just don't recover in time -- cutting them at a small loss rather than letting them sit exposed to the wider broker-side SL (5 pips ≈ -$10).

4. **This says nothing about post-breakeven profitability.** 90%+ of trades clear this phase, but what happens next -- the hardcoded `$0.04` trailing pullback found in the code review -- is untested here and is a much more likely candidate for where real trade outcomes are decided, given how easy breakeven itself turns out to be to reach.

## Caveats

- Lot size is fixed at a documented `0.2`, not derived per-trade via `RiskManager` -- doesn't change the tick-timing analysis (which is spread/price-driven, not size-driven) but the dollar amounts scale linearly with whatever the real lot size is at trade time.
- Entry price uses the first real tick at/after the signal's candle close, not accounting for any execution latency a live order would have.
- Same account-specific-spread caveat as everything else in this project: `MetaQuotes-Demo` is a generic test server, not a specific broker's demo -- this near-instant breakeven finding is the most spread-sensitive result in the whole project so far, and would be the first thing to re-check against a real broker's actual spread.
- Only the pre-breakeven phase is simulated. A full lifecycle backtest (through the post-breakeven trailing/profit-taking logic) is the natural next step and is still not done.
