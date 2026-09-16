# ML Entry Model vs. Hand-Coded Rules

Date: 2026-09-16

## Setup

- **Symbol**: EURUSD, both out-of-sample windows used throughout this investigation (window 1: `--start-pos 1`; window 2: `--start-pos 28801`).
- **Motivation**: instead of hand-picked indicator thresholds/weights (`StrongSignalStrategy`'s MACD/SMA/RSI vote), train a model on the same underlying indicators to see if it finds a better combination. Explicitly not an attempt at genuine price forecasting (discussed and rejected per market efficiency) — a different way to combine the same lagging indicators already in use.
- **Model**: `sklearn.ensemble.RandomForestClassifier` (200 trees, max_depth=8, `class_weight="balanced"`), trained on 6 features (`app/signals/ml/features.py`: MACD histogram + delta, RSI, SMA diff + short/long slopes) extracted from a bounded 300-bar rolling window. Labels: for every candle, replay both a hypothetical buy and sell through `evaluate_signal` (target=7/stop=5, 1-pip spread — MTF's validated ratio); "buy"/"sell" when exactly one hypothesis wins, "hold" otherwise.
- **Training data**: ~6 weeks (43,349 usable rows after feature availability), starting at `--start-pos 57601` — entirely older than and disjoint from both window 1 and window 2, so the comparison below is genuinely out-of-sample for the model (MT5's available history for this symbol/account ran out before reaching the originally-requested 12 weeks at that offset).
- **Config switch**: `Config.USE_ML_ENTRY_MODEL` (default `False`); `strategy_factory` swaps `MLSignalStrategy` in for the hand-coded vote at the `base`/entry layer when set, composing with MTF wrapping/session filter/n-tick confirmation exactly as the hand-coded rules do.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --ml-entry --target-pips 7 --stop-pips 5 --spread-pips 1 [--start-pos 28801]
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --ml-entry --session-filter --target-pips 7 --stop-pips 5 --spread-pips 1 [--start-pos 28801]
  ```

## Training results (held-out test split within the training data itself)

Test accuracy 32.6% (43,349-row dataset) — essentially tied with the trivial "always predict hold" baseline (41.3%, hold's share of the test set), i.e. **no accuracy lift over doing nothing clever at all**. A separate 4-week-only training run (28,779 rows) gave a nearly identical result (31.8% vs. 35.2% baseline) — roughly 50% more training data didn't move this, suggesting the bottleneck isn't data volume. Feature importances were fairly evenly spread (0.10-0.22 across all 6), with no feature standing out as strongly predictive.

## Backtest results: ML entry vs. hand-coded rules

| Strategy | Window | ML win rate | Hand-coded win rate | Gap |
|---|---|---|---|---|
| Single-timeframe | 1 | 30.8% (n=8,145 decided) | 32.6% (n=3,610 decided) | -1.8 |
| Single-timeframe | 2 | 27.3% (n=8,655 decided) | 33.6% (n=3,553 decided) | -6.3 |
| **Single-timeframe** | **Pooled** | **29.0% (n=16,800)** | **33.1% (n=7,163)** | **-4.1** |
| MTF | 1 | 34.6% (n=610 decided) | 44.4% (n=135 decided) | -9.8 |
| MTF | 2 | 20.8% (n=509 decided) | 39.7% (n=121 decided) | -18.9 |
| **MTF** | **Pooled** | **28.3% (n=1,119)** | **42.2% (n=256)** | **-13.9** |

(Signal counts differ between ML and hand-coded because they're different classifiers producing different buy/sell/hold decisions on the same candles, not a methodology difference — both run under the same live config, including the session filter now on by default.)

## Findings

1. **The hand-coded rules beat the ML model in all four window/strategy combinations, with no exceptions.** This isn't a marginal or mixed result — MTF's gap is particularly large (-9.8 to -18.9 points).
2. **The training-time warning sign (barely better than "always hold") predicted the backtest outcome correctly.** Worth remembering for next time: a classifier that can't beat a trivial baseline on held-out classification accuracy is a real red flag before ever reaching the backtest stage, not something to explain away.
3. **This is a genuine, informative negative result, not a failed experiment.** It's consistent with — and reinforces — the investigation's broader finding that the achievable edge from these specific indicators (MACD/RSI/SMA) is thin and close to the ceiling the hand-tuned rules already found (RSI-weighted vote, crossover MACD fix). A generic model over the *same* inputs, with generic hyperparameters, didn't find anything the careful manual tuning throughout this investigation had missed — if anything, the hand-tuned domain knowledge (specific thresholds, RSI's known individual edge, the crossover-vs-accelerating distinction) outperformed a more generic learned combination.
4. **What this doesn't rule out**: a materially different feature set (e.g. ADX, multi-timeframe features, tick-level microstructure), a different label definition (the buy/sell/hold 3-class scheme here is a specific, debatable choice), or a different model class (gradient boosting, or a simpler regularized model less prone to the noise this RandomForest may be fitting). None of those were tried — this result speaks to *this* specific model/feature/label combination, not to "ML in general" for this problem.

## Recommendation

**`Config.USE_ML_ENTRY_MODEL` stays `False`** (already the default). The hand-coded rules remain the better-performing entry logic for both strategies as currently configured. The ML infrastructure (`app/signals/ml/`, `MLSignalStrategy`, `scripts/train_signal_model.py`, `--ml-entry`) stays in the codebase as a working, switchable path for future iteration (different features/labels/models), not deleted — but not switched on.
