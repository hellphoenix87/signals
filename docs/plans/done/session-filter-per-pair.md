# Session Filter Per-Pair Analysis

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Thread 1 of `docs/exit-strategy-open-threads.md` (raised from P3 to a direct blocker): `SessionFilteredSignalStrategy`'s blocked-hours window (08:00-18:59 UTC) was derived entirely from EURUSD data (`docs/test-results/session-filter-analysis.md`) and has never been checked against the other 6 majors this account trades. This matters now because Thread 4's ticks-to-cap dynamic-cap signal needs either a validated EURUSD-only sample (too small, 2 real-recovery trades) or a justified pooling across pairs -- and pooling isn't justified while each pair's own session-driven volatility pattern is uncharacterized. This plan reruns the original session-filter-by-hour methodology per pair and decides whether EURUSD's window transfers, or each pair needs its own.

## Out of scope

- Building the actual dynamic post-BE cap (Thread 4's follow-on) -- this plan only unblocks it.
- Re-deriving the ratio/spread assumptions themselves (uses `Config`'s current live defaults, same as every other pair sweep in this investigation).
- No tests, per MVP/POC mode.
- Deriving each pair's own validated blocked-hours window (Phase 3 below only builds the per-symbol *mechanism*; only EURUSD has a validated window today, from the original `session-filter-analysis.md`). A future session can derive real windows for other pairs once/if they go live, using the same per-pair methodology this plan already ran.

**Revision note**: Phase 3 was added after the plan's original "resolved and wired" state, at the user's explicit request, reversing the original out-of-scope call ("nothing live to wire a multi-pair window into yet") -- building the per-symbol config mechanism now, ahead of `Config.SYMBOLS` actually expanding, so it's in place and tested via backtest before it's ever needed live.

## Phases

### Phase 1: Per-pair hour-by-hour data collection

- Change: none (no code changes) -- run `scripts/backtest_signals.py --symbol <PAIR> --mtf --weeks 4 --spread-pips 1 --start-pos 1` and `--start-pos 28801` (the same two non-overlapping 4-week windows `session-filter-analysis.md` used) for each of the 7 pairs already used elsewhere in this investigation (`EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD NZDUSD`), without `--session-filter` (raw signal, so every UTC hour's win rate can be read before any block is chosen) and at `Config`'s current live target/stop (8/5, no override needed). 14 runs total, each writing a per-signal CSV to `backtest_results/` (`time`, `direction`, `outcome`, `bars`).
- Acceptance criteria: 14 CSVs exist under `backtest_results/`, one per pair/window, each with a nonzero row count.

### Phase 2: Per-pair hour/block analysis and writeup

- Change: a one-off analysis script (`scripts/analyze_session_filter_by_pair.py` or a scratch script run and discarded, matching how the original EURUSD analysis was done) that, for each CSV, recovers true UTC hour from the `time` column via the same offset formula `signal_generation.get_broker_utc_offset_hours` uses in production, builds the hour-by-hour win-rate table per pair (pooled across its 2 windows), and reports: (a) win rate inside EURUSD's existing 08:00-18:59 UTC block vs. outside it, per pair: does the block transfer; (b) whether a different contiguous block would fit that pair's own data meaningfully better than EURUSD's block, using the same "look for a clear contiguous split, don't chase single-hour noise" judgment call the original analysis used (`session-filter-analysis.md` Finding 4).
- Change: `docs/test-results/session-filter-per-pair-analysis.md` -- new writeup with the full per-pair hour tables, the block-transfer verdict per pair, and an explicit answer to Thread 1's actual question (does EURUSD's window transfer well enough to justify pooling the other 6 pairs for Thread 4's dynamic-cap signal, or not).
- Change: `docs/exit-strategy-open-threads.md` Thread 1 section -- status updated to done/resolved, verdict recorded, and Thread 4's "next step, if resumed" note updated to reflect whether pooling is now justified or still blocked.
- Acceptance criteria: the writeup states a clear per-pair verdict (transfers / doesn't transfer / inconclusive) and an explicit pooling recommendation for Thread 4, not just raw tables.

### Phase 3: Per-symbol session-filter config mechanism

- Change: `app/config/settings.py` -- replace the flat `SESSION_FILTER_BLOCKED_HOURS_UTC = list(range(8, 19))` with `SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL: dict[str, list[int]] = {"EURUSD": list(range(8, 19))}`. A symbol with no entry gets no filtering at all -- per this plan's own finding, inheriting EURUSD's window is actively wrong for at least 2 of the 6 other pairs checked, so "no filter" is the only defensible default for an un-derived pair, not "reuse EURUSD's."
- Change: `app/signals/signal_generation.py::strategy_factory` -- add an optional `symbol: str | None = None` parameter; when `USE_SESSION_FILTER` is on, look up `blocked_hours = getattr(config, "SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL", {}).get(symbol, [])` instead of the old flat list. `SessionFilteredSignalStrategy` itself is untouched -- it already just takes whatever blocked-hours list it's handed.
- Change: `app/factory.py` -- pass `symbol=symbol` into the per-symbol `strategy_factory(...)` call in the `Config.SYMBOLS` loop (currently built identically for every symbol despite the loop already being per-symbol).
- Change: `scripts/backtest_signals.py` -- pass `symbol=symbol` into both `strategy_factory` call sites (`run_backtest`, `run_mtf_backtest`) so `--session-filter`/`--no-session-filter` backtests exercise the real per-symbol lookup, not just `USE_SESSION_FILTER`'s on/off toggle.
- Change: `scripts/analyze_session_filter_by_pair.py` -- update its `Config.SESSION_FILTER_BLOCKED_HOURS_UTC` reference to read `SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL.get("EURUSD")` instead (it's specifically checking EURUSD's block against other pairs' data, unaffected in intent).
- Acceptance criteria: `PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --mtf --weeks 1 --session-filter` still blocks 08-18 UTC exactly as before (manual smoke check, no regression); `--symbol GBPUSD --mtf --weeks 1 --session-filter` produces signals during 08-18 UTC (GBPUSD has no entry in the new dict, so the filter should no-op for it) -- confirms the per-symbol lookup is live, not just present in code.
- **Verified** (in-process, pinned `SESSION_FILTER_UTC_OFFSET_HOURS=5` -- see the unrelated bug noted below for why pinning was needed): EURUSD with the filter on produced signals in UTC hours `{0,1,2,4,6,7,19,20,21,22}` only -- zero in the blocked 8-18 range. GBPUSD with the filter on (no per-symbol entry) produced signals across all 22 observed hours, including 8-18 -- confirms the no-entry-means-no-filtering default is live, not just configured.
- **Unrelated bug found while verifying, not fixed here**: `get_broker_utc_offset_hours()` (`app/signals/signal_generation.py`) reads the last live tick's timestamp with no freshness check. With the market closed (this verification ran on a Sunday), it returned nonsense (-29, observed) instead of the real ~5, because the last tick was stale. This is a **pre-existing bug that predates this plan and affects every symbol's filter equally**, including EURUSD's already-live one -- not introduced or fixed by the per-symbol work here. Flagged in a code comment on the function; needs its own follow-up (e.g. reject a tick older than some threshold and fail safe to no filtering, or to the last-known-good offset).

### Phase 4: Fix the stale-tick offset bug found in Phase 3

- Change: `app/signals/signal_generation.py::get_broker_utc_offset_hours` -- the computed offset is now sanity-bounded (`MAX_PLAUSIBLE_UTC_OFFSET_HOURS = 15`, generous vs. real-world timezone range); a value outside that range means the last tick was stale (market closed, or no tick yet since a restart), not a real offset. Falls back to a new module-level `_last_known_good_utc_offset_hours` cache (the last value that *did* look plausible, so Friday's good value survives through the weekend) instead of the hardcoded `default` every time, only using `default` if no good value has ever been seen this process.
- Acceptance criteria: with the real live (currently stale, weekend) MT5 tick, `get_broker_utc_offset_hours()` returns a plausible value (~5) instead of the previously-observed -29; a fresh-tick case still computes the real offset and caches it; a stale-tick case after a good one was cached falls back to the cached value, not `default`; a stale tick with no cache yet falls back to `default`.

## Open questions

None -- methodology, pair list, and windows are all already established by prior sessions' work (`session-filter-analysis.md`, and the 7-pair list used throughout Threads 3/4's sweeps).

## QA

(MVP/POC mode -- no qa agent pass.)

## Result

Done. `docs/test-results/session-filter-per-pair-analysis.md` has the full writeup. Verdict: EURUSD's session-filter block does not transfer -- AUDUSD and USDJPY show a reproducible *opposite* effect (both non-overlapping windows), USDCHF shows no reproducible effect, GBPUSD/NZDUSD show the same direction but far weaker. Pooling the other 6 pairs to validate Thread 4's dynamic post-BE cap signal is **not justified**. `docs/exit-strategy-open-threads.md` Threads 1 and 4 updated accordingly. No production config/behavior change -- `Config.SYMBOLS` is EURUSD-only and EURUSD's own window is confirmed correct.

One unplanned code change beyond the original scope: the first data-collection pass silently ran *with* the live `Config.USE_SESSION_FILTER=True` default still on (omitting `--session-filter` doesn't turn it off, since it's already on by default), which zeroed out every signal in the exact hours this analysis needed to read. Added `--no-session-filter` to `scripts/backtest_signals.py` to force it off for this kind of unfiltered discovery pass, and reran all 14 combinations.

**Phase 3 (added after the above, per explicit user request)**: done. Per-symbol session-filter config mechanism built and verified (see Phase 3's Verified note above). `Config.SYMBOLS` is still EURUSD-only, so no live behavior change -- the mechanism is in place for when a second pair is added, rather than defaulting it to (wrongly) inherit EURUSD's window. Surfaced one pre-existing, unrelated bug in `get_broker_utc_offset_hours()` (stale-tick flakiness) along the way -- documented, not fixed, out of scope for this plan.

**Phase 4 (added after the above, per explicit user request)**: done. `get_broker_utc_offset_hours()` now rejects implausible offsets (stale-tick artifacts) and falls back to the last known-good value instead of silently returning garbage. Verified against a mocked fresh tick, a mocked stale tick with a cached good value, a mocked stale tick with no cache, a `None` tick -- and against the real live (still-stale, weekend) MT5 connection, which now returns 5 instead of the earlier-observed -29. Re-ran the same end-to-end EURUSD/GBPUSD session-filter check from Phase 3 *without* pinning the offset this time -- identical results to the pinned run, confirming the fix closes the loop.
