# ML Entry Signal Model (Config-Switchable Alternative to Hand-Coded Rules)

Status: todo
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

Replace the hand-coded indicator-threshold rules (`StrongSignalStrategy`'s MACD/SMA/RSI weighted vote) with a trained model as a **config-switchable alternative**, not a replacement -- `Config.USE_ML_ENTRY_MODEL` flips between them, everything else (MTF wrapping, session filter, n-tick confirmation) composes on top of either unchanged, since both implement the same `BaseSignalStrategy` interface.

Motivation (from conversation, not a formal backtest finding yet): our current rules are a hand-designed combination of a few indicators with hand-picked weights/thresholds. A model trained on the same underlying features could learn a better combination than we hand-coded, potentially extracting more of the small, validated edge (session-filter-analysis.md, rsi-weight-sweep.md) than the current blunt vote does. This is explicitly **not** an attempt to forecast future price with enough lead time to front-run moves (discussed and rejected as very unlikely to work on public price data alone, per market efficiency) -- it's a better way to combine the same lagging indicators we already trust, not a fundamentally different information source.

## Out of scope (explicit, not forgotten)

- Forecasting actual future price values (regression) or anything claiming genuine lead time over the market -- discussed and deliberately not attempted, per the market-efficiency discussion.
- News/sentiment or any alternative data source -- a different, separately-discussed idea, not part of this plan.
- Any change to the exit strategy -- entry-side only.
- Retraining automation / MLOps (scheduled retraining, drift monitoring) -- this is a one-off trained artifact for now; production retraining is a future decision once/if this proves worthwhile.
- No tests, per MVP/POC mode. Verified via direct backtest comparison against the existing hand-coded rules on the same historical windows used throughout this investigation.

## Phase 1: Feature extraction + labeled training data

- **New `app/signals/ml/features.py`**: `extract_features(candles: List[dict]) -> Optional[Dict[str, float]]` -- computes named features from the same indicators already in production: MACD histogram value and its delta from the previous bar (`app/signals/indicators/macd.py`'s internal EMA/histogram calc, exposed as raw numbers rather than a buy/sell/hold string), RSI value (`calculate_rsi`, exposing the raw 0-100 value), SMA short/long values and their difference (`generate_sma_signal`'s internal calc). Returns `None` if there isn't enough data (mirrors `MIN_CANDLES_FOR_INDICATORS`), consistent with existing indicator functions' own insufficient-data handling.
- **New `scripts/train_signal_model.py`**: fetches historical M1 EURUSD data (reusing `MarketData`/`fetch_history` from `scripts/backtest_signals.py`), and for every candle with enough history, computes `extract_features` plus a label: reusing `evaluate_signal` (target=7/stop=5, 1-pip spread, matching MTF's validated ratio) to evaluate **both** a hypothetical buy and a hypothetical sell from that candle; label is `"buy"` if the buy hypothesis wins and the sell hypothesis doesn't, `"sell"` for the mirror case, `"hold"` otherwise (neither wins, or the ambiguous case where both would). Splits chronologically (not shuffled, to avoid training on the future and testing on the past) into train/test, trains an `sklearn.ensemble.RandomForestClassifier`, prints train/test accuracy and a confusion matrix, and saves the fitted model to `models/entry_signal_model.joblib` (new `models/` directory).
- Add `scikit-learn` to `Pipfile`.

## Phase 2: `MLSignalStrategy` + config switch

- **New `app/signals/strategies/ml_signal_strategy.py`**: `MLSignalStrategy(BaseSignalStrategy)` -- lazy-loads the trained model from `Config.ML_MODEL_PATH` on first use (cached after that), calls `extract_features` on the incoming candles, predicts a class (`buy`/`sell`/`hold`) and its probability, and returns a `generate_signal`-compatible dict (`final_signal`, `raw_signal`, `confidence` set to the predicted class's probability, `symbol`). If the model file is missing or `extract_features` returns `None` (insufficient data), returns `{"final_signal": "hold", "raw_signal": "hold", "confidence": 0.0, "reason": "..."}` rather than raising -- never crash the orchestrator over a missing/stale model artifact.
- **`app/config/settings.py`**: add `USE_ML_ENTRY_MODEL: bool = False` (default off -- opt-in only, since it needs a trained model file to exist) and `ML_MODEL_PATH: str = "models/entry_signal_model.joblib"`.
- **`strategy_factory`**: when `getattr(config, "USE_ML_ENTRY_MODEL", False)`, build `MLSignalStrategy(config=config)` instead of the `StrongSignalStrategy(...)` indicator vote for the `base`/entry layer -- everything downstream (MTF bias/confirm wrapping, n-tick confirmation, session filter) wraps around it exactly as it does the hand-coded rules today, unchanged.

## Phase 3: Backtest comparison

- **`scripts/backtest_signals.py`**: add `--ml-entry` (store_true, not compatible with `--indicators`, same restriction pattern as the existing mutual-exclusivity check) to swap in `MLSignalStrategy` for the single-timeframe/MTF-entry-layer base, via the same `config` override mechanism already used for `--rsi-weight`/`--mtf-score-threshold`.
- Run the same comparison this whole investigation has used throughout: target=7/stop=5, 1-pip spread, session filter on, both historical windows (`--start-pos 1` and `--start-pos 28801`) -- once for the hand-coded rules (existing baseline, already measured: pooled 42.2% vs 41.7% breakeven), once for the ML model.
- Write up the comparison as `docs/test-results/ml-entry-model-comparison.md`, following the established report format.

## Verification (manual, per MVP/POC mode)

- `extract_features` produces the expected keys/values on a small hand-built candle sample, and returns `None` on too-short input.
- `scripts/train_signal_model.py` runs end-to-end against real MT5 history and produces a saved model file plus a train/test accuracy readout that isn't degenerate (e.g. not predicting "hold" 100% of the time, which would be a trivial/useless classifier given "hold" is likely the majority class).
- `strategy_factory(config=Config)` with `USE_ML_ENTRY_MODEL=True` on a `Config` subclass override produces an `MLSignalStrategy` instance; with it unset/`False`, produces the existing `StrongSignalStrategy` unchanged.
- `scripts/backtest_signals.py --ml-entry` runs without error against real history and produces a real (not degenerate) win/loss distribution.
- The comparison report has an honest verdict: report whichever result is genuinely better (or if they're statistically indistinguishable), not a foregone conclusion that the ML model wins just because it's newer/more sophisticated.
