# Momentum with wider exits, and an M5 extrapolation -- smoke screen

Date: 2026-09-26. Plan: [`docs/plans/done/momentum-exit-grid.md`](../plans/done/momentum-exit-grid.md).
**Smoke screen only.** Everything here comes from the signal lab (a tick-level approximation) or from
candles, not from production code. It can rule ideas out; it cannot confirm them.

Verdict: **momentum entries do not become profitable when they are given room.** Wider fixed exits and
trailing exits do not make their edge grow, at M1 or at M5 (extrapolated). The one signal whose edge
does grow with wider exits is the production MACD in London/NY hours, but only to about break-even.
At M5, the edge is in fading (mean reversion) in the live hours, the same finding as the abandoned
RSI-fade system.

## 1. Exit grid on M1 entries (`scripts/signal_lab.py --exit-grid [--macd] --hours=ldn_ny|live`)

- Entries: the live entry (first zero-spread tick within 15 s of the M1 close), every candle a
  candidate fires on. Trades overlap.
- 23 exits: fixed target {1,2,3,5} x stop {0.5,1,2,3} pips, plus 7 trailing stops (initial stop S;
  once the peak reaches A, the stop follows at peak - D).
- Costs: 0.09 pip beyond the stop level on every stop or trail exit, and 0.04 pip short on targets.
- Each exit was tuned on W1, W2, W31-W35 and reported on W36-W42 (holdout).
- Check: the fixed +1/-0.5 exit agrees with the lab's barrier outcome on 100% of 196,866 entries.

Summary for London/NY hours (08-18 UTC). Net pips/trade on the holdout at the exit tuned on the other half:

| candidate | tuned exit | tune net | holdout net | holdout edge vs random | windows + |
|---|---|---|---|---|---|
| KAMA slope + vol exp | +2/-0.5 | -0.064 | -0.043 | +0.024 | 1/6 |
| Keltner + vol exp | +3/-0.5 | -0.059 | -0.043 | +0.013 | 0/6 |
| ROC + vol exp | +2/-0.5 | -0.082 | -0.045 | +0.014 | 1/6 |
| Hull + vol exp | +2/-0.5 | -0.068 | -0.065 | +0.003 | 1/6 |
| KAMA slope | +1/-0.5 | -0.096 | -0.057 | +0.004 | 0/6 |
| Keltner | trail 1/1/1 | -0.098 | -0.020 | +0.007 | 2/6 |
| **production MACD** | +3/-3 | **+0.031** | -0.004 | **+0.092** | 2/6 |
| random | +1/-3 | -0.086 | -0.123 | 0 | 0/6 |

- **The momentum candidates' edge does not grow with exit width.** For KAMA + vol exp it is +0.01..+0.03
  pip at 0.5-1 pip stops, and zero or negative at 2-3 pip stops, in both halves. A signal that catches
  moves which keep going would show the opposite.
- **The production MACD's edge grows with width in both halves:** +0.00 at +1/-0.5, +0.09..+0.14 pip at
  +3/-3, +5/-3 and trail 3/2/2. It is the only signal that is consistent across the tune and holdout
  windows. Net it is about -0.00..+0.07 pip per trade, because a random direction's cost also grows at
  wide exits (-0.09..-0.23 pip).
- Live hours (19-07 UTC): every candidate is negative on the tune windows at every exit, and wider is
  worse. On the holdout (2023), the vol-exp candidates turn positive at wide trails (+0.2 pip). The sign
  flips between periods, so this is regime noise, not an edge.

## 2. M5 extrapolation from candles (`scripts/m5_candle_extrapolation.py`)

- M1 candles from the lab caches, resampled to M5. The signals are the same indicators computed on M5
  bars. Entry at the M5 close, one position at a time. The target/stop race is played on the following
  M1 highs/lows; if both levels are hit inside one M1 bar, the trade counts as a loss.
- Gross = no costs. Net = gross - 0.20 pip. This cost is **assumed** (spread reopening plus stop
  slippage, from the tick runs); the true cost at M5 has not been measured.

London/NY hours, best rows (all 14 windows):

| signal | exit | trades/week | win % | gross pip/tr | edge vs random | net | windows net + |
|---|---|---|---|---|---|---|---|
| KAMA + vol exp | +/-5 | 97 | 50.8 | +0.084 | +0.102 | -0.116 | 4/14 |
| KAMA + vol exp | +8/-4 | 85 | 34.0 | +0.074 | +0.062 | -0.126 | 5/14 |
| ROC + vol exp | +8/-4 | 56 | 33.9 | +0.062 | +0.078 | -0.138 | 6/14 |
| KAMA / Hull / ROC / Keltner alone | any | 36-325 | -- | -0.10..+0.02 | -0.15..+0.02 | < -0.18 | <= 5/14 |

Live hours:

| signal | exit | trades/week | gross pip/tr | net | windows net + |
|---|---|---|---|---|---|
| KAMA slope | +/-5 | 73 | -0.347 | -0.547 | 0/14 |
| Keltner | +/-5 | 29 | -0.676 | -0.876 | 0/14 |
| Bollinger fade (contrast) | +/-5 | 36 | **+0.538** | **+0.338** | **11/14** |
| RSI fade (contrast) | +/-5 | 19 | **+0.684** | **+0.484** | **10/14** |

- At M5, momentum in the live hours is clearly negative, and wider exits make it worse. Fading is the
  mirror image: it is clearly positive (this matches the M5 RSI-fade system that passed Test P2 and
  was then abandoned).
- In London/NY hours, M5 momentum with vol expansion shows a small gross edge (+0.06..+0.10 pip). That
  is under half the assumed cost, and positive in only 4-6 of 14 windows.

## What this leaves

- Riding momentum with indicator entries does not work on EURUSD M1/M5 in this data. Wider exits do not
  unlock it; the edge does not grow with room.
- If entry work continues, the only leads with a consistent sign are: (a) the production MACD with
  +3/-3-style exits in London/NY hours (about break-even in the lab, so it needs a production run
  before anything else), and (b) fading at M5 in the live hours (already built and tested; abandoned
  for speed and fragility).
