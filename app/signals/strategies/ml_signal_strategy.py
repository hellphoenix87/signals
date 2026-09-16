from pathlib import Path
from typing import Any, List, Optional

from app.signals.strategies.base_signal_strategy import BaseSignalStrategy
from app.signals.ml.features import extract_features, features_to_vector
from app.utils.configure_logging import logger as default_logger


class MLSignalStrategy(BaseSignalStrategy):
    """
    Config-switchable alternative to `StrongSignalStrategy`'s hand-coded
    indicator vote: predicts buy/sell/hold from a model trained on the
    same underlying indicators (see `scripts/train_signal_model.py`,
    `app/signals/ml/features.py`), rather than hand-picked thresholds
    and weights. Not an attempt at genuine price forecasting -- just a
    different way to combine the same lagging indicators the hand-coded
    rules already use.

    Loads the model lazily (on first `generate_signal` call) and caches
    it; never raises on a missing/unreadable model file or on
    insufficient candle history -- both fall back to "hold", the same
    way the hand-coded strategies handle "not enough data".
    """

    def __init__(
        self,
        config: Any,
        logger: Any = default_logger,
        model_path: Optional[str] = None,
    ):
        self.config = config
        self.logger = logger
        self.model_path = model_path or getattr(config, "ML_MODEL_PATH", "models/entry_signal_model.joblib")
        self._model = None
        self._feature_names = None
        self._load_attempted = False

    def _ensure_model_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True

        path = Path(self.model_path)
        if not path.exists():
            self.logger.error(f"[MLSignalStrategy] Model file not found: {path}")
            return False
        try:
            import joblib

            payload = joblib.load(path)
            self._model = payload["model"]
            self._feature_names = payload["feature_names"]
            return True
        except Exception as e:
            self.logger.error(f"[MLSignalStrategy] Failed to load model from {path}: {e}")
            return False

    def generate_signal(self, candles: List[dict]) -> dict:
        symbol = self._resolve_symbol(candles, self.config)

        if not self._ensure_model_loaded():
            return {
                "final_signal": "hold",
                "raw_signal": "hold",
                "confidence": 0.0,
                "reason": "ml_model_unavailable",
                "symbol": symbol,
            }

        features = extract_features(candles)
        if features is None:
            return {
                "final_signal": "hold",
                "raw_signal": "hold",
                "confidence": 0.0,
                "reason": "not_enough_data_for_features",
                "symbol": symbol,
            }

        vector = [features_to_vector(features)]
        prediction = self._model.predict(vector)[0]
        proba = self._model.predict_proba(vector)[0]
        class_index = list(self._model.classes_).index(prediction)
        confidence = float(proba[class_index])

        self.logger.info(
            f"[MLSignalStrategy] features={features}, prediction={prediction}, confidence={confidence:.2f}"
        )

        return {
            "final_signal": prediction,
            "raw_signal": prediction,
            "confidence": confidence,
            "features": features,
            "symbol": symbol,
        }
