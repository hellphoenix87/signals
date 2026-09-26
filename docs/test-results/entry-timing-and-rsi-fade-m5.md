# Entry Timing, Signal Quality, and the M5 RSI-Fade System -- Findings (Abandoned)

Date: 2026-09-26. **Status: abandoned by decision.** The code (RSI-fade strategy, fixed-pip exits,
one-position gate, backtest mode, signal lab) was built on branch `entry-timing` and **not merged**;
it remains readable in closed PR #79. This document is the record of what was found.

**TL;DR**

- **Entry timing is not a lever at M1.** At the live zero-spread entry, none of 22 entry-state
  features predicts whether a trade follows through (AUC 0.49-0.51).
- **At M1 every simple signal's edge (~0.02-0.03 pip) is 3-4x smaller than fill slippage (~0.09 pip).**
  No target/stop shape between 0.5 and 1 pip changes that.
- **Mean-reversion edge grows with horizon faster than friction.** The best candidate, RSI(14) fade on
  M5 with fixed +/-5 pip exits, **passed a pre-registered production test** on all 24 unseen windows
  (Mar 2021 - Jan 2023): +$0.745/trade, t = 2.25.
- **Abandoned anyway: too slow and too fragile.** ~10 trades a week, profit factor 1.16, +0.075R per
  trade, max drawdown -$429 (~43% of the $1,000 sizing basis) for ~$380 a year, and an edge of ~0.37
  pip per trade that a tenth of a pip of extra slippage would cut by a quarter.

## 1. Entry timing at M1 (live config, spent windows)

18,744 live-config trades across 14 windows; each trade's actual entry tick reconstructed (entry price
match 99.5-100%).

- **Features at entry**: price path during the spread wait / last 5 ticks / 60 s / 5 min; signal
  candle range, body and close position; prior 3 candles; tick activity and flat-tick share; ATR; 1h
  range, position and trend; MACD value; signal freshness and run length; wait time; hour. All AUC
  0.49-0.51 for reaching the staircase. Combined model on held-out windows: AUC 0.512.
- The earlier "don't chase" / "quiet market" effects (AUC 0.53-0.56) were about winning back the
  spread; they disappear once the entry is at zero spread.
- **The live exit is a barrier game.** At 0.2 lots, the staircase starts at +1 pip and the cap sits at
  -0.5 pip. At every real entry tick:

| Direction | +1 pip before -0.5 pip |
|---|---|
| Signal | 33.2% |
| Opposite side | 30.6% |
| Random walk | 33.3% |
| Break-even with live fills | 34.8% |

- **Inverted signal** (production backtest, live config, 4 windows): signal -$0.023/trade, inverted
  -$0.153/trade. The direction is real (4/4 windows) but only lifts the entry to random-walk level.

## 2. Signal lab screens (spent windows)

A barrier-game screener: for every live-hour candle close, the live entry tick (15 s zero-spread wait)
and the outcome for both directions, so any rule is scored against the same-tick random direction.
Screens only (95.8% agreement with the full backtest on live trades).

- **Rules at the live geometry** (+1/-0.5 pip, 14 windows, bar +3 pp over random): none passed. Every
  mean-reversion rule was positive (Bollinger fade +1.80 pp, MACD against the 4h trend +1.69, RSI fade
  +1.60, live MACD +1.32); every momentum/trend rule negative (-0.4 to -1.8 pp).
- **Exit geometry** (+/-0.5 to 1 pip, 4 signals, net of measured slippage): all 20 combinations net
  negative. A random direction misses random-walk break-even in every geometry by 1.3-2.6 pp: a fixed
  entry handicap (the spread reopens after a zero-spread entry), not reversion favouring one shape.
- **Scale** (+/-1, 2, 3, 5 pips; M1 and M5 signals): random-direction friction grows with scale
  (-0.10 -> -0.25 pip net) but reversion edge grows faster. RSI fade on M5: +4.7 -> +7.3 pp from
  +/-1 to +/-5. First entry per episode at +/-5: +7.9 pp, +0.51 pip net, 8/12 windows (bar was 75%).

## 3. Pre-registered tests (bars were fixed before each run)

| Test | Rule / windows | Pre-registered bar | Result | Verdict |
|---|---|---|---|---|
| P1 | Lab screen, RSI fade M5 +/-5, W25-W30 (Jun-Nov 2024) | pooled net > 0, edge > 0, >= 2/3 of windows with >= 20 trades positive, >= 3 such windows | 51 trades in 1 window (zero-spread entry never fired in a wide-spread period) | INCONCLUSIVE |
| P1b | Same, W43-W48 (Jan-Jul 2023) | same | 281 trades, 53.4% win, +4.45 pp, +0.275 pip/trade, 4/6 windows | PASS (weak, ~1.5 SE) |
| P2 | Production system, all 24 unseen windows W49-W72 (Mar 2021 - Jan 2023), stages 8/4/8/4 | early stop if pooled $/trade < 0; final: $/trade > 0, t >= 2, total > live config | see below | **PASS** |

**Test P2**, production code (live session filter, 15 s zero-spread wait, 0.15-pip gate; M5 entries,
fixed +/-5 pips, one position at a time):

| Pooled, 24 windows | Trades | Win % | $/trade | Total | t | Max drawdown |
|---|---|---|---|---|---|---|
| RSI-fade M5 | 945 | 53.9 | +0.745 | +$704.20 | +2.25 | -$429 |
| Live config | 45,051 | 34.9 | +0.013 | +$601.00 | +1.54 | -$806 |

- Stage path: +0.283 -> +0.563 -> +0.591 -> +0.745 $/trade; early stop never triggered.
- RSI: 17 of 23 trading windows positive (live config 13); window-total sd $69 (live $127); longest
  losing streak 7 trades (live 41); 2021 +$27 / 2022 +$677 (live -$177 / +$778). Correlation of window
  totals between the two: 0.42.
- **The live config was also profitable in 2021-2022**, although it loses ~-$0.044/trade on 2023-2026
  data. So the RSI case was risk, not return.

## 4. Why it was abandoned

| | |
|---|---|
| Trades | ~10 a week (945 in 96 weeks) |
| Net | +$704 in 96 weeks: ~$7/week, ~$380/year at 0.2 lots |
| Per trade | +$0.745 = +0.075R (risk ~$10 per trade) |
| Profit factor | 1.16 (avg win +$10.17, avg loss -$10.25) |
| Max drawdown | -$429, ~43% of the $1,000 sizing basis |
| Months | 13 of 22 positive; worst -$123, best +$199 |

- **Too slow**: at ~10 trades a week, telling a real edge from luck forward would take many months.
- **Too fragile**: the edge is ~0.37 pip on a 5-pip stop; +0.1 pip of real-world slippage costs ~$0.20
  per trade, a quarter of it. The strong period (2022) was friendly to every system tested.

## 5. What this leaves for future work

- **At M1, entry selection and timing are exhausted** with simple rules and the current exits: the
  outcome is set by friction, not by the signal. Do not revisit entry-state filters, target/stop
  reshaping at the 0.5-1 pip scale, or simple indicator swaps at M1.
- **Mean reversion, not momentum,** is where EURUSD's short-horizon directional edge was found.
- **Data**: all tick data from 2021-03 onward has now been used for entry-signal hypotheses; any new
  hypothesis in this family needs forward (demo/live) evidence, or data from before 2021-03 if the
  broker's history reaches that far.
- **Method worth keeping**: screen ideas with a same-tick barrier game against a random direction
  before any full backtest; grade underpowered systems on pooled trade-level results, not window
  counts. The screener lives in PR #79 (`scripts/signal_lab.py`).
