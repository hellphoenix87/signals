# Wait-for-Zero-Spread Entry (Test O)

Date: 2026-09-25. Pre-registered in [pre-registrations-2026-09-22.md](pre-registrations-2026-09-22.md)
(Test O) before any of the 12 windows was run; stages run sequentially (4 -> 2 -> 4 -> 2), stage
order fixed in advance.

**TL;DR**: Holding each signal up to **15 s for a zero-spread tick** (else skipping) beats the
0.1-pip gate in **12 of 12** unseen windows (Jul 2023 - Jun 2024). Pooled: 0.1-pip gate 16,645 trades,
-$2,411 (**-$0.145/trade**); wait 11,408 trades, **-$573 (-$0.050/trade)**. The old 1.0-pip gate would
have lost -$21,889 (-$0.520/trade). The wait eliminates the 30-tick pre-BE timeout entirely (0.0% in
every window). Three windows came out slightly positive (W36 +$19, W39 +$13, W40 +$9); **pooled it is
still negative**. The remaining loss is the post-BE $1 cap vs the staircase trail.

## Mechanism

A zero-spread entry is born at breakeven (`is_break_even` = `profit >= 0`), so it cannot hit the
30-tick pre-BE timeout -- ~90% of net loss under the old gate. `SpreadWaitEntryStrategy` turns a
buy/sell into a pending signal at the candle close; the orchestrator feeds it every tick with the real
spread (ask - bid in points); it confirms on the first tick with spread <= 0.5 pt within 15 s, else
expires. Mean wait was 0.6-6.7 s.

## Setup

- Production code: `scripts/backtest_exit_strategy.py --full-lifecycle --single-timeframe
  --full-lifecycle-max-ticks 10000`, live config (STF, staircase, arm=30, $1 cap, $5 pre-BE SL,
  DST-correct session filter ON). Two runs per window:
  - Run A `--spread-wait off --max-entry-spread-pips 1.0` -> **OLD** (all entries) and **TIGHT**
    (subset with entry spread <= 1 pt = the 0.1-pip gate; trades are independent).
  - Run B `--spread-wait on --max-entry-spread-pips 0.15` -> **WAIT**: the backtest feeds real ticks
    through the production wrapper and enters on the exact confirming tick.
- Graded: WAIT vs TIGHT. Window win = better $/trade; < 100 trades in either arm -> graded on total.

## Results

| Stage | Window | Entries <= 1 pt | OLD $/tr | TIGHT n | TIGHT $/tr | TIGHT total | WAIT n | WAIT $/tr | WAIT total | Graded on |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | W31 (May 24) | 57% | -0.394 | 2,111 | -0.152 | -$321 | 2,079 | **-0.130** | -$270 | per-trade |
| 1 | W34 (Feb 24) | 5% | -0.802 | 174 | -0.497 | -$86 | 16 | -1.213 | **-$19** | total |
| 1 | W37 (Nov 23) | 50% | -0.469 | 1,796 | -0.044 | -$80 | 1,721 | **-0.019** | -$33 | per-trade |
| 1 | W40 (Sep 23) | 69% | -0.412 | 2,580 | -0.116 | -$298 | 1,729 | **+0.005** | +$9 | per-trade |
| 2 | W32 (Apr 24) | 17% | -0.539 | 574 | -0.284 | -$163 | 594 | **-0.161** | -$96 | per-trade |
| 2 | W38 (Oct 23) | 2% | -0.763 | 86 | -0.372 | -$32 | 31 | -0.174 | **-$5** | total |
| 3 | W33 (Mar 24) | 15% | -0.535 | 509 | -0.267 | -$136 | 516 | **-0.220** | -$113 | per-trade |
| 3 | W35 (Jan 24) | 21% | -0.639 | 780 | -0.354 | -$276 | 184 | **-0.216** | -$40 | per-trade |
| 3 | W39 (Sep 23) | 47% | -0.445 | 1,523 | -0.069 | -$106 | 943 | **+0.014** | +$13 | per-trade |
| 3 | W41 (Aug 23) | 69% | -0.419 | 2,376 | -0.131 | -$312 | 1,659 | **-0.004** | -$7 | per-trade |
| 4 | W36 (Dec 23) | 47% | -0.472 | 1,547 | -0.238 | -$368 | 176 | **+0.110** | +$19 | per-trade |
| 4 | W42 (Jul 23) | 73% | -0.350 | 2,589 | -0.090 | -$233 | 1,760 | **-0.018** | -$31 | per-trade |
| | **Pooled** | | **-0.520** | **16,645** | **-0.145** | **-$2,411** | **11,408** | **-0.050** | **-$573** | |

Stage verdicts (cumulative; each also required pooled $/trade better and pooled total no worse):
Stage 1 4/4 (>= 3); Stage 2 6/6 (>= 5); Stage 3 10/10 (>= 8); Stage 4 **12/12 (>= 9) -- PASS**.

Timeouts: OLD 18-48%, TIGHT 3-24%, **WAIT 0.0% in all 12 windows**.

## What it means

- **Unlike Test N, this is not mostly abstention.** 10 of 12 windows were graded per trade, and WAIT
  won all of them. In W32 it made *more* trades than the gate (594 vs 574) and still lost less.
- **It roughly breaks even in tight-spread months**: W36 +0.110, W39 +0.014, W40 +0.005, W41 -0.004,
  W42 -0.018 $/trade. The live feed today is in that regime (median spread 0 points).
- **Still no edge.** Pooled -$0.050/trade. With the timeout gone, every trade's fate is decided by the
  post-BE $1 cap vs the staircase trail; that balance is now the whole remaining result.
- **Trade count**: WAIT keeps 69% of TIGHT's trades pooled. It trades far less than the gate where
  1-point spreads are common but zero-spread ticks are rare (W35: 184 vs 780; W36: 176 vs 1,547).

## Caveats

- **Demo-account spreads.** On a real account, zero-spread ticks may be rare and WAIT would mostly
  expire.
- **Live path not yet exercised end-to-end.** The orchestrator changes (real spread + tick time into
  `on_new_tick`, confirmed signal executed on a tick) are verified in the backtest and a REPL only.
  A demo run of the app should show `waiting_for_spread` -> `spread_wait_confirmed` before merging.
- **Backtest tick time is millisecond-precise; live passes whole seconds** (`tick.time`). Only a tick
  within 1 s of the 15 s deadline can differ.
- The printed "expired/vetoed" count includes every signal from session-blocked hours (the wrapper
  sits inside the session filter); it is not the expiry rate.
- Unseen windows remaining: W25-W30 and anything before 2023-07.

## Artifacts

Per-trade CSVs `backtest_results/EURUSD_full_lifecycle_20260925_<suffix>.csv` (Run A / Run B):
W31 194040/195928, W34 201843/203606, W37 194130/200020, W40 201926/203719, W32 205703/211202,
W38 205733/211207, W33 213031/214554, W35 213222/214929, W39 220335/221853, W41 220708/222203,
W36 223828/225229, W42 224116/225629.
