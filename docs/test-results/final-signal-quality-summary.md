# Final Signal Quality Summary (Current Production Config)

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history (window 1), 1-pip realistic spread modeled throughout.
- **Purpose**: a single consolidated snapshot of both strategies' signal quality as the codebase now stands, after every fix and tuning pass in this investigation: crossover MACD trigger (both strategies' entry layer), `ENTRY_RSI_WEIGHT=2.5` (single-timeframe vote only — no-op for MTF's single-indicator entry layer). Two of the three single-timeframe numbers below (7:5, 10:5) are freshly re-run against the final `ENTRY_RSI_WEIGHT=2.5` config, since [rsi-weight-sweep.md](rsi-weight-sweep.md) only directly verified the weight change at 5:5; the MTF numbers are reused from earlier reports since RSI weight doesn't affect MTF's entry layer at all.
- **Commands** (the two newly re-run cells):
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --target-pips 7 --stop-pips 5 --spread-pips 1
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --target-pips 10 --stop-pips 5 --spread-pips 1
  ```

## Results

All cells: stop=5, 1-pip spread modeled.

| Ratio | Breakeven | Single-tf win rate | Single-tf gap | MTF win rate | MTF gap |
|---|---|---|---|---|---|
| **5:5** | 50.0% | **43.4%** | **-6.6** | 38.7% | -11.3 |
| 7:5 | 41.7% | 32.6% | -9.1 | 33.7% | **-8.0** |
| 10:5 | 33.3% | 22.3% | -11.0 | 22.6% | -10.7 |

(Re-running 7:5 and 10:5 confirmed `ENTRY_RSI_WEIGHT=2.5` vs. the earlier 2.0 makes essentially no difference at these ratios — 32.6% vs. 32.7%, 22.3% vs. 22.2% — the weight change's benefit is specific to the 5:5 ratio, where RSI-alone-triggered signals make up a larger share of the total.)

## Findings

1. **Single-timeframe at 5:5 (1:1 risk/reward) is the best signal-quality configuration found across the entire investigation**: -6.6 points from breakeven, its own local optimum (confirmed in [ratio-sweet-spot-search.md](ratio-sweet-spot-search.md) and [rsi-weight-sweep.md](rsi-weight-sweep.md)).
2. **The two strategies now have different optimal ratios**: single-timeframe peaks at 5:5, MTF peaks at 7:5 — there's no single ratio that's best for both, so any future config choice needs to pick a strategy first, then its own matched ratio, not a shared default.
3. **Neither strategy clears breakeven at its own best ratio.** This is the accumulated conclusion of the whole investigation: a small, real, reproducible directional edge exists (confirmed across two independent windows), survives neither the original 50-pip target nor a realistic spread at any tested ratio, and both of the tunable levers available in this codebase (target/stop ratio, RSI vote weight) are now exhausted without closing it.
4. **What would move the needle from here** (neither attempted in this investigation): a materially lower real-world spread than the 1-pip assumption used throughout (worth checking against whatever account this eventually trades on), or a genuine signal-quality improvement beyond parameter tuning — e.g. finally root-causing the unexplained negative passive-hold drift ([quick-check-multi-horizon.md](quick-check-multi-horizon.md)), or a different indicator/approach entirely.
5. **`USE_MULTI_TIMEFRAME_SIGNALS` stays `False`.** No change to this decision — both signal generators remain not ready for live or demo forward-testing at this time.
