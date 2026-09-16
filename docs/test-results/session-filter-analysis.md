# Session (Hour-of-Day) Filter Analysis

Date: 2026-09-15/16

## Setup

- **Symbol**: EURUSD, two independent 4-week M1 windows (window 1: `--start-pos 1`, the most recent; window 2: `--start-pos 28801`, the immediately preceding, non-overlapping 4 weeks — same windows used throughout this investigation).
- **Motivation**: no prior report had looked at whether signal quality varies by time of day. Given spread (the actual profitability blocker found throughout this investigation) and volatility both vary heavily by trading session, this checks whether restricting entries to specific hours improves the win-rate-vs-breakeven gap.
- **Method**: for each backtested signal, its UTC hour was recovered from the candle timestamp. `MarketData` constructs candle times via `datetime.fromtimestamp` on the raw MT5 epoch, which (on this machine, against this broker) is offset from true UTC by a fixed number of hours, determined empirically by comparing a live tick's epoch against true UTC at analysis time (`candle_frame_hour - 5) % 24 = true_utc_hour`, at the time of this analysis). Production code (`get_broker_utc_offset_hours`, `app/signals/signal_generation.py`) computes this live rather than hardcoding it, since it depends on both the local machine's timezone and the broker server's.
- **Ratios used**: MTF at its own best-found ratio (target=7/stop=5, from `final-signal-quality-summary.md`); single-timeframe at its own best-found ratio (target=5/stop=5). Both with a realistic 1-pip round-trip spread.
- **Commands** (production-verified, after implementation — see Phase 2 below; the original discovery pass used one-off scratch scripts with identical replay logic):
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --session-filter --target-pips 7 --stop-pips 5 --spread-pips 1 [--start-pos 28801]
  ```

## Results: MTF (target=7/stop=5, breakeven 41.7%)

Full hour-by-hour breakdown, window 1:

| UTC hour | Signals | Win rate | UTC hour | Signals | Win rate |
|---|---|---|---|---|---|
| 0 | 9 | 77.8% | 12 | 16 | **0.0%** |
| 1 | 3 | 33.3% | 13 | 25 | 24.0% |
| 2 | 8 | 50.0% | 14 | 17 | 23.5% |
| 3 | 5 | 80.0% | 15 | 25 | 25.0% |
| 4 | 11 | 27.3% | 16 | 7 | 33.3% |
| 5 | 19 | 57.9% | 17 | 7 | **0.0%** |
| 6 | 12 | 33.3% | 18 | 5 | **0.0%** |
| 7 | 20 | 52.6% | 19 | 11 | 60.0% |
| 8 | 13 | 23.1% | 20 | 11 | 54.5% |
| 9 | 26 | 44.0% | 21 | 10 | **0.0%** |
| 10 | 12 | 25.0% | 22 | 15 | 13.3% |
| 11 | 21 | 33.3% | 23 | 3 | 66.7% |

Two contiguous blocks emerge — **08:00-18:59 UTC** (London open through the NY session) consistently underperforms **19:00-07:59 UTC** (US afternoon/evening through early European morning):

| Window | Block 08-18 UTC | Block 19-07 UTC |
|---|---|---|
| Window 1 | 24.5% (n=163 decided) | 44.4% (n=135 decided) ✅ |
| Window 2 | 33.3% (n=147 decided) | 39.7% (n=121 decided) |
| **Pooled** | **~28%** | **42.2% (n=256 decided)** |

## Results: Single-timeframe (target=5/stop=5, breakeven 50%)

Same two blocks, pooled across both windows:

| Block | Win rate | Decided trades |
|---|---|---|
| 08-18 UTC | 42.5% | 5,908 |
| 19-07 UTC | 42.4% | 7,482 |

**No effect.** The two windows even disagreed on which block was better individually (window 1: bad block higher; window 2: good block higher) — pooling shows the difference is noise, not signal. Single-timeframe's unfiltered baseline was already 43.4% at this ratio; the filter changes nothing for it, for better or worse.

## Findings

1. **MTF has a real, reproducible session effect; single-timeframe does not.** The direction (08-18 UTC worse) held across two independent windows for MTF, while single-timeframe showed no consistent pattern at all. Likely explanation: MTF's bias/confirm/ADX gating gets whipsawed by the faster, choppier price action during the most heavily-traded hours, in a way the simpler single-timeframe vote doesn't experience as acutely.
2. **Effect magnitude varies a lot between windows** (20-point gap in window 1, 6.4-point gap in window 2) — window 1's dramatic split likely reflects something specific to that period (a particularly bad stretch of midday chop) layered on top of a smaller, more durable underlying effect.
3. **Combined with the validated ratio and realistic spread, MTF+session-filter reaches the breakeven line: 42.2% pooled vs. 41.7% required** — the single best result across this entire investigation, edging out single-timeframe's unfiltered 43.4% vs. its own 50% breakeven (-6.6 points).
4. **Individual hours are not stable enough to trust on their own** (e.g. hour 19 was MTF's second-best hour in window 1, 60.0%, and its single worst in window 2, 4.3% on n=23) — only the broad block-level pattern is being relied on here, not any specific hour.
5. **Implementation verified exact**: the production-wired `--session-filter` flag reproduced the scratch-analysis numbers to the win/loss count, on both windows (window 1: 60W/75L/44.4%; window 2: 48W/73L/39.7%) — confirming `SessionFilteredSignalStrategy`/`get_broker_utc_offset_hours` behave identically to the original one-off analysis.

## Honest caveats

- **42.2% vs. 41.7% is a thin margin on a modest pooled sample** (256 decided trades across two windows) — "no longer clearly losing," not "confirmed profitable."
- **Spread is still a realistic assumption (1 pip), not this account's own actual historical spread** (which is unrealistically near-zero) or a live/real-broker-verified number.
- Applied only to MTF's own best ratio (7:5) — not re-verified across the other ratios tested earlier ([ratio-sweet-spot-search.md](ratio-sweet-spot-search.md)) in combination with this filter.
