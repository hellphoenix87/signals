# RSI Weight Sweep

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history (window 1)
- **Motivation**: `Config.ENTRY_RSI_WEIGHT = 2.0` (weighting RSI double vs. MACD/SMA in the single-timeframe vote, adopted in [production-fix-validation.md](production-fix-validation.md)) was a first guess from the ablation finding, never itself swept.
- **Fixed**: target=5/stop=5 (1:1, breakeven 50%) — the middle of the plateau found in [ratio-sweet-spot-search.md](ratio-sweet-spot-search.md), with a realistic 1-pip spread.
- **New tooling**: `--rsi-weight` (mirrors the existing `--mtf-score-threshold` pattern) overrides `Config.ENTRY_RSI_WEIGHT` for one run via a `Config` subclass, without touching live settings.
- **Swept**: 1.0 (equal weight, i.e. the pre-fix baseline), 1.5, 2.0 (current default, reused from `ratio-sweet-spot-search.md`), 2.5, 3.0, 4.0.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --target-pips 5 --stop-pips 5 --spread-pips 1 --rsi-weight <1.0|1.5|2.5|3.0|4.0>
  ```

## Results

| RSI weight | Signals | Win rate | Gap to 50% breakeven |
|---|---|---|---|
| 1.0 (equal) | 910 | 38.6% | -11.4 |
| 1.5 | 5,753 | 42.5% | -7.5 |
| 2.0 (previous default) | 7,112 | 42.9% | -7.1 |
| **2.5** | **6,866** | **43.4%** | **-6.6** |
| 3.0 | 6,867 | 43.4% | -6.6 |
| 4.0 | 6,867 | 43.4% | -6.6 |

## Findings

1. **Weight matters a lot below 2.0**: equal weighting (1.0) is clearly worse (-11.4) than any weighted variant, confirming the original ablation-driven decision to weight RSI higher was correct in direction.
2. **The curve saturates at 2.5 and above — 3.0 and 4.0 are bit-identical to 2.5** (same signal count, same win/loss counts). Mechanism: at `Config.CONFIDENCE_THRESHOLD = 0.5` with MACD/SMA weight 1.0 each, RSI's vote alone clears the confidence gate once `rsi_weight / (2 + rsi_weight) >= 0.5`, i.e. `rsi_weight >= 2.0` — from that point on, every signal RSI would trigger alone already fires regardless of how much further its weight increases, so there is nothing left for a higher weight to change.
3. **2.5 is a real, if modest, improvement over 2.0** (-6.6 vs -7.1 points, signal count actually *drops slightly* 7,112 → 6,866 despite the higher weight — some MACD+SMA-agreeing-without-RSI signals lose enough confidence share to drop out as RSI's weight grows, and this swap turns out to be a net quality improvement, consistent with RSI being the strongest individual indicator).
4. **Updated `Config.ENTRY_RSI_WEIGHT` default to 2.5`** (the smallest value that reaches the plateau) as a result of this sweep.
5. **This does not close the gap to breakeven** — still -6.6 points at the best-found ratio/weight combination. Consistent with [ratio-sweet-spot-search.md](ratio-sweet-spot-search.md)'s conclusion that the remaining gap looks structural to this signal engine + spread cost, not something further parameter tuning (ratio or now weight) closes.
