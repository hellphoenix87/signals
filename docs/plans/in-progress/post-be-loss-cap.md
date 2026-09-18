# Thread 4: Post-Breakeven Loss Cap (the "reversed after arming" gap)

Status: in-progress
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Thread 4 of `docs/exit-strategy-open-threads.md` was "entirely unscoped, zero discussion" -- until the just-merged Thread 3 trail-rule work (post-be-trail-sim, #55) surfaced that it actually dominates a large chunk of post-breakeven outcomes. `LossExitManager`'s post-BE branch (`app/exit_strategies/managers/loss.py`, `state.be_armed` case) hardcodes `drop_profit_after_be = -5`: if a trade that armed breakeven reverses and drops to -$5 or below, it force-closes. A one-pair/one-window smoke test found this fires on **38.6% of reached_be trades**, and -- critically -- **100% of those firings happen in the "gap"**: the window between breakeven arming and whenever a Thread-3 trail rule's own peak-based activation threshold is cleared ($2-$9 depending on candidate). That means the -$5 cap's cost is a fixed cost independent of which Thread-3 trail rule gets chosen, and needs its own answer.

A corrected gap-reversal counterfactual (same smoke sample, 49 capped trades) found 32.7% would have recovered to positive profit if the cap didn't exist, and removing the cap entirely would have modestly improved pooled P&L (+$79.60) on that sample -- but at the cost of much bigger tail losses (worst case -$18.60 vs. the current -$5 floor). That's a real fixed-vs-variable stop-size tradeoff, not a clear bug like the pre-breakeven soft-SL turned out to be. This plan scales that analysis to the full dataset and decides whether -$5 is well-sized.

**All the measurement code already exists** (`scripts/backtest_exit_strategy.py`'s `simulate_trail_rules`, merged in #55) -- this plan is about running it at scale and, if warranted, extending it to test alternate cap thresholds, not building new instrumentation from scratch.

## Phase 1: Pool the gap/cap diagnostics across the full 7-pair x 3-window sweep

- Re-run the same 21-combination sweep (`EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD NZDUSD` x 3 non-overlapping 4-week windows) with `--simulate-trail --measure-post-be --counterfactual`, same as the merged Thread 3 sweep -- the loss-manager combination and gap-reversal counterfactual fields (`trail_lm_*`, `gap_*`) are already computed as part of that run, no new flags needed.
- Pool across all 21 CSVs (same technique as this session's earlier pooling scripts):
  - What fraction of `reached_be` trades hit the real `-$5` cap, pooled and per-pair? Split by reason (`profit_drop_after_be` vs `be_recovered_after_unprofit`).
  - Confirm (or refute, at scale) the smoke test's finding that ~100% of cap firings happen "in the gap" (`trail_<rule>_lm_capped_in_gap`, any rule) rather than after a trail rule has already activated.
  - Pooled gap-reversal counterfactual: recovery rate, real-capped-total vs. would-have-been-left-alone-total, worst-further-drawdown distribution (percentiles, not just avg/min).
  - Revisit the entry-signal-metadata correlation (confidence/adx/m15_bias/m5_confirm/m1_entry, plus the ticks-to-cap hint from the smoke test) against recovered/not-recovered, pooled this time -- Thread 2's own lesson was that single-pair/window correlations aren't trustworthy until pooled.
- Write up the pooled result in `docs/test-results/post-breakeven-loss-cap.md`, same structure as the existing post-BE trail-simulation doc.

## Phase 2: Decide whether -$5 is the right cap (test alternates if warranted)

- Based on Phase 1's pooled numbers, decide: is `-$5` too tight (cutting off too many real recoveries, per the counterfactual), too loose (letting too much tail risk through), or roughly right?
- If the data supports testing alternates: extend `scripts/backtest_exit_strategy.py` with a small set of candidate cap thresholds (e.g. `-$3`, `-$7`, `-$10`), mirroring the `TRAIL_RULES` pattern -- reuse the existing gap-reversal-counterfactual machinery rather than rebuilding it, since it already isolates exactly the trades and window this needs.
- Update `docs/exit-strategy-open-threads.md` Thread 4 with the pooled findings and recommendation (or explicit "leave at -$5, here's why" if that's the data-backed call).
- **Wiring any change into `LossExitManager`/`Config` is out of scope for this plan** -- same measurement-only boundary Thread 3 used; `Config.EXIT_MAX_LOSS_MONEY` precedent exists for turning a hardcoded threshold into a real config value, but that's a follow-up once a number is chosen and reviewed.

## Out of scope (explicit, not forgotten)

- Wiring any Thread 3 trail rule OR any Thread 4 cap change into production code -- both remain measurement-only until explicitly reviewed and approved for wiring.
- Re-litigating Thread 3's rule choice (`pct60_floor2` stays the provisional "active" label from #55) -- this plan is scoped to the gap/cap question specifically.
- Threads 1 and 2 (session filtering per pair, dynamic pre-BE via signal confidence) -- unrelated, lower priority, not touched here.
- No tests, per MVP/POC mode.
