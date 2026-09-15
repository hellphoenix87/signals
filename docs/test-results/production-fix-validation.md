# Production Fix Validation: MACD Crossover + RSI-Weighted Vote

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history, same window methodology as all prior reports in this category.
- **Change under test**: two of the "still open" follow-ups from earlier reports, now adopted in production (not experimental/backtest-only):
  1. **`calculate_macd`** (`app/signals/indicators/macd.py`) now fires on a true crossover (histogram sign flip) instead of "still accelerating in the same direction" — see [macd-crossover-fix.md](macd-crossover-fix.md) for the original backtest-only comparison that motivated this. Affects both the single-timeframe vote and MTF's MACD-only entry layer.
  2. **Weighted voting**: `StrongSignalStrategy` now supports per-indicator weights; the single-timeframe vote weights RSI at 2.0 vs. MACD/SMA at 1.0 each (`Config.ENTRY_RSI_WEIGHT`/`ENTRY_MACD_WEIGHT`/`ENTRY_SMA_WEIGHT`), motivated by [single-indicator-ablation.md](single-indicator-ablation.md) finding RSI alone outperforms the equal-weight combined vote. No-op for MTF's entry layer (MACD only, nothing to weigh against).
- **Also new this run**: a tighter 7:5 target/stop ratio (1.4:1, breakeven 41.7%) — the previous tightest tested was 10:5 (2:1, breakeven 33.3%), from [target-stop-realistic-resweep.md](target-stop-realistic-resweep.md).
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --target-pips <15|10|7> --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --target-pips <15|10|7> --stop-pips 5
  ```
- **Spread**: still not modeled by the backtest (same caveat as every report in this category) — see note in Findings below on why this matters more here than in prior reports.

## Results

| Strategy | Target:Stop | R:R | Breakeven | Pre-fix win rate | Post-fix win rate | Gap to breakeven |
|---|---|---|---|---|---|---|
| Single-tf | 15:5 | 3:1 | 25.0% | 16.2% | 17.8% | 7.2 pts |
| Single-tf | 10:5 | 2:1 | 33.3% | 26.6% | 30.2% | 3.1 pts |
| **Single-tf** | **7:5** | **1.4:1** | **41.7%** | — (not tested) | **43.8%** | **+2.1 pts (above breakeven)** |
| MTF | 15:5 | 3:1 | 25.0% | 17.7% | 18.5% | 6.5 pts |
| MTF | 10:5 | 2:1 | 33.3% | 26.5% | 29.5% | 3.8 pts |
| MTF | 7:5 | 1.4:1 | 41.7% | — (not tested) | 39.2% | 2.5 pts |

Signal counts (post-fix): single-tf ~7,056-7,062 across all three ratios; MTF 327 (down from 855 pre-fix, since MTF's entry layer now also uses the less-frequent crossover MACD).

## Findings

1. **Both fixes together improved every ratio tested for both strategies** — single-tf gained 1.6-3.6 points, MTF gained 0.8-3.0 points, at every target/stop combination re-run from the earlier resweep.
2. **Single-timeframe at 7:5 crosses breakeven: 43.8% vs. 41.7% required** — the first result across this entire investigation (11 prior reports) to clear breakeven on raw win rate. MTF at the same ratio (39.2%) comes close but doesn't quite clear it.
3. **This flips the earlier MTF-vs-single-timeframe narrative.** At wide targets, MTF's extra selectivity clearly beat single-timeframe. At this tightest, most realistic ratio, single-timeframe (7,056 signals) now slightly *outperforms* MTF (327 signals) — once the entry indicator itself is fixed and well-weighted, the extra multi-timeframe filtering doesn't add further value at this scale, and may even discard some of the signals that would have paid off.
4. **Important caveat before reading this as "found a profitable strategy": spread still isn't modeled.** This demo account's historical spread is unrealistically tight (median 0, mean 0.016 pips — see [quick-check-1-minute-exit-analysis.md](quick-check-1-minute-exit-analysis.md)), but a real broker's typical EURUSD spread (0.5-1+ pips round trip) would eat directly into a 2.1-point margin on a 7-pip target — this could easily erase the edge on a live/real account. The margin above breakeven is thin enough that transaction costs are not a footnote here, they're potentially decisive.
5. **Single sample, single window**: this is one 4-week EURUSD window. Before treating 43.8% as a real, durable edge (rather than this window's specific price action), it should be validated on a different and/or longer historical window.
6. **Recommended next steps, not done here**: (a) re-test the 7:5 (and maybe even tighter, e.g. 5:5 or a small negative-skew ratio) on a different time period to check robustness; (b) fold a realistic spread assumption (e.g. 1 pip round trip) into `evaluate_signal` to see whether the breakeven-crossing survives real transaction costs; (c) only then consider this a candidate for enabling live/demo forward-testing.
