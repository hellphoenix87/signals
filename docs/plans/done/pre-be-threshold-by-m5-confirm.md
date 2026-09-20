# Pre-Breakeven Threshold Split by M5-Confirm State

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Thread 2 of `docs/exit-strategy-open-threads.md` (P1 -- highest remaining priority; every other thread is resolved). The pre-breakeven soft-SL (`Config.EXIT_MAX_LOSS_MONEY`, currently $5 flat) applies the same threshold to every trade. Prior analysis (n=3,407 trades pooled 7 pairs x 3 windows) found M5-confirm state (whether RSI/M5 actually agrees with the trade direction, vs. just neutral) predicts whether a trade survives to breakeven at all, consistently across all 7 pairs -- but never turned that into an actual threshold test. This plan runs that test: would a tighter soft-SL for "M5 doesn't confirm" trades and/or a looser one for "M5 confirms" trades beat today's flat $5 rule.

## Revision of the assumed approach (read before Phase 1)

The doc's own "next step" text assumed this needed "no new backtest, just a smarter reanalysis of `backtest_results/*_exit_strategy_*.csv` files from the metadata sweep." On inspection that's not enough: the existing CSVs record only the real $5/90-tick rule's *final* outcome per trade (plus a counterfactual continuation only from the point that rule actually cut a trade) -- they don't contain the trade's minimum pre-breakeven excursion, so there's no way to know from that data alone whether a *different* threshold would have cut a trade earlier or later. Properly answering this needs a real per-candidate replay, the same way Thread 3 (`--simulate-trail`) and Thread 4 (`--sweep-post-be-cap`) both built dedicated multi-candidate simulators rather than guessing from single-threshold data. This plan builds the pre-BE equivalent instead of trying to force an answer out of insufficient data.

