"""Train a RandomForestClassifier entry-signal model as a config-switchable
alternative to the hand-coded MACD/SMA/RSI vote (`StrongSignalStrategy`).

Features: the same indicators the hand-coded rules already use, as raw
values instead of buy/sell/hold decisions (`app/signals/ml/features.py`).
Labels: for every candle, replay both a hypothetical buy and a
hypothetical sell through the same forward-looking win/loss evaluation
used throughout this repo's backtest investigation
(`scripts/backtest_signals.py::evaluate_signal`, target=7/stop=5 pips,
1-pip spread -- MTF's own validated ratio). A candle is labeled "buy" if
the buy hypothesis wins and the sell hypothesis doesn't (mirrored for
"sell"), "hold" otherwise (neither wins, or the ambiguous case where
both would).

Split chronologically (not shuffled) into train/test, since shuffling
would leak future bars into the training set.

Usage:
    pipenv run python scripts/train_signal_model.py [--symbol EURUSD]
        [--weeks 4] [--target-pips 7] [--stop-pips 5] [--spread-pips 1]
        [--test-fraction 0.2] [--out models/entry_signal_model.joblib]
"""

import argparse
import contextlib
import io
import logging
import sys
from pathlib import Path

import joblib
import MetaTrader5 as mt5
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix

from app.config.settings import Config
from app.data.market_data import MarketData
from app.signals.ml.features import FEATURE_NAMES, extract_features, features_to_vector
from app.trade_execution.broker import Broker
from app.trade_execution.mode import TradingMode
from scripts.backtest_signals import evaluate_signal, fetch_history

M1_BARS_PER_TRADING_WEEK = 5 * 24 * 60
FEATURE_LOOKBACK_BARS = 300  # bounded rolling window, not full growing history -- keeps
# feature extraction O(1) per step instead of O(n); EMA-based indicators'
# sensitivity to history beyond this decays to negligible well within it.
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None, help="Symbol to train on (default: Config.SYMBOLS[0])")
    parser.add_argument("--weeks", type=float, default=4.0, help="Weeks of M1 history to fetch")
    parser.add_argument("--target-pips", type=float, default=7.0, help="Favorable-move threshold for labeling (default: MTF's validated ratio)")
    parser.add_argument("--stop-pips", type=float, default=5.0, help="Adverse-move threshold for labeling")
    parser.add_argument("--spread-pips", type=float, default=1.0, help="Round-trip spread cost modeled in labeling")
    parser.add_argument("--forward-bars", type=int, default=300, help="Lookahead window (bars) for labeling")
    parser.add_argument("--test-fraction", type=float, default=0.2, help="Fraction of the (chronologically last) data held out for testing")
    parser.add_argument("--out", default=str(MODELS_DIR / "entry_signal_model.joblib"), help="Where to save the trained model")
    return parser.parse_args()


def build_dataset(candles, pip_size, target_pips, stop_pips, spread_pips, forward_bars):
    """Return (X, y) -- feature vectors and string labels, one row per
    candle with enough history for both feature extraction and a
    resolvable forward label."""
    X, y = [], []
    for i in range(len(candles)):
        window = candles[max(0, i - FEATURE_LOOKBACK_BARS + 1) : i + 1]
        features = extract_features(window)
        if features is None:
            continue

        buy_outcome, _ = evaluate_signal(candles, i, "buy", pip_size, target_pips, stop_pips, forward_bars, spread_pips)
        sell_outcome, _ = evaluate_signal(candles, i, "sell", pip_size, target_pips, stop_pips, forward_bars, spread_pips)

        buy_wins = buy_outcome == "win"
        sell_wins = sell_outcome == "win"
        if buy_wins and not sell_wins:
            label = "buy"
        elif sell_wins and not buy_wins:
            label = "sell"
        else:
            label = "hold"

        X.append(features_to_vector(features))
        y.append(label)

    return X, y


def main() -> None:
    args = parse_args()
    symbol = args.symbol or getattr(Config, "SYMBOLS", ["EURUSD"])[0]
    count = int(args.weeks * M1_BARS_PER_TRADING_WEEK)

    if not mt5.initialize():
        print("MT5 initialization failed.")
        sys.exit(1)

    market_data = MarketData()
    candles = fetch_history(market_data, symbol, Config.TIMEFRAME, count)
    if not candles:
        print(f"No historical candles returned for {symbol}.")
        sys.exit(1)

    broker = Broker(TradingMode.BACKTEST)
    pip_size = broker.get_pip_size(symbol)

    print(f"Building labeled dataset from {len(candles)} candles (this replays two forward evaluations per candle, expect several minutes)...")
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            X, y = build_dataset(candles, pip_size, args.target_pips, args.stop_pips, args.spread_pips, args.forward_bars)
    finally:
        logging.disable(logging.NOTSET)

    if not X:
        print("No usable rows produced -- not enough history for feature extraction.")
        sys.exit(1)

    from collections import Counter
    print(f"Dataset: {len(X)} rows. Label distribution: {dict(Counter(y))}")

    split = int(len(X) * (1 - args.test_fraction))
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    print(f"Chronological split: {len(X_train)} train / {len(X_test)} test")

    model = RandomForestClassifier(n_estimators=200, max_depth=8, min_samples_leaf=20, random_state=42, class_weight="balanced")
    model.fit(X_train, y_train)

    train_acc = model.score(X_train, y_train)
    test_acc = model.score(X_test, y_test)
    print(f"\nTrain accuracy: {train_acc:.3f}  Test accuracy: {test_acc:.3f}")
    print("\nTest classification report:")
    print(classification_report(y_test, model.predict(X_test), zero_division=0))
    print("Test confusion matrix (rows=true, cols=predicted, label order buy/hold/sell):")
    labels = sorted(set(y_train) | set(y_test))
    print(confusion_matrix(y_test, model.predict(X_test), labels=labels))
    print(f"Labels: {labels}")

    print("\nFeature importances:")
    for name, importance in sorted(zip(FEATURE_NAMES, model.feature_importances_), key=lambda x: -x[1]):
        print(f"  {name}: {importance:.3f}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "feature_names": FEATURE_NAMES}, out_path)
    print(f"\nSaved model to {out_path}")


if __name__ == "__main__":
    main()
