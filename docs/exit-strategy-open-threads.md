# Exit Strategy Investigation: Open Threads

**Purpose of this doc**: a session on 2026-09-17 did a deep-dive code review and backtest investigation of `app/exit_strategies/`, which opened five distinct threads that are not yet resolved. This document exists so a future session (this one or a fresh one) can resume any of them without re-deriving the context first. Written to be self-contained -- read this instead of replaying the whole conversation.

**How we got here, in one paragraph**: a code review of `app/exit_strategies/managers/{profit,loss}.py` found that a lot of `Config`'s exit-related surface is dead -- config fields exist, are threaded through constructors, and are never read; the real behavior was governed by hardcoded constants instead (`-$5` pre-breakeven stop, `$0.04` post-breakeven trail). We fixed the pre-breakeven side (`EXIT_MAX_LOSS_MONEY` now real, `$5`), built the first-ever full exit-strategy-aware backtest (not just signal-quality proxies), and along the way kept finding more open questions than we closed. This doc is those open questions.

## Current code/branch state (read this first)

- **`master`** has: the wire-risk-gates work (ATR/spread gates built-but-disabled, daily P&L cap live), the n-tick confirmation investigation (rejected, session-filter-bypass bug fixed), a doc typo fix, and the pre-breakeven soft-SL wiring (`EXIT_MAX_LOSS_MONEY=5.0`, real now).
- **`counterfactual-pre-be-backtest`** (still open, PR #54, **not merged**) carries everything since: the currency-conversion bug fix for USDJPY/USDCHF/USDCAD, `scripts/backtest_exit_strategy.py` with `--counterfactual`, `--disable-timeout`, and `--measure-post-be` (built, **not yet run**), plus entry-signal metadata capture (confidence/adx/m15_bias/m5_confirm/m1_entry). **Check whether this PR has merged before starting new work** -- if not, keep building on this branch rather than off master, since master is missing all of this.
- `docs/test-results/pre-breakeven-exit-strategy-backtest.md` has the full counterfactual writeup (corrected numbers, after the currency bug fix): pre-breakeven soft-SL/timeout is a modest net positive (+$84.23 across 585 cut-short trades, 7 pairs x 3 windows) but not unanimous per-pair.

## Thread 1: Session filtering per pair

**Status**: evidence gathered, nothing built or tested.

**Context**: `SessionFilteredSignalStrategy` blocks MTF entries during 08:00-18:59 UTC. That window was derived *entirely from EURUSD* data (`docs/test-results/session-filter-analysis.md`) and has never been checked against any other pair.

**Evidence gathered this session**:
- Live spread snapshot across the 7 majors this account trades: EURUSD 0.00, USDCAD 0.00, GBPUSD/USDCHF/NZDUSD 0.10, AUDUSD 0.20, USDJPY 0.30 pips. Pairs genuinely differ.
- Pre-breakeven cut-short rate (soft-SL + timeout, same uniform config applied to all 7 pairs) ranges from 6.6% (EURUSD) to 29.2% (USDJPY) -- a 4.4x spread. USDJPY's high spread plausibly explains part of this (harder to clear breakeven -> more timeouts).
- **Reasoned but untested hypothesis**: different pairs have different dominant trading sessions (GBPUSD/London, USDJPY/Tokyo, AUDUSD+NZDUSD/Asia-Pacific), so if the session effect is genuinely about session-driven volatility/choppiness, each pair's *own* bad hours plausibly differ from EURUSD's 08-19 UTC block -- but this has never been measured.

**Next step, if resumed**: rerun the original session-filter-by-hour methodology (`docs/test-results/session-filter-analysis.md`'s approach) per-pair instead of EURUSD-only, to find out whether each pair needs its own blocked-hours window, or whether EURUSD's happens to transfer reasonably.

## Thread 2: Dynamic pre-breakeven loss management, keyed on entry-signal characteristics

**Status**: correlation established and validated (not a pair-mix artifact), the actual dynamic-threshold test has **not been run yet** -- this was the agreed immediate next step when the conversation moved to documentation instead.

**Context**: the pre-breakeven soft-SL/timeout currently applies the same fixed thresholds to every trade. The idea: since we can measure entry-signal characteristics at the moment a trade opens, use them to select a per-trade risk profile (not a smooth dial -- see below).

**Evidence gathered this session** (n=3,407 trades, 561 cut-short, pooled across 7 pairs x 3 windows, via `scripts/backtest_exit_strategy.py`'s new metadata capture):
- **Confidence**: higher in cut-short trades than reached_be trades, **consistently across all 7 pairs** (7/7 same sign, magnitude 0.001-0.130). Real effect, not pair-mix.
- **Full timeframe agreement** (m15_bias and m1_entry both match final direction): also higher in cut-short trades, **7/7 pairs consistent**.
- **M5/RSI confirmation** (m5_confirm actually agrees with direction, not just neutral): **lower** in cut-short trades, 6/7 pairs consistent -- the most intuitively sensible of the three (a different indicator family catching something MACD-driven signals miss).
- **ADX**: no consistent pattern (2/7 positive, 5/7 negative) -- confirmed noise, not signal, at both pooled and per-pair granularity.
- **Important technical caveat**: confidence is effectively **binary** (0 or 1) for MTF's entry layer, since it's MACD-only (a single indicator can't produce a graded vote: `confidence = votes_agreeing / total_indicators` degenerates to 0 or 1 with `total_indicators=1`). Full agreement is also binary (true/false). M5 confirm is effectively 2-state (agrees/holds; "opposes" never occurs given the MTF veto logic already in place). **So "dynamic per-trade adjustment" in practice means selecting between a small number of discrete risk profiles at entry time, not a continuously-scaling dial.**
- **Plausible explanation discussed**: these entry indicators (MACD-driven) are reactive/lagging -- ties back to a much earlier finding in this project (`docs/test-results/quick-check-multi-horizon.md`) that passive holding after these signals shows mild *negative* drift, growing with hold time. A "textbook-clean, fully-agreed, high-confidence" setup by these particular indicators may just mean the move is already late/exhausted by the time it fires.

**What this does NOT yet tell us**: whether confidence/agreement/M5-confirm predict actual final win/loss (post-breakeven), only whether a trade *survives to breakeven at all*. Reaching breakeven isn't the same as ending up profitable.

**Next step, if resumed**: the agreed-but-not-yet-run test -- simulate what pre-breakeven thresholds would have looked like if trades were split by (e.g.) M5-confirm state, using the existing 3,407-trade dataset already on disk (no new backtest needed, just a smarter reanalysis of `backtest_results/*_exit_strategy_*.csv` files from the metadata sweep). See if a tighter threshold for "M5 doesn't confirm" trades and/or a looser one for "M5 confirms" trades would have beaten the flat $5/90-tick rule, using the same real-tick-replay approach already built.

## Thread 3: Trailing stop redesign for the post-breakeven (profit-taking) phase

**Status**: design discussed at length, concrete numbers explicitly deferred pending real data. The data-gathering tool is **built but deliberately not run yet** (user's explicit instruction: build it, don't run it until other in-flight work is analyzed).

**Context**: `ProfitExitManager.check_exit_on_tick`/`check_exit_on_candle_close` currently hardcode `breach_threshold = 0.04` (i.e. ~$0.04, ~0.02 pips at current lot size) -- once breakeven arms, tracks the running peak profit and exits the instant profit pulls back more than 4 cents from that peak. This is disconnected from `Config` entirely (`EXIT_TRAIL_START_PIPS`/`EXIT_TRAIL_DISTANCE_PIPS`/`buffer_pips`/`eps_pips` are all dead -- confirmed via grep, referenced nowhere outside their own definitions). `dynamic_buffer` and `is_favorable_vs_anchor` are built, wired into the constructor, and never called -- dead scaffolding for exactly the kind of volatility-aware trail this thread wants.

**The design, confirmed with the user across several exchanges**:
1. Once breakeven arms, keep monitoring profit every tick (already true).
2. As profit grows, the exit trigger should move up too -- a genuine trailing stop, sized to balance two competing goals: protect what's been captured, while leaving room for the position to keep growing. Illustrative example given (numbers explicitly NOT final): peak profit $5 -> trigger at $2 (a $3 give-back allowance); peak grows to $10 -> trigger moves to $7 (same $3 gap). **Open design question, explicitly unresolved**: should the gap stay a fixed dollar amount as peak grows (as in the example), or scale with the peak (percentage/tiered)? A fixed gap gets *relatively* tighter as profit grows (60% given back at a $5 peak vs. 30% at a $10 peak) -- may or may not be intended.
3. Separately, if profit reverses below breakeven entirely after having been positive, that's a **distinct** loss cap -- see Thread 4, not part of this trail mechanism.
4. Two smaller open sub-questions raised, not yet answered: (a) is there a minimum profit floor before the trail engages at all, or does it start trailing from the moment breakeven arms even at $0.01? (b) how does the trail interact with Thread 4's loss cap if the trail's own computed trigger would be negative for a small peak (e.g. peak $1, gap $3 -> trigger -$2)?

**Why numbers shouldn't be guessed**: agreed explicitly that "actual numbers" should come from real post-breakeven price behavior on this account, the same way the pre-breakeven soft-SL threshold conversation worked -- not picked by feel.

**What's built for this** (`scripts/backtest_exit_strategy.py --measure-post-be`, committed on `counterfactual-pre-be-backtest`, verified via static AST checks but never executed against MT5): for every `reached_be` trade, continues watching the *same* real tick stream past arming with **no exit rule applied at all** -- pure observation -- tracking:
- `post_be_peak_profit`: the best profit ever reached in the observation window
- `post_be_ticks_to_peak`: how long that took
- `post_be_max_drawdown_from_peak`: the largest pullback ever seen from *whatever the running peak was at that moment* (not just the final peak) -- this is the number a trail gap actually has to survive without getting stopped out by ordinary noise before a new high is made
- `post_be_final_profit`: profit at the end of the observation budget (default 3000 extra ticks, `--post-be-max-ticks`)

**Next step, if resumed**: run `--measure-post-be` (a full 7-pair x 3-window sweep would match the scale of everything else in this investigation), look at the percentile distributions it prints (median/p10/p90 for each of the four metrics above), and use those real numbers to pick an actual gap-sizing rule -- then decide the fixed-vs-scaling-gap question with data instead of a guess.

## Thread 4: Post-breakeven loss cap (the "reversed after arming" case)

**Status**: identified as a distinct, separate mechanism from Thread 3. Zero discussion or testing beyond noting it exists.

**Context**: `LossExitManager.check_exit_on_tick`'s post-breakeven branch has its own hardcoded safety net: `drop_profit_after_be = -5` -- if a position that already armed breakeven reverses back into loss and drops to -$5 or below, force-close (`reason="profit_drop_after_be"`). There's also a related rule in the same branch: if profit went negative after arming and then recovers into `0 < profit < $0.05`, exit immediately (`reason="be_recovered_after_unprofit"`) to lock in a marginal win rather than let it round-trip again.

**Why this is explicitly a separate thread from Thread 3**: Thread 3 is about *protecting and growing* profit on a position that's doing well. This is about *what happens when a position that was doing fine turns bad again* -- a different risk scenario with its own question of what the right cap should be, whether it should be the same `$5` as the pre-breakeven soft-SL (arbitrary coincidence that it's currently the same hardcoded number) or something else, and whether the `$0.05` marginal-win lock-in threshold makes sense.

**Next step, if resumed**: this hasn't been scoped at all yet -- the first step would be deciding whether it's even worth its own investigation (how often does this actually fire? is `-$5` here principled or just copy-pasted from the pre-breakeven number?) before designing anything.

## Thread 5: PR #54 -- carrying all of this, still unmerged

**Status**: open pull request, not merged, accumulating commits across this whole investigation.

**What's on it, in order**: the currency-conversion bug fix (USDJPY/USDCHF/USDCAD profit was computed in the wrong currency), `--disable-timeout` (tests removing just the 90-tick rule -- result: pooled +$99.53 but entirely driven by one pair, GBPUSD, with 6/7 pairs slightly worse -- inconclusive, no decision made on it), entry-signal metadata capture, and `--measure-post-be` (built, not run).

**Why this matters for a future session**: everything in Threads 1-4 either directly depends on code that's only on this branch (not master), or produced findings that only exist in this branch's commit history and `docs/test-results/pre-breakeven-exit-strategy-backtest.md`. **Before starting fresh work, check `gh pr view 54` for merge status** -- if still open, keep building on `counterfactual-pre-be-backtest` rather than branching fresh off `master`, which is missing all of this.

**Open question not yet asked of the user**: whether to merge PR #54 as-is now (it's a coherent, complete piece of work on its own -- currency fix + counterfactual tooling + results) and continue Threads 1-4 as new branches/PRs, or keep accumulating everything on this one branch until more threads resolve. Worth raising explicitly next session if not already decided.
