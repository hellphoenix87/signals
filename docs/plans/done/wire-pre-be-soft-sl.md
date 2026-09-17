# Wire Pre-Breakeven Soft SL

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

A code review of `app/exit_strategies/` (this session) found `LossExitManager.check_exit_on_tick` hardcoded a `-$5` pre-breakeven stop-loss, completely ignoring `Config.EXIT_MAX_LOSS_MONEY`/`EXIT_MAX_LOSS_PRICE`/`EXIT_MAX_LOSS_PIPS` (three unit choices for the same cap, per `ExitTradeConfig`'s own docstring) despite all three being threaded all the way through the constructor already. User confirmed the intended design (three layers of pre-breakeven loss management: a breakeven-timeout, a wide broker-side SL attached at entry, and a narrower app-side tick-based check) and asked to wire this third layer to its own dedicated config, then backtest how it actually behaves.

## Phase 1: Wire the config

- **`app/exit_strategies/managers/loss.py`**: new `_pre_be_soft_sl_hit(profit, price, entry, side, symbol)` reads whichever of `max_loss_money`/`max_loss_price`/`max_loss_pips` is configured (`> 0`), exiting on whichever fires first if more than one is set; all three at `0` means no pre-breakeven soft SL at all. Replaces the hardcoded `drop_profit = -5` in the pre-breakeven branch of `check_exit_on_tick`. The post-breakeven `-$5` safety net (`drop_profit_after_be`) is a separate, not-yet-discussed mechanism and was intentionally left untouched.
- **`app/config/settings.py`**: `EXIT_MAX_LOSS_MONEY` was `10.0` (never actually used before this) -- set to `5.0` to match the previous hardcoded value exactly, so this PR is a pure wiring fix with zero behavior change, not also a silent risk-tolerance change.
- Verified: money/price/pips units each fire exactly at their configured threshold and not before; all-disabled correctly applies no pre-BE soft SL; confirmed end-to-end through the real `ExitTrade`/`ExitTradeConfig` composition.

## Phase 2: Backtest the real behavior

- **`scripts/backtest_exit_strategy.py`** (new): the first full exit-strategy-aware backtest in this project (every prior report evaluated signal quality via a fixed target/stop proxy, never a real trade lifecycle). Opens simulated positions at real entries from the live MTF signal generator, replays real historical ticks through the actual, production `LossExitManager.check_exit_on_tick`, and reports the pre-breakeven phase's outcome distribution (`soft_sl` / `timed_out` / `reached_be`).
- **Result** (two independent 4-week windows, highly consistent unlike entry-signal win rates): 90.6%-97.4% of trades reach breakeven almost immediately (avg 3-7 ticks) -- a direct consequence of this account's confirmed near-zero spread. The soft SL we just wired barely ever fires (1.3%-1.7% of trades). `timed_out` trades (0.9%-8.1%) lose modestly (avg -$1.00 to -$1.25), well short of the `-$5` cap by construction.
- Full detail: `docs/test-results/pre-breakeven-exit-strategy-backtest.md`.
- **Not pursued here**: simulating past breakeven into the profit-taking/trailing phase (separately known to be governed by a hardcoded `$0.04` pullback, unrelated to `Config` -- a natural next step, not done in this plan).

## Out of scope (explicit, not forgotten)

- The post-breakeven `-$5` safety net (`drop_profit_after_be`) -- same hardcoded-value issue, not discussed or touched here.
- The profit-taking/trailing logic's own dead config (`EXIT_TRAIL_START_PIPS`, `buffer_pips`, `dynamic_buffer`, etc.) -- a separate, larger fix flagged in this session's code review, not part of this plan.
- No tests, per MVP/POC mode. Verified via direct stub/unit checks and the real backtest above.
