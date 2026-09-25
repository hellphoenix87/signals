# Tight Entry Spread Gate (Test N)

Date: 2026-09-25. Pre-registered in [pre-registrations-2026-09-22.md](pre-registrations-2026-09-22.md)
(Test N) before any of the 12 windows was run. Stages run sequentially (4 -> 2 -> 4 -> 2 windows),
stage order fixed in advance.

**TL;DR**: Gating entries at **1 point (0.1 pip)** instead of the shipped 1.0 pip wins **12 of 12**
unseen windows. Pooled: SHIPPED 21,123 trades, -$16,893 (-$0.800/trade) vs TIGHT 1,120 trades,
**-$496** (-$0.443/trade). **Most of that is abstention**: across Nov 2024 - Oct 2025 the broker's
spread sat at 0.2-1.3 pip for months at a time, and the tight gate simply did not trade them. In the 3
windows where it did trade it was better per trade every time, but **still negative in all three**.
It is a loss limiter, not an edge.

## Why this was tested

In the 10 Test M windows, the pre-breakeven phase was ~90% of net loss, almost all of it the 30-tick
`failed_to_reach_be` timeout. Entry spread was the dominant predictor of that timeout (AUC 0.75;
0.0 pip -> 0% timeouts, 0.2 -> 29%, >=0.5 -> 52%+); RSI/MACD carried none (AUC 0.50). It is largely
mechanical: a zero-spread entry is born at breakeven; a 0.3-pip entry must first win back $0.60.
Even zero-spread entries averaged -$0.064/trade -- the signal is roughly break-even *before* costs.

## Setup

- Production code: `scripts/backtest_exit_strategy.py --full-lifecycle --single-timeframe
  --full-lifecycle-max-ticks 10000 --max-entry-spread-pips 1.0`, session filter toggled off, live
  exit config (staircase on, arm=30, $1 cap, $5 pre-BE SL). The backtest now logs
  `entry_spread_pips` per trade.
- Arms, both under the live session filter (DST-correct, blocks 08-18 UTC):
  **SHIPPED** = entry spread <= 1.0 pip; **TIGHT** = entry spread <= 1 point (0.1 pip). Read from one
  run per window (trades are simulated independently, so a subset equals a gated run).
- Window win = better $/trade; if TIGHT made < 100 trades it "abstained" and is graded on total.

## Results

| Stage | Window | Median spread* | SHIPPED trades | SHIPPED $/tr | SHIPPED total | TIGHT trades (kept) | TIGHT $/tr | TIGHT total | Graded on |
|---|---|---|---|---|---|---|---|---|---|
| 1 | W13 (Sep-Oct 25) | 0.1 | 2,694 | -0.505 | -$1,362 | 427 (16%) | **-0.152** | -$65 | per-trade |
| 1 | W16 (Jul 25) | 1.3 | 44 | -2.132 | -$94 | 1 | -- | -$1 | total |
| 1 | W19 (Apr-May 25) | 1.3 | 95 | -2.787 | -$265 | 1 | -- | -$23 | total |
| 1 | W22 (Jan-Feb 25) | 0.4 | 3,797 | -0.935 | **-$3,551** | 10 | -- | -$2 | total |
| 2 | W14 (Aug-Sep 25) | -- | 1,670 | -0.575 | -$960 | 299 (18%) | -0.567 | -$170 | per-trade |
| 2 | W20 (Mar-Apr 25) | -- | 1,497 | -0.697 | -$1,043 | 45 | -- | +$3 | total |
| 3 | W15 (Jul-Aug 25) | -- | 457 | -0.739 | -$338 | 63 | -- | -$9 | total |
| 3 | W17 (Jun 25) | -- | 55 | -2.396 | -$132 | 1 | -- | -$1 | total |
| 3 | W21 (Feb-Mar 25) | -- | 3,488 | -0.724 | -$2,527 | 55 | -- | -$20 | total |
| 3 | W23 (Dec 24-Jan 25) | -- | 3,350 | -0.877 | -$2,938 | 14 | -- | -$12 | total |
| 4 | W18 (May 25) | -- | 255 | -1.422 | -$363 | 198 (78%) | **-0.953** | -$189 | per-trade |
| 4 | W24 (Nov-Dec 24) | -- | 3,721 | -0.893 | -$3,321 | 6 | -- | -$7 | total |
| | **Pooled (12)** | | **21,123** | **-0.800** | **-$16,893** | **1,120** | **-0.443** | **-$496** | |

