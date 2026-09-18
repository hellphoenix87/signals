# Post-Breakeven Trailing-Stop Rule Simulation

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Thread 3 of `docs/exit-strategy-open-threads.md` (P0 -- highest priority): `ProfitExitManager`'s real trailing gap is a hardcoded `$0.04`, disconnected from `Config` entirely. `--measure-post-be` (built earlier, now run across 7 pairs x 3 windows, 2,816 pooled `reached_be` trades) confirmed real post-breakeven drawdown-from-peak has a **p10 of $3.47** -- the current $0.04 gap isn't a trailing stop, it's effectively "exit on the first tick of pullback." That observational data can size a starting gap but can't settle fixed-vs-scaling on its own, since "drawdown from peak" there is measured against whatever the *running* peak was at each moment, not the trade's eventual final peak. This plan builds an actual rule simulator: replay a handful of candidate trailing-gap rules through the same real tick data and see which ones actually capture the most profit.

## Phase 1: Build `--simulate-trail` and smoke-test it

- **`scripts/backtest_exit_strategy.py`**: new `simulate_trail_rules()` function, called for every `reached_be` trade when `--simulate-trail` is passed (reuses the same extended tick fetch/budget as `--measure-post-be`, `--post-be-max-ticks`). Single pass over the post-arming tick stream computing the shared running peak once, then for each of a fixed set of named candidate rules, a `trigger = peak - gap(rule, peak)` (clamped to a `$0` floor -- reversal below breakeven is Thread 4's territory, explicitly out of scope here) checked every tick; records the rule's captured profit and ticks-held at the first tick profit drops to/below its trigger, or the observed final profit if it never triggers before the tick budget runs out.
- **Candidate rules**, informed by the pooled percentiles (median drawdown $6.49, p25 $4.70, p75 $9.55): `flat5`, `flat7`, `flat9` (flat-dollar giveback allowance), `pct40_floor2`, `pct60_floor2` (gap = max($2, 40%/60% of peak) -- a floor so tiny peaks aren't stopped by trivial noise). Five rules is enough to read both the flat-vs-scaling question and roughly where the flat gap should sit, without turning this into an unbounded parameter sweep.
- Extend the per-trade CSV (`write_results_csv`) with one captured-profit/ticks/triggered column per rule; extend `summarize()` to print, per rule: total captured P&L across all `reached_be` trades in that run, win rate, avg ticks held -- alongside the already-printed peak-profit ceiling and no-exit-final-profit floor for reference.
- Smoke-test on one pair/window (EURUSD, window 1) before committing to the full sweep.

## Phase 2: Run the 7-pair x 3-window sweep, pool results, decide

- Re-run the same 21-combination sweep (`EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD NZDUSD` x 3 non-overlapping 4-week windows) with `--simulate-trail --measure-post-be` together (one tick fetch covers both).
- Pool the per-rule captured P&L across all 21 CSVs (same pooling approach as the `--measure-post-be` percentile pooling this session already did).
- Write up the result: which rule(s) actually beat both "hold forever" (the -$997.44 floor already measured) and get meaningfully closer to the peak-profit ceiling (+$12,750.34); whether a flat or scaling gap wins and by how much; pick a concrete gap size/formula to recommend wiring into `ProfitExitManager` (wiring itself is out of scope for this plan -- see below).
- Update `docs/exit-strategy-open-threads.md` Thread 3 with the result and close out its two sub-questions ((a) minimum profit floor before trail engages, (b) interaction with Thread 4's loss cap) to whatever extent this simulation's rule design settles them.

## Out of scope (explicit, not forgotten)

- Actually wiring the chosen rule into `ProfitExitManager`/`Config` -- this plan is measurement only, matching how Thread 3 was scoped from the start ("build the tool, don't run it until other in-flight work is analyzed" -> now "run it and decide the rule shape," wiring is a further step after a rule is chosen).
- Thread 4 (post-breakeven loss cap, the "reversed after arming" case) -- explicitly a separate mechanism, not touched here.
- Threads 1 and 2 (session filtering per pair, dynamic pre-BE via signal confidence) -- unrelated, lower priority.
- No tests, per MVP/POC mode.
