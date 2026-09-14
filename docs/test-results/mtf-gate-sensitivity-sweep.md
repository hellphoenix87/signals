# MTF Gate Sensitivity Sweep

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history
- **Motivation**: `MultiTimeframeStrongSignalStrategy`'s confluence gate (`MTF_SCORE_THRESHOLD = 0.6` default) and ADX trend-strength filter (`MTF_ADX_MIN_STRENGTH = 20.0` default) were never tuned — just carried over from when the strategy was built (`mtf-signal-folding` plan). This checks whether tightening either is a quick win, using values meaningfully stricter than default rather than a fine grid, given the volume of other testing already in this batch.
- **New tooling**: `--mtf-score-threshold`/`--mtf-adx-min-strength` (added this session) override `Config.MTF_SCORE_THRESHOLD`/`MTF_ADX_MIN_STRENGTH` via a dynamically-built `Config` subclass, passed to `strategy_factory` for that run only — live settings untouched.
- **Target/stop**: 15/5 pips (3:1 R:R, breakeven 25%), matching the other reports in this batch. Default-config MTF result at these same parameters comes from [target-stop-realistic-resweep.md](target-stop-realistic-resweep.md) (17.7%, 855 signals).
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --mtf-score-threshold 0.75 --target-pips 15 --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --mtf-adx-min-strength 28 --target-pips 15 --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --mtf-score-threshold 0.75 --mtf-adx-min-strength 28 --target-pips 15 --stop-pips 5
  ```

## Results

| Config | Signals (buy/sell) | Win rate |
|---|---|---|
| Default (score=0.6, adx=20) | 855 (621/234) | 17.7% |
| score=0.75 | 63 (4/59) | 14.5% |
| **adx=28** | **454 (335/119)** | **19.5%** |
| score=0.75 + adx=28 | 40 (0/40) | 18.9% |

## Findings

1. **Raising `MTF_ADX_MIN_STRENGTH` to 28 is the most effective single change tested**: 19.5% win rate (vs. 17.7% default) on a still-meaningful sample (454 signals) — the ADX trend-strength filter is doing real, useful work, and the default of 20 may be too permissive.
2. **Raising `MTF_SCORE_THRESHOLD` to 0.75 alone made things worse (14.5%)** and produced a suspiciously lopsided sample (4 buy vs. 59 sell) on a small n=63 — this looks like overfitting to a specific directional stretch in this 4-week window rather than a genuine quality improvement, and shouldn't be trusted without a longer/different window to confirm.
3. **Combining both (score=0.75 + adx=28) doesn't clearly beat ADX alone** (18.9% vs. 19.5%) and shrinks the sample to 40 — not worth the added restriction given ADX alone already captures most of the benefit.
4. **Recommendation**: `MTF_ADX_MIN_STRENGTH` is the more promising tunable of the two; worth a finer sweep around 24-30 in a future pass, plus testing on a longer window before trusting the score-threshold result at all given its small, skewed sample.
