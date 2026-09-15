# Ratio Sweet-Spot Search (Spread-Adjusted)

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history (window 1, the same most-recent window used throughout this category)
- **Motivation**: [spread-and-second-window-validation.md](spread-and-second-window-validation.md) found a real, reproducible edge at 7:5 target/stop that doesn't survive a realistic 1-pip spread (drops ~9-11 points below breakeven). Since win rate rose sharply as target tightened from 15→10→7 pips ([production-fix-validation.md](production-fix-validation.md)), this searches nearby ratios to see whether any point minimizes the win-rate-vs-breakeven gap enough to close it, rather than assuming 7:5 is already optimal.
- **Fixed**: stop=5 pips throughout (matches `Config.MIN_SL_PIPS`), spread=1 pip (realistic round-trip cost, per [spread-and-second-window-validation.md](spread-and-second-window-validation.md)).
- **Swept**: target in {3, 4, 5, 6, 7, 8, 9, 10} pips (7 and 10 reused from prior reports; 3/4/5/6/8/9 new this run).
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --target-pips <3|4|5|6|8|9> --stop-pips 5 --spread-pips 1
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --target-pips <5|6|8|9> --stop-pips 5 --spread-pips 1
  ```

## Results

Gap = win rate minus that ratio's breakeven (`stop/(stop+target)`). Negative = below breakeven.

| Target:Stop | Breakeven | Single-tf win rate | Single-tf gap | MTF win rate | MTF gap |
|---|---|---|---|---|---|
| 3:5 | 62.5% | 55.4% | **-7.1** | — (not tested, see Findings) | — |
| 4:5 | 55.6% | 48.6% | **-7.0** | — (not tested) | — |
| 5:5 | 50.0% | 42.9% | **-7.1** | 38.7% | -11.3 |
| 6:5 | 45.5% | 37.4% | -8.1 | 34.8% | -10.7 |
| 7:5 | 41.7% | 32.7% | -9.0 | 33.7% | **-8.0** |
| 8:5 | 38.5% | 28.2% | -10.3 | 29.7% | -8.8 |
| 9:5 | 35.7% | 24.5% | -11.2 | 26.1% | -9.6 |
| 10:5 | 33.3% | 22.2% | -11.1 | 22.6% | -10.7 |

## Findings

1. **No ratio tested closes the gap.** The best (smallest) gap found is about **-7 points**, for both strategies — real, but not zero.
2. **Single-timeframe's gap plateaus, it doesn't keep improving.** From 10:5 down to 5:5, the gap steadily shrinks (-11.1 → -7.1) as expected from the earlier trend, but pushing tighter still (4:5, 3:5) doesn't improve on that — the gap holds flat around -7.0 to -7.1 across the entire 3:5-5:5 range rather than continuing to close. This looks like a genuine floor for this signal engine + 1-pip-spread combination, not a point that further tightening would break through.
3. **MTF's optimum is a single interior point, not a plateau**: its best gap (-8.0) is specifically at 7:5, with both tighter (5:5: -11.3) and looser (10:5: -10.7) ratios doing worse. Unlike single-timeframe, MTF doesn't benefit from pushing toward 1:1 — its own bias/confirm/ADX gating apparently interacts with ratio differently than the simpler single-timeframe vote does. (MTF wasn't extended to 3:5/4:5 given this non-monotonic shape already pointed away from that direction.)
4. **Single-timeframe at its plateau (~-7 points) is now the best result found across the entire investigation** — slightly better than 7:5's -9.0 (the previous best-known point) and meaningfully better than MTF's best (-8.0).
5. **Conclusion: further target/stop tuning has plateaued as a lever.** The -7 point floor appears structural to this combination of signal engine and spread cost, not an artifact of an untried ratio. Closing it further would need a different lever — reducing effective spread (a tighter-spread broker/account), improving underlying signal quality (the RSI-weighting/MACD-crossover fixes already applied, further untested ideas like RSI weight tuning), or accepting that this signal engine's edge, while real, may not be large enough to trade profitably on this symbol/timeframe with realistic costs.
