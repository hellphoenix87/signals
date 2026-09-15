# Realistic Target/Stop Re-Sweep

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history (same window as prior reports in this category)
- **Motivation**: every earlier target/stop result used `Config.DEFAULT_TP_PIPS = 50`, a 2.5:1-10:1 risk/reward depending on stop size, which almost never resolved as a win within 300 bars (see [multi-timeframe-target-stop-sweep.md](multi-timeframe-target-stop-sweep.md) finding #4: avg bars-to-resolution reaching 151, ~2.5 hours). This re-sweep tests both strategies at target/stop ratios closer to what M1 EURUSD can plausibly deliver.
- **Strategies under test**: single-timeframe (MACD+SMA+RSI, current default) and multi-timeframe (SMA/M15, RSI/M5, MACD/M1), both unchanged from prior reports.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --target-pips 15 --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --target-pips 10 --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --target-pips 15 --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --target-pips 10 --stop-pips 5
  ```

## Results

| Strategy | Target:Stop | R:R | Breakeven win rate | Signals | Win rate | Gap to breakeven |
|---|---|---|---|---|---|---|
| Single-tf | 50:20 | 2.5:1 | 28.6% | 846 (MTF)/16,082 (single-tf) | 7.1%/16.5% | ~13-22 pts |
| Single-tf | 15:5 | 3:1 | 25.0% | 5,657 | 16.2% | 8.8 pts |
| Single-tf | 10:5 | 2:1 | 33.3% | 5,662 | 26.6% | 6.7 pts |
| MTF | 15:5 | 3:1 | 25.0% | 855 | 17.7% | 7.3 pts |
| MTF | 10:5 | 2:1 | 33.3% | 855 | 26.5% | 6.8 pts |

(50:20 row summarizes the earlier sweep from [multi-timeframe-target-stop-sweep.md](multi-timeframe-target-stop-sweep.md) for reference.)

## Findings

1. **Win rate climbs sharply as the target/stop ratio tightens toward something the market can realistically deliver**: single-timeframe goes from 7.1% (at 50:20) to 16.2% (15:5) to 26.6% (10:5). The earlier 50-pip target wasn't just aggressive, it was actively hiding whatever signal quality exists by asking for a move size M1 EURUSD rarely makes within a useful timeframe.
2. **Gap to breakeven narrows substantially**: from ~13-22 points short at 50:20 down to ~7 points short at both 15:5 and 10:5. Still not profitable at any ratio tested, but meaningfully closer.
3. **Single-timeframe and MTF converge at tighter ratios** (26.6% vs 26.5% at 10:5; 16.2% vs 17.7% at 15:5) — a much smaller gap than at 50:20, where MTF clearly led (16.5% vs 7.1%). MTF's extra selectivity mattered most when most signals were "reaching" for an unrealistic target; at a target the market can actually deliver, the two converge.
4. **Natural next step, not tested here**: push the ratio even tighter (e.g. target=7/stop=5, near 1:1) to see whether win rate keeps climbing toward or past breakeven, or plateaus around this ~26-27% level regardless of ratio.
