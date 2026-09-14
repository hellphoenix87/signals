# MTF ADX Finer Sweep (Post-Fix)

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history
- **Motivation**: [mtf-gate-sensitivity-sweep.md](mtf-gate-sensitivity-sweep.md) found `MTF_ADX_MIN_STRENGTH=28` clearly beat the default 20 (19.5% vs 17.7% win rate) — but that result was measured against the *old* MACD entry trigger. This re-sweeps a finer grid (24/26/28/30) now that [production-fix-validation.md](production-fix-validation.md) has changed the MTF entry layer's own behavior (crossover MACD), to see whether the earlier ADX conclusion still holds.
- **Target/stop**: 10:5 (the ratio the earlier ADX sweep used), post-fix default win rate at this ratio is 29.5% (327 signals, from [production-fix-validation.md](production-fix-validation.md)).
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --mtf-adx-min-strength <24|26|28|30> --target-pips 10 --stop-pips 5
  ```

## Results

| ADX min strength | Signals (buy/sell) | Win rate |
|---|---|---|
| 20 (default, post-fix) | 327 (169/158) | 29.5% |
| 24 | 257 (135/122) | 31.3% |
| 26 | 212 (115/97) | 28.5% |
| 28 | 183 (95/88) | 28.0% |
| 30 | 153 (75/78) | 29.2% |

For reference, the pre-fix sweep found: default (20) 17.7%, adx=28 19.5%.

## Findings

1. **The earlier ADX conclusion does not replicate post-fix.** Pre-fix, adx=28 clearly beat the default (19.5% vs 17.7%). Post-fix, adx=28 is now *worse* than the default (28.0% vs 29.5%), and the relationship across 24/26/28/30 is non-monotonic (31.3% -> 28.5% -> 28.0% -> 29.2%) rather than a clean trend in either direction.
2. **This is the expected consequence of changing the entry layer's own indicator**, not a contradiction — the earlier ADX sweep was tuned against a specific (and since-replaced) MACD trigger. ADX threshold and entry-indicator behavior interact; a gate tuned for one entry signal doesn't necessarily transfer to another.
3. **adx=24 is marginally the best value tested here (31.3%)**, but the spread across all five values (28.0%-31.3%) is narrow enough, on samples of 153-327 signals each, that this could plausibly be noise rather than a real optimum — no strong recommendation to change the default from this sweep alone.
4. **General lesson for this codebase**: any future gate/threshold tuning should be re-validated whenever an upstream indicator changes, rather than assumed to carry over. Don't reuse old sensitivity-sweep conclusions after modifying the indicators feeding into that gate.