Also found while scoping this: the most recent on-disk sweep data is inconsistent (EURUSD's latest files are accidental duplicate reruns of the same window with no window 2/3 present at all), and -- more importantly -- every non-EURUSD pair's older sweep data predates the Thread 1 fix and was silently being filtered by EURUSD's own session-filter window this whole time (the flat `SESSION_FILTER_BLOCKED_HOURS_UTC` applied to every symbol before that fix). A fresh sweep is the correct move regardless of the above.

## Out of scope

- Wiring a discovered split into `Config`/`LossExitManager` -- only if the result is a clean, decisive win; otherwise this plan documents the finding and stops, same as how Thread 2 was scoped from the start ("bounded ceiling"). A decision on wiring is made once the data is in, not pre-committed here.
- Sweeping `be_arming_ticks` (the 90-tick timeout) as a second dimension -- money-threshold only, per the doc's own suggested next step. Left as a follow-up if the money-threshold split alone doesn't tell a clean story.
- Post-breakeven behavior of any kind -- this is pre-BE-phase only, matching every other pre-BE simulation already in this script.
- No tests, per MVP/POC mode.

## Phases

### Phase 1: Build `--sweep-pre-be-threshold` in `scripts/backtest_exit_strategy.py`

- Change: new `PRE_BE_THRESHOLD_CANDIDATES: list[float] = [2.0, 3.0, 4.0, 5.0, 7.0, 10.0]` module constant (5.0 = today's live `Config.EXIT_MAX_LOSS_MONEY`), matching the existing `CAP_THRESHOLDS`/`FULL_LIFECYCLE_CAP_CANDIDATES` pattern.
- Change: new `simulate_pre_be_threshold_sweep()`, built by directly mirroring `simulate_full_lifecycle_cap_sweep()` (multiple real `ExitTrade` instances, one per candidate, built via `create_exit_trade(..., config=ExitTradeConfig(max_loss_money=c))`, replayed against one shared real tick stream) -- but calling only `loss_manager.check_exit_on_tick` per candidate per tick (no `profit_manager`, out of scope), and stopping a candidate on either an exit action (`soft_sl`/`timed_out`) or `state.be_armed` becoming true with no action (`reached_be`), mirroring `simulate_pre_be_phase`'s own stopping rule. Reuses the real `LossExitManager`/`ExitTradeConfig` rather than reimplementing the threshold/timeout state machine, since that machine has a real timing subtlety (the arming-ticks timeout check happens on a tick *after* the last soft-SL-checked tick, not the same one) not worth risking a subtly-wrong reimplementation of for money-moving logic.
- Change: new `--sweep-pre-be-threshold` CLI flag, wired into `run()` alongside the existing `full_lifecycle`/`sweep_post_be_cap` branch structure -- builds the candidate `ExitTrade` list once, calls the new simulator per signal instead of `simulate_pre_be_phase`, keeping metadata capture (confidence/adx/m15_bias/m5_confirm/m1_entry/pullback_completed) unchanged.
- Change: new `write_pre_be_threshold_sweep_csv()`/`summarize_pre_be_threshold_sweep()`, mirroring `write_full_lifecycle_cap_sweep_csv()`/`summarize_full_lifecycle_cap_sweep()`'s shape (pooled per-candidate totals for a sanity check; the by-m5-confirm split happens in a separate analysis step, same division of labor as Thread 1's `analyze_session_filter_by_pair.py`).
- Acceptance criteria: smoke-test on one pair/window (EURUSD, `--weeks 1`) with `--sweep-pre-be-threshold`; the `pre5_*` candidate's outcome/profit/ticks must match a plain (no-flag) run's real outcome/profit/ticks_used trade-for-trade (same threshold as production, so results must be identical -- this is the correctness check against reimplementing the real logic wrong).

### Phase 2: Run the fresh 7-pair x 3-window sweep

- Change: none (no code changes) -- run `scripts/backtest_exit_strategy.py --symbol <PAIR> --weeks 4 --start-pos <1|28801|57601> --sweep-pre-be-threshold` for all 7 pairs (`EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD NZDUSD`) x 3 non-overlapping 4-week windows -- 21 runs, each on current `master` (post-Thread-1-fix, so the per-symbol session filter behaves correctly for every pair this time, unlike the stale on-disk data).
- Acceptance criteria: 21 CSVs exist under `backtest_results/`, one per pair/window, each with a nonzero row count and populated `m5_confirm`/`pre<C>_outcome` columns for every candidate.

### Phase 3: Split by M5-confirm, analyze, decide, write up

- Change: new one-off analysis script (`scripts/analyze_pre_be_threshold_by_m5_confirm.py`), mirroring `analyze_session_filter_by_pair.py`'s shape -- pools all 21 CSVs, splits trades into `m5_confirm == "true"`/agrees vs. not, and for each `PRE_BE_THRESHOLD_CANDIDATES` value reports: total pre-BE-phase P&L, win rate (of decided trades), reached-BE rate, within each group -- so the best threshold per group can be read off directly, compared against today's flat $5 baseline applied uniformly.
- Change: `docs/test-results/pre-be-threshold-by-m5-confirm.md` -- writeup with the full per-group-per-candidate table and an explicit verdict: does a split (tighter for non-confirm / looser for confirm, or any other combination) beat flat $5, by how much, and is it decisive enough to wire.
- Change: `docs/exit-strategy-open-threads.md` Thread 2 section -- status updated with the result either way (resolved-and-wired, or resolved-not-actionable, matching how Threads 3/4 recorded their outcomes).
- Change (only if the result is a clean, decisive win per the Out-of-scope note above): `Config.EXIT_MAX_LOSS_MONEY` and `LossExitManager`/`strategy_factory` wiring to make the threshold conditional on `m5_confirm`, following the same "read via `getattr(config, ...)`, inject through `__init__`" conventions as every other exit-strategy config value in this codebase.
- Acceptance criteria: the writeup states a clear win/no-win verdict per group with numbers, not just raw tables; if wired, a smoke-test run shows the conditional threshold actually taking effect (mirroring Phase 1's correctness-check pattern).

## Open questions

None -- methodology (real-tick replay, multi-candidate single-pass simulation) and the pair/window list are both already established by this investigation's prior work.

## QA

(MVP/POC mode -- no qa agent pass.)

## Result

Done. `docs/test-results/pre-be-threshold-by-m5-confirm.md` has the full writeup. Pooled, the result matched Thread 2's own hypothesis exactly (tighter for M5-non-confirm, looser for M5-confirm beats flat $5) -- but a per-pair reproducibility check (the same discipline used for Thread 1) found the confirm-side half doesn't survive at all (driven almost entirely by one outlier pair, NZDUSD; GBPUSD's own best threshold for that group is the opposite direction). The non-confirm-side half is more consistent (5/7 pairs improve) but still not unanimous. **Verdict: not actionable as a hard-coded split.** No `Config`/`LossExitManager` change made. `docs/exit-strategy-open-threads.md` Thread 2 updated, plus a closing note: all five of this doc's original threads are now resolved, none produced a further production win, and the next real lever for this project (MACD-only MTF entry-signal quality itself) is outside this doc's scope.

One bug caught and fixed before it could corrupt the analysis: `m5_confirm` is a raw direction string ("hold"/"buy"/"sell"), not a boolean -- an early draft of the split logic treated "non-hold" as "confirms," which would have silently swapped the two groups (a "sell" value would have counted as confirming a "buy" trade). Caught via a sanity check against a sample file's known counts before trusting the real 21-file run.
