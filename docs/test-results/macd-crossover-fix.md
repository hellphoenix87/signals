# MACD Crossover Fix — Re-Test

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history
- **Motivation**: [single-timeframe-macd-only-baseline.md](single-timeframe-macd-only-baseline.md) identified `calculate_macd`'s trigger (`app/signals/indicators/macd.py`) as an "accelerating histogram" condition (`hist_last > 0 and hist_last > hist_prev`) rather than a true crossover — it re-fires on nearly every candle throughout a trending stretch, not just at the reversal that starts it. This tests a fix directly.
- **Change under test**: `calculate_macd_crossover` (new, added this session, `app/signals/indicators/macd.py`) — same EMA/histogram math, but triggers only on the histogram flipping sign (`hist_last > 0 and hist_prev <= 0`, equivalent to the MACD line crossing the signal line). Experimental/backtest-only — `strategy_factory`/live trading still uses the original `calculate_macd`.
- **Target/stop**: 15/5 pips (3:1 R:R, breakeven 25%), matching the other reports in this batch.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --indicators macd_crossover --target-pips 15 --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --indicators macd_crossover,sma,rsi --target-pips 15 --stop-pips 5
  ```

## Results

| Variant | Signals | Win rate |
|---|---|---|
| MACD (original, accelerating-histogram) | 16,070 | 15.6% |
| **MACD (crossover fix)** | **4,075** | **16.2%** |
| MACD (original) + SMA + RSI (current live combo) | 5,657 | 16.2% |
| **MACD (crossover fix) + SMA + RSI** | **906** | **16.5%** |

## Findings

1. **The crossover fix cuts raw MACD signal count by 75%** (16,070 -> 4,075) while modestly improving win rate (15.6% -> 16.2%) — a much healthier signal-to-noise ratio for essentially the same result quality. Confirms the original trigger condition was firing far more often than the underlying edge (such as it is) justified.
2. **Swapping it into the 3-indicator vote further reduces signal count** (5,657 -> 906) with another small win-rate gain (16.2% -> 16.5%) — the fix compounds with the existing multi-indicator gating rather than being redundant with it.
3. **Still below the 25% breakeven** at every variant tested — this is a real, if modest, quality improvement (less noise for the same or slightly better hit rate), not a fix that makes the strategy profitable on its own.
4. **Recommendation**: worth adopting `calculate_macd_crossover`'s trigger logic as the production `calculate_macd` behavior regardless of the profitability question — a strategy that fires 75% less often for equal-or-better quality is a strict improvement for live trading (less noise, fewer unnecessary trades/spread costs), independent of whether overall profitability is solved yet.
