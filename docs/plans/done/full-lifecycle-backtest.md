# Full-Lifecycle Exit-Strategy Backtest

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Every exit-strategy backtest built so far in this project stops short of the real thing: `simulate_pre_be_phase` only replays the real `LossExitManager` up to breakeven arming; `--measure-post-be` watches post-breakeven price with *no* exit rule at all; `--simulate-trail`/`CAP_THRESHOLDS` test candidate rules that reimplement formulas independently rather than calling the real `ProfitExitManager`. None of them have ever run a trade through the actual, complete `ExitTrade` (both managers, real precedence, real config) from entry to a genuine final exit. `docs/test-results/pre-breakeven-exit-strategy-backtest.md` flagged this explicitly as "the natural next step" months ago; it's still not been done.

Now that Thread 3 (trailing stop, `pct60_floor2`) and Thread 4 (post-BE loss cap, `-$3`) are both actually wired into `ProfitExitManager`/`LossExitManager` (this session, PRs #56/#57), a true full-lifecycle backtest is finally meaningful -- it will report the *real*, complete, single realized P&L per trade under the system as it actually exists today, not a phase-by-phase reconstruction.

## Phase 1: Build `--full-lifecycle` and smoke-test it

- **`scripts/backtest_exit_strategy.py`**: new `simulate_full_lifecycle()` function and `--full-lifecycle` flag. For each buy/sell signal from `strategy_factory(config=Config)` (MTF, live default -- unchanged from the rest of this script), opens a simulated position and replays real historical ticks through the actual `exit_trade._loss_manager.check_exit_on_tick` THEN (if no loss action) `exit_trade._profit_manager.check_exit_on_tick`, in that order -- the exact precedence `ExitTrade.on_tick` itself uses, gated the same way on `config.profit_exits_on_tick` (default `True`). One single `PosState` carries continuously across the whole trade (pre-BE arming through post-BE trailing/cap), same object, no phase boundary.
- Records the real exit `reason` whichever manager produces it (`profit_drop`, `failed_to_reach_be`, `profit_drop_after_be`, `be_recovered_after_unprofit`, `trailing_breach_pct_of_peak`) or `exhausted` if neither manager ever exits within the tick budget. A generous tick budget is needed (pre-BE + full post-BE lifecycle) -- reuse the same ~3000-4000 tick scale already used for post-BE observation elsewhere in this script.
- Smoke-test on one pair/window (EURUSD, window 1) before committing to the full sweep. Sanity-check the exit-reason distribution looks plausible (e.g. `trailing_breach_pct_of_peak` should be common, `exhausted` should be rare given the generous budget).

## Phase 2: Run the 7-pair x 3-window sweep, pool, write up

- Same 21-combination sweep as the rest of this project's exit-strategy work (`EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD NZDUSD` x 3 non-overlapping 4-week windows).
- Pool the real, single realized P&L per trade across all 21 CSVs -- this is the number that actually answers "is the bot profitable" for the first time in this whole investigation, using the system as it actually exists today (post Thread 3/4 wiring), not a reconstructed/candidate approximation.
- Break down by exit reason (how much of total P&L comes from each), same way the pre-BE backtest broke down `soft_sl`/`timed_out`/`reached_be`.
- Write up in `docs/test-results/full-lifecycle-backtest.md`.
- Update `docs/exit-strategy-open-threads.md`'s opening "How we got here" framing to note this gap is now closed, and/or add a summary note pointing to the new doc.

**Result**: pooled across 4,276 trades, total realized P&L **-$839.91**, every pair negative. Root cause: a win/loss size mismatch (trail exits avg +$1.44, cap exits avg -$3.16 -- over 2x), not a low win rate (trade counts were close to balanced). Full writeup: `docs/test-results/full-lifecycle-backtest.md`.

## Phase 3: Retune Thread 4's cap against the real trail (scope expansion, per explicit user instruction)

**Originally out of scope** (see the struck-through line below, kept for history) -- Phase 2's result made clear that `-$3` was picked in an isolated sweep before Thread 3 was wired, never accounting for the real trail's actual win size. The user asked to retune it properly.

- `simulate_full_lifecycle_cap_sweep`: replays `FULL_LIFECYCLE_CAP_CANDIDATES` against the same tick stream in one pass, each with its own independent `LossExitManager`/`PosState` but the SAME real trail formula, via `--sweep-post-be-cap`.
- Fixed a real bug found along the way: `getattr(self.config, "post_be_loss_cap_money", 5.0) or 5.0` in `LossExitManager` silently discarded a deliberately-configured `0.0`, falling back to `5.0` -- fixed before testing the `$0` candidate.
- Swept `$0/$1/$1.5/$2/$2.5/$3` pooled (3,931 trades) and EURUSD-only (509 trades, the only pair actually live in `Config.SYMBOLS`). **`-$1` won both** -- pooled -$852.83 vs `-$3`'s -$1,125.70; EURUSD-only -$23.80 vs `-$3`'s -$77.40. Not monotonic: `$0` (near-zero tolerance, ~5% win rate) is worse than `$1`, confirming a real valley rather than "tighter is always better."
- **Wired**: `Config.EXIT_POST_BE_LOSS_CAP_MONEY = 1.0` (was `3.0`), explicitly documented as temporary -- even at this best-tested value the system remains net negative (-$852.83 pooled / -$23.80 EURUSD-only). Exit-side tuning has been pushed about as far as bounded sweeps reasonably go; further improvement needs entry-signal quality (Threads 1/2), not more retuning of this number.

~~Any further retuning of Thread 3/4's wired values based on this result -- this backtest measures the system as currently wired; if the result suggests either value should change, that's a new, separate investigation, not folded into this one.~~ -- superseded, see above.

## Out of scope (explicit, not forgotten)

- Candle-close profit exits (`profit_exits_on_candle_close`, default `False`) -- matches the live default, not exercised here.
- Threads 1 and 2 (session filtering per pair, dynamic pre-BE via signal confidence) -- explicitly deferred by the user, not touched here.
- Testing cap values tighter than `$0` (none exist -- `$0` is the floor by construction) or between the tested half-dollar steps (e.g. `$0.5`, `$0.75`) -- the tested range already found the valley's approximate location, finer resolution not chased.
- No tests, per MVP/POC mode.
