# Session Filter Per-Pair Analysis

Status: in-progress
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Thread 1 of `docs/exit-strategy-open-threads.md` (raised from P3 to a direct blocker): `SessionFilteredSignalStrategy`'s blocked-hours window (08:00-18:59 UTC) was derived entirely from EURUSD data (`docs/test-results/session-filter-analysis.md`) and has never been checked against the other 6 majors this account trades. This matters now because Thread 4's ticks-to-cap dynamic-cap signal needs either a validated EURUSD-only sample (too small, 2 real-recovery trades) or a justified pooling across pairs -- and pooling isn't justified while each pair's own session-driven volatility pattern is uncharacterized. This plan reruns the original session-filter-by-hour methodology per pair and decides whether EURUSD's window transfers, or each pair needs its own.

## Out of scope

- Building the actual dynamic post-BE cap (Thread 4's follow-on) -- this plan only unblocks it.
- Wiring per-pair blocked-hours config into `SessionFilteredSignalStrategy`/`Config` -- `Config.SYMBOLS` is EURUSD-only in production today, so there is nothing live to wire a multi-pair window into yet. This plan's output is a decision + writeup, and a config change only if EURUSD's own window turns out wrong for EURUSD itself.
- Re-deriving the ratio/spread assumptions themselves (uses `Config`'s current live defaults, same as every other pair sweep in this investigation).
- No tests, per MVP/POC mode.

## Phases

### Phase 1: Per-pair hour-by-hour data collection

- Change: none (no code changes) -- run `scripts/backtest_signals.py --symbol <PAIR> --mtf --weeks 4 --spread-pips 1 --start-pos 1` and `--start-pos 28801` (the same two non-overlapping 4-week windows `session-filter-analysis.md` used) for each of the 7 pairs already used elsewhere in this investigation (`EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD NZDUSD`), without `--session-filter` (raw signal, so every UTC hour's win rate can be read before any block is chosen) and at `Config`'s current live target/stop (8/5, no override needed). 14 runs total, each writing a per-signal CSV to `backtest_results/` (`time`, `direction`, `outcome`, `bars`).
- Acceptance criteria: 14 CSVs exist under `backtest_results/`, one per pair/window, each with a nonzero row count.

### Phase 2: Per-pair hour/block analysis and writeup

- Change: a one-off analysis script (`scripts/analyze_session_filter_by_pair.py` or a scratch script run and discarded, matching how the original EURUSD analysis was done) that, for each CSV, recovers true UTC hour from the `time` column via the same offset formula `signal_generation.get_broker_utc_offset_hours` uses in production, builds the hour-by-hour win-rate table per pair (pooled across its 2 windows), and reports: (a) win rate inside EURUSD's existing 08:00-18:59 UTC block vs. outside it, per pair: does the block transfer; (b) whether a different contiguous block would fit that pair's own data meaningfully better than EURUSD's block, using the same "look for a clear contiguous split, don't chase single-hour noise" judgment call the original analysis used (`session-filter-analysis.md` Finding 4).
- Change: `docs/test-results/session-filter-per-pair-analysis.md` -- new writeup with the full per-pair hour tables, the block-transfer verdict per pair, and an explicit answer to Thread 1's actual question (does EURUSD's window transfer well enough to justify pooling the other 6 pairs for Thread 4's dynamic-cap signal, or not).
- Change: `docs/exit-strategy-open-threads.md` Thread 1 section -- status updated to done/resolved, verdict recorded, and Thread 4's "next step, if resumed" note updated to reflect whether pooling is now justified or still blocked.
- Acceptance criteria: the writeup states a clear per-pair verdict (transfers / doesn't transfer / inconclusive) and an explicit pooling recommendation for Thread 4, not just raw tables.

## Open questions

None -- methodology, pair list, and windows are all already established by prior sessions' work (`session-filter-analysis.md`, and the 7-pair list used throughout Threads 3/4's sweeps).

## QA

(MVP/POC mode -- no qa agent pass.)
