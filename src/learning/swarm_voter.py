"""
Swarm Voter for NEXUS ALPHA — MiroFish-inspired ensemble.

Creates N=100 lightweight sklearn models, each trained on a slightly
different feature subset and time window. Combines their votes into
a continuous signal from -1.0 (strong bear) to +1.0 (strong bull).

Models: RandomForest, GradientBoosting, ExtraTrees (sklearn)
Feature subset: random 60% of available features per model
Time window: random 20-60 days per model

Retrain schedule: weekly.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

N_MODELS = 100
FEATURE_SUBSET_RATIO = 0.60    # Each model uses 60% of features
MIN_WINDOW_DAYS = 20
MAX_WINDOW_DAYS = 60
MODEL_TYPES = ["rf", "gb", "et"]   # RandomForest, GradientBoosting, ExtraTrees

# Feature names (must match build_feature_matrix output)
ALL_FEATURES = [
    "rsi", "rsi_slope", "macd_hist", "macd_signal",
    "bb_pct", "bb_width", "atr_pct", "volume_ratio",
    "sma20_slope", "sma50_slope", "close_vs_sma20",
    "close_vs_sma50", "high_low_range", "body_pct",
    "upper_shadow", "lower_shadow", "fear_greed",
    "funding_rate", "open_interest_change",
]
N_FEATURES = len(ALL_FEATURES)


class SwarmVoter:
    """
    Ensemble swarm of 100 lightweight classifiers.

    Each model is a binary UP/DOWN predictor trained on a different
    (feature subset × time window) combination. Predictions are
    aggregated into a continuous signal value.

    Parameters
    ----------
    model_dir : str
        Directory for saving/loading trained model ensemble.
    market : str
        Target market (used for model namespacing).
    """

    def __init__(
        self,
        model_dir: str = "./models/swarm",
        market: str = "crypto",
    ) -> None:
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._market = market

        # Each element: {'model': estimator, 'feature_indices': list, 'window': int}
        self._models: List[Dict[str, Any]] = []
        self._trained_at: Optional[str] = None
        self._is_trained: bool = False

        logger.info(
            "SwarmVoter initialised | market=%s | N=%d | model_dir=%s",
            market, N_MODELS, model_dir,
        )

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train_swarm(
        self,
        df: Any,              # DataFrame with OHLCV + indicators
        market: str,
        n_models: int = N_MODELS,
        forward_bars: int = 5,  # Predict direction N bars forward
    ) -> Dict[str, Any]:
        """
        Train the full swarm ensemble on historical data.

        Parameters
        ----------
        df : DataFrame
            Historical data with columns matching ALL_FEATURES + 'close'.
            Must have at least MAX_WINDOW_DAYS + 30 rows.
        market : str
            Market identifier.
        n_models : int
            Number of models to train.
        forward_bars : int
            Prediction horizon: UP if close[t+N] > close[t], else DOWN.

        Returns
        -------
        dict
            Training summary: {n_trained, avg_train_accuracy, trained_at}
        """
        try:
            from sklearn.ensemble import (
                RandomForestClassifier,
                GradientBoostingClassifier,
                ExtraTreesClassifier,
            )
        except ImportError:
            logger.error("scikit-learn not installed; cannot train swarm")
            return {"n_trained": 0, "avg_train_accuracy": 0.0, "error": "sklearn_missing"}

        logger.info("Training swarm: %d models for %s", n_models, market)

        # Build full feature matrix
        X_full, y_full = self._build_dataset(df, forward_bars)
        if X_full is None or len(X_full) < 50:
            logger.error("Insufficient data for swarm training")
            return {"n_trained": 0, "avg_train_accuracy": 0.0}

        self._models.clear()
        accuracies = []

        model_type_cycle = (MODEL_TYPES * ((n_models // len(MODEL_TYPES)) + 1))[:n_models]

        for i in range(n_models):
            # Random feature subset (60%)
            n_feat = max(3, int(N_FEATURES * FEATURE_SUBSET_RATIO))
            feature_indices = sorted(random.sample(range(N_FEATURES), n_feat))

            # Random time window (20-60 days worth of bars)
            # Assume daily data: 1 bar = 1 day. Adjust for intraday.
            window_days = random.randint(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, len(X_full) - forward_bars - 5))
            window_bars = min(window_days, len(X_full) - 1)
            X_window = X_full[-window_bars:][:, feature_indices]
            y_window = y_full[-window_bars:]

            if len(np.unique(y_window)) < 2:
                continue  # Skip if only one class in window

            # Instantiate model
            model_type = model_type_cycle[i]
            model = self._make_model(model_type, n_estimators=20)

            # Train
            try:
                model.fit(X_window, y_window)
                train_acc = model.score(X_window, y_window)
                accuracies.append(train_acc)
                self._models.append({
                    "model": model,
                    "feature_indices": feature_indices,
                    "window_days": window_days,
                    "model_type": model_type,
                    "train_accuracy": train_acc,
                })
            except Exception as exc:
                logger.debug("Model %d training failed: %s", i, exc)
                continue

            if (i + 1) % 20 == 0:
                logger.debug("Swarm training: %d/%d models complete", i + 1, n_models)

        self._trained_at = datetime.now(timezone.utc).isoformat()
        self._is_trained = True
        self._market = market

        avg_acc = float(np.mean(accuracies)) if accuracies else 0.0
        logger.info(
            "Swarm training complete: %d/%d models | avg_accuracy=%.3f",
            len(self._models), n_models, avg_acc,
        )
        return {
            "n_trained": len(self._models),
            "avg_train_accuracy": avg_acc,
            "trained_at": self._trained_at,
        }

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def vote(self, features: np.ndarray) -> float:
        """
        Aggregate all model predictions into a single directional signal.

        Parameters
        ----------
        features : np.ndarray
            Feature vector of shape (N_FEATURES,) matching ALL_FEATURES order.

        Returns
        -------
        float
            Signal value from -1.0 (strong bear) to +1.0 (strong bull).
            Formula: (pct_bullish * 2) - 1.0

        Example:
            70% of models predict UP → signal = 0.70 * 2 - 1.0 = 0.40
            100% predict UP → signal = 1.0
            50% predict UP → signal = 0.0 (neutral)
        """
        if not self._models:
            logger.warning("SwarmVoter has no trained models; returning neutral (0.0)")
            return 0.0

        bullish_count = 0
        valid_count = 0

        for m_info in self._models:
            feature_indices = m_info["feature_indices"]
            model = m_info["model"]
            x = features[feature_indices].reshape(1, -1)

            try:
                pred = int(model.predict(x)[0])
                if pred == 1:
                    bullish_count += 1
                valid_count += 1
            except Exception:
                continue

        if valid_count == 0:
            return 0.0

        pct_bullish = bullish_count / valid_count
        signal = pct_bullish * 2.0 - 1.0
        logger.debug(
            "SwarmVoter: %d/%d bullish (%.1f%%) → signal=%.3f",
            bullish_count, valid_count, pct_bullish * 100, signal,
        )
        return float(np.clip(signal, -1.0, 1.0))

    def predict_with_confidence(
        self, features: np.ndarray
    ) -> Tuple[str, float, float]:
        """
        Extended prediction with direction, signal strength, and agreement.

        Returns
        -------
        Tuple[direction, signal, agreement]
            direction: 'LONG', 'SHORT', or 'HOLD'
            signal: -1.0 to 1.0
            agreement: 0.0 to 1.0 (fraction of models agreeing with majority)
        """
        signal = self.vote(features)

        if signal > 0.2:
            direction = "LONG"
            agreement = (signal + 1.0) / 2.0
        elif signal < -0.2:
            direction = "SHORT"
            agreement = (-signal + 1.0) / 2.0
        else:
            direction = "HOLD"
            agreement = 1.0 - abs(signal)

        return direction, signal, agreement

    def build_feature_vector(self, market_data: Dict[str, Any]) -> np.ndarray:
        """
        Build feature vector from market_data dict matching ALL_FEATURES order.

        Parameters
        ----------
        market_data : dict
            Must include 'indicators' and 'sentiment' sub-dicts.

        Returns
        -------
        np.ndarray of shape (N_FEATURES,)
        """
        ind = market_data.get("indicators", {})
        sentiment = market_data.get("sentiment", {})

        close = max(float(ind.get("close", 1.0)), 1e-9)
        atr = max(float(ind.get("atr", 1.0)), 1e-9)

        rsi = float(ind.get("rsi", 50.0)) / 100.0
        rsi_prev = float(ind.get("rsi_prev", ind.get("rsi", 50.0))) / 100.0
        rsi_slope = (rsi - rsi_prev) * 5.0

        macd_hist = float(ind.get("macd_hist", 0.0)) / atr
        macd_sig = float(ind.get("macd_signal", 0.0)) / atr

        bb_upper = float(ind.get("bb_upper", close * 1.02))
        bb_lower = float(ind.get("bb_lower", close * 0.98))
        bb_range = max(bb_upper - bb_lower, 1e-9)
        bb_pct = (close - bb_lower) / bb_range
        bb_width = bb_range / close

        atr_pct = atr / close
        vol_ratio = min(float(ind.get("volume_ratio", 1.0)), 5.0) / 5.0

        sma20 = float(ind.get("sma20", close))
        sma50 = float(ind.get("sma50", close))
        prev_sma20 = float(ind.get("prev_sma20", sma20))
        prev_sma50 = float(ind.get("prev_sma50", sma50))
        sma20_slope = (sma20 - prev_sma20) / max(prev_sma20, 1e-9)
        sma50_slope = (sma50 - prev_sma50) / max(prev_sma50, 1e-9)
        close_vs_sma20 = (close - sma20) / max(sma20, 1e-9)
        close_vs_sma50 = (close - sma50) / max(sma50, 1e-9)

        high = float(ind.get("high", close))
        low_ = float(ind.get("low", close))
        open_ = float(ind.get("open", close))
        hl_range = (high - low_) / max(close, 1e-9)
        body_pct = abs(close - open_) / max(hl_range * close, 1e-9)
        upper_shadow = (high - max(close, open_)) / max(close, 1e-9)
        lower_shadow = (min(close, open_) - low_) / max(close, 1e-9)

        fg = float(sentiment.get("fear_greed", 50)) / 100.0
        funding = float(sentiment.get("funding_rate", 0.0))
        oi_change = float(sentiment.get("open_interest_change", 0.0))

        features = np.array([
            rsi, rsi_slope, np.tanh(macd_hist), np.tanh(macd_sig),
            bb_pct, bb_width, np.tanh(atr_pct * 100), vol_ratio,
            np.tanh(sma20_slope * 100), np.tanh(sma50_slope * 100),
            np.tanh(close_vs_sma20 * 100), np.tanh(close_vs_sma50 * 100),
            np.tanh(hl_range * 20), np.clip(body_pct, 0, 1),
            np.tanh(upper_shadow * 50), np.tanh(lower_shadow * 50),
            fg, np.tanh(funding * 100), np.tanh(oi_change * 10),
        ], dtype=np.float32)

        assert len(features) == N_FEATURES
        return features

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_models(self) -> str:
        """Serialise and save all models to disk using pickle."""
        filename = f"{self._market}_swarm_{N_MODELS}.pkl"
        path = self._model_dir / filename
        payload = {
            "models": self._models,
            "trained_at": self._trained_at,
            "market": self._market,
            "n_features": N_FEATURES,
            "feature_names": ALL_FEATURES,
        }
        with open(str(path), "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info("SwarmVoter: saved %d models to %s", len(self._models), path)
        return str(path)

    def load_models(self, market: Optional[str] = None) -> bool:
        """Load previously saved models from disk."""
        mkt = market or self._market
        filename = f"{mkt}_swarm_{N_MODELS}.pkl"
        path = self._model_dir / filename
        if not path.exists():
            logger.warning("No swarm model file found at %s", path)
            return False
        try:
            with open(str(path), "rb") as f:
                payload = pickle.load(f)
            self._models = payload["models"]
            self._trained_at = payload.get("trained_at")
            self._market = payload.get("market", mkt)
            self._is_trained = True
            logger.info(
                "SwarmVoter: loaded %d models (trained %s)",
                len(self._models), self._trained_at,
            )
            return True
        except Exception as exc:
            logger.error("Failed to load swarm models: %s", exc)
            return False

    def needs_retraining(self, retrain_after_days: int = 7) -> bool:
        """Check if the swarm is due for retraining (weekly schedule)."""
        if not self._trained_at or not self._is_trained:
            return True
        try:
            trained_dt = datetime.fromisoformat(self._trained_at)
            if trained_dt.tzinfo is None:
                trained_dt = trained_dt.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - trained_dt).days
            return elapsed >= retrain_after_days
        except Exception:
            return True

    @property
    def n_models(self) -> int:
        return len(self._models)

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_model(model_type: str, n_estimators: int = 20) -> Any:
        """Instantiate a sklearn estimator."""
        from sklearn.ensemble import (
            RandomForestClassifier,
            GradientBoostingClassifier,
            ExtraTreesClassifier,
        )
        params = {
            "n_estimators": n_estimators,
            "random_state": random.randint(0, 9999),
            "max_features": "sqrt",
        }
        if model_type == "rf":
            return RandomForestClassifier(**params)
        elif model_type == "gb":
            # GBC doesn't support max_features kwarg in all versions
            gb_params = {"n_estimators": n_estimators, "random_state": params["random_state"]}
            return GradientBoostingClassifier(**gb_params)
        else:  # et
            return ExtraTreesClassifier(**params)

    @staticmethod
    def _build_dataset(
        df: Any, forward_bars: int
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Build feature matrix X and binary label y from DataFrame.

        y[t] = 1 if close[t+forward_bars] > close[t] else 0
        """
        try:
            # Try to extract each feature from the dataframe
            n = len(df)
            if n < forward_bars + 10:
                return None, None

            X = np.zeros((n - forward_bars, N_FEATURES), dtype=np.float32)
            y = np.zeros(n - forward_bars, dtype=int)

            closes = np.array(df["close"], dtype=float)

            for i in range(n - forward_bars):
                # Fill features with available data or defaults
                row = {}
                for feat in ALL_FEATURES:
                    col_name = feat
                    # Map feature names to DataFrame columns
                    if col_name in df.columns if hasattr(df, "columns") else col_name in df:
                        val = float(df[col_name].iloc[i] if hasattr(df, "iloc") else df[col_name][i])
                    else:
                        val = 0.0
                    row[feat] = val

                X[i] = [row[f] for f in ALL_FEATURES]
                y[i] = 1 if closes[i + forward_bars] > closes[i] else 0

            return X, y

        except Exception as exc:
            logger.error("Dataset build failed: %s", exc)
            return None, None

    def __repr__(self) -> str:
        return (
            f"<SwarmVoter market={self._market} n_models={len(self._models)} "
            f"trained={self._is_trained} trained_at={self._trained_at}>"
        )