\* Sampled from one mid-window trading day's ticks; stage 1 only.

Stage verdicts (cumulative, pre-registered bars; each also required pooled per-trade better and
pooled total no worse): Stage 1 4/4 (>= 3) PASS; Stage 2 6/6 (>= 5) PASS; Stage 3 10/10 (>= 8) PASS;
Stage 4 12/12 (>= 9) **PASS**.

Secondary (not graded), same comparison under other session settings:

| Session | 1.0-pip gate | Tight gate |
|---|---|---|
| All hours | 40,945 tr, -$0.737/tr, -$30,160 | 7,139 tr, -$0.306/tr, -$2,182 |
| Inverted (trade 08-18 UTC, Test M) | 19,822 tr, -$0.669/tr, -$13,267 | 6,019 tr, -$0.280/tr, -$1,687 |

## What it means

**The broker's spread regime decides whether this bot bleeds.** Sampled median spreads: 1.3 pip
(Apr, Jul 2025), 0.4 pip (Jan 2025), 0.1 pip (Oct 2025), 0.0 pip (Sep 2026, live today). The
shipped 1.0-pip gate was sized to block rollover spikes; it lets the bot trade straight through a
0.4-pip month (W22: 3,797 trades, -$3,551; W24: -$3,321). The tight gate sits those months out.

**It does not create an edge.** In the three windows where TIGHT traded meaningfully it improved
per-trade every time (W13 -0.505 -> -0.152, W14 -0.575 -> -0.567 [a tie], W18 -1.422 -> -0.953) and
lost money every time. Combined with the exploratory -$0.064/trade at zero spread, the entry signal
has no measurable edge beyond cost; the best available lever is to not pay spread.

**Live impact today would be small.** The live feed is currently in the tight regime (last-3h
sample: 96% of ticks <= 0.1 pip), so today TIGHT keeps ~95% of entries. Its value is insurance: if
spreads widen again, the bot stops instead of bleeding.

## Caveats

- **Demo-account spreads.** On a real account with typical spreads >= 0.2 pip, TIGHT would rarely
  trade. That would itself be the answer: this strategy only survives at near-zero spread.
- **Per-trade evidence while trading rests on 3 windows** (W13, W14, W18) plus the exploratory set.
  The 12/12 is dominated by abstention wins, which is exactly what the gate is for, but they say
  nothing about trade quality.
- **Live form must avoid a float edge case**: `_spread_ok` compares `(ask - bid) / point <=
  MAX_SPREAD_POINTS` as floats, so a 1-point spread can compute as 1.0000000001 and be rejected at a
  threshold of exactly 1. Spreads are whole points, so ship `MAX_SPREAD_POINTS = 1.5`.
- Reserve windows W25-W30 (2024-06-04 .. 2024-10-22) remain unseen.

## Shipping (not done -- user decision)

`Config.MAX_SPREAD_POINTS = 1.5` (from 10). Config only.

## Artifacts

Per-trade CSVs (`backtest_results/`, run 2026-09-25): W13 `..._140630`, W16 `..._140619`,
W19 `..._142332`, W22 `..._142242`, W14 `..._144028`, W20 `..._144123`, W15 `..._145814`,
W17 `..._145849`, W21 `..._151414`, W23 `..._151153`, W18 `..._153043`, W24 `..._153130`
(`EURUSD_full_lifecycle_20260925_<suffix>.csv`).
