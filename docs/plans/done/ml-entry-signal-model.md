# ML Entry Signal Model (Config-Switchable Alternative to Hand-Coded Rules)

Status: done
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

## Verification (manual, per MVP/POC mode) -- all confirmed

- `extract_features` confirmed on hand-built samples: returns the expected 6-key dict on sufficient data, `None` on too-short input (verified both a 5-candle and a 30-candle case).
- `scripts/train_signal_model.py` ran end-to-end twice (4 weeks, then ~6 weeks of older/disjoint data via `--start-pos 57601`), producing saved model files and non-degenerate train/test accuracy readouts (not predicting one class 100% of the time) -- but both showed a real warning sign: test accuracy (31.8%, 32.6%) barely beat or lagged the trivial "always predict hold" baseline (35.2%, 41.3%). More data (4 -> ~6 weeks) didn't move this.
- `strategy_factory` confirmed directly: `USE_ML_ENTRY_MODEL=True` produces `MLSignalStrategy` for both the single-timeframe base and MTF's entry layer; unset/`False` produces the existing `StrongSignalStrategy` unchanged.
- **Found and fixed a real bug during this verification**: `run_backtest` never explicitly passed `use_multi` to `strategy_factory` -- harmless while `Config.USE_MULTI_TIMEFRAME_SIGNALS` defaulted `False`, but silently wrong now that it's `True` (PR #46). Fixed to force `use_multi=False` explicitly, since `run_backtest`'s flat-candle-list replay is fundamentally incompatible with MTF's per-timeframe dict input.
- `scripts/backtest_signals.py --ml-entry` ran against real history for both strategies, both out-of-sample windows, producing real (non-degenerate) win/loss distributions.
- **Comparison report has an honest verdict, following through on the classification-accuracy warning sign**: the ML model lost to the hand-coded rules in all 4 window/strategy combinations tested (single-tf: -1.8 to -6.3 points; MTF: -9.8 to -18.9 points). `Config.USE_ML_ENTRY_MODEL` stays `False` (its existing default) -- see `docs/test-results/ml-entry-model-comparison.md` for the full numbers and recommendation.
