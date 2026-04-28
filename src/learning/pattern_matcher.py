"""
Pattern Matcher for NEXUS ALPHA Learning System.

Uses cosine similarity to find historical market situations
that most closely match the current situation.

Features encoded:
    RSI, MACD, BB_pct, ATR_pct, volume_ratio,
    trend_alignment, sentiment, regime (one-hot)

Storage backend: Supabase with pgvector extension.
Fallback: brute-force cosine similarity on local numpy arrays.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Feature vector size
FEATURE_DIM = 16  # Must match encode_situation output

# Regime one-hot mapping
REGIME_MAP = {
    "trending_up": 0,
    "trending_down": 1,
    "ranging": 2,
    "high_volatility": 3,
}

# Sentiment one-hot
SENTIMENT_MAP = {
    "STRONG_BUY": 0,
    "MODERATE_BUY": 1,
    "SLIGHT_BUY": 2,
    "HOLD": 3,
    "SLIGHT_SELL": 4,
    "MODERATE_SELL": 5,
    "STRONG_SELL": 6,
}


@dataclass
class HistoricalMatch:
    """A single result from the similarity search."""
    situation: Dict[str, Any]        # Original situation dict
    similarity: float                 # Cosine similarity (0.0–1.0)
    outcome: str                      # 'win', 'loss', 'breakeven'
    pnl: float                        # P&L in R-multiples
    strategy: str                     # Strategy that generated the original trade
    timestamp: str = ""


class PatternMatcher:
    """
    Cosine similarity-based historical pattern search.

    Encodes current market situation into a fixed-length feature vector
    and compares against stored historical situation vectors.

    Parameters
    ----------
    supabase_client : optional
        Supabase client with pgvector extension enabled.
        Falls back to in-memory brute-force if None.
    max_local_cache : int
        Maximum situations to cache locally (for brute-force fallback).
    """

    def __init__(
        self,
        supabase_client: Optional[Any] = None,
        max_local_cache: int = 10_000,
    ) -> None:
        self._supabase = supabase_client
        self._max_local_cache = max_local_cache

        # Local fallback store
        self._local_vectors: List[np.ndarray] = []
        self._local_metadata: List[Dict[str, Any]] = []

        logger.info(
            "PatternMatcher initialised | backend=%s | dim=%d",
            "supabase+pgvector" if supabase_client else "local_numpy",
            FEATURE_DIM,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode_situation(self, market_data: Dict[str, Any]) -> np.ndarray:
        """
        Encode a market situation dictionary into a normalised feature vector.

        Parameters
        ----------
        market_data : dict
            Expected keys (all optional with sensible defaults):
                indicators.rsi, indicators.macd_hist, indicators.bb_pct,
                indicators.atr_pct, indicators.volume_ratio,
                indicators.sma_fast, indicators.sma_slow,
                sentiment.fear_greed, sentiment.llm_consensus,
                regime, market

        Returns
        -------
        np.ndarray of shape (FEATURE_DIM,), float32, L2-normalised.
        """
        ind: Dict[str, Any] = market_data.get("indicators", {})
        sentiment: Dict[str, Any] = market_data.get("sentiment", {})
        regime: str = market_data.get("regime", "ranging")

        # --- Scalar features (indices 0–7) ---
        rsi = float(ind.get("rsi", 50.0)) / 100.0          # Normalise to [0,1]
        macd_raw = float(ind.get("macd_hist", 0.0))
        # Normalise MACD by ATR to make it price-independent
        atr = max(float(ind.get("atr", 1.0)), 1e-9)
        close = max(float(ind.get("close", 1.0)), 1e-9)
        macd_norm = np.tanh(macd_raw / atr)

        # Bollinger Band position: (close - lower) / (upper - lower)
        bb_upper = float(ind.get("bb_upper", close * 1.02))
        bb_lower = float(ind.get("bb_lower", close * 0.98))
        bb_range = max(bb_upper - bb_lower, 1e-9)
        bb_pct = (close - bb_lower) / bb_range  # 0 = at lower, 1 = at upper

        # ATR as % of price (volatility normalised)
        atr_pct = atr / close

        # Volume ratio (current / average)
        vol_ratio = min(float(ind.get("volume_ratio", 1.0)), 5.0) / 5.0

        # Trend alignment: (SMA_fast - SMA_slow) / SMA_slow
        sma_fast = float(ind.get("sma_fast", close))
        sma_slow = float(ind.get("sma_slow", close))
        trend_align = np.tanh((sma_fast - sma_slow) / max(sma_slow, 1e-9) * 20)

        # RSI slope (momentum proxy)
        rsi_prev = float(ind.get("rsi_prev", ind.get("rsi", 50.0))) / 100.0
        rsi_slope = (rsi - rsi_prev) * 10.0  # Scale

        # Fear & Greed index normalised
        fear_greed = float(sentiment.get("fear_greed", 50)) / 100.0

        features = np.array([
            rsi,
            float(macd_norm),
            float(bb_pct),
            float(np.tanh(atr_pct * 100)),
            float(vol_ratio),
            float(trend_align),
            float(rsi_slope),
            fear_greed,
        ], dtype=np.float32)

        # --- Regime one-hot (indices 8–11) ---
        regime_vec = np.zeros(4, dtype=np.float32)
        regime_idx = REGIME_MAP.get(regime, 2)  # Default: ranging
        regime_vec[regime_idx] = 1.0

        # --- Sentiment one-hot (indices 12–15, first 4 of 7 categories) ---
        # Collapse to 4 bins: strong_bull, mild_bull, neutral, bear
        consensus = sentiment.get("llm_consensus", "HOLD")
        sentiment_vec = np.zeros(4, dtype=np.float32)
        sent_raw_idx = SENTIMENT_MAP.get(consensus, 3)
        if sent_raw_idx <= 1:
            sentiment_vec[0] = 1.0  # Strong/moderate buy
        elif sent_raw_idx == 2:
            sentiment_vec[1] = 1.0  # Slight buy
        elif sent_raw_idx == 3:
            sentiment_vec[2] = 1.0  # Hold
        else:
            sentiment_vec[3] = 1.0  # Any sell

        # Concatenate all features
        vec = np.concatenate([features, regime_vec, sentiment_vec])
        assert len(vec) == FEATURE_DIM, f"Feature dim mismatch: {len(vec)} != {FEATURE_DIM}"

        # L2 normalise
        norm = np.linalg.norm(vec)
        if norm > 1e-9:
            vec = vec / norm

        return vec.astype(np.float32)

    def store_situation(
        self,
        market_data: Dict[str, Any],
        outcome: str,
        pnl: float,
        strategy: str,
    ) -> None:
        """
        Encode and store a situation for future similarity search.

        Parameters
        ----------
        market_data : dict
            Current market data to encode.
        outcome : str
            'win', 'loss', or 'breakeven'.
        pnl : float
            P&L in R-multiples.
        strategy : str
            Strategy name.
        """
        vec = self.encode_situation(market_data)
        metadata = {
            "situation": market_data,
            "outcome": outcome,
            "pnl": pnl,
            "strategy": strategy,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if self._supabase is not None:
            self._store_in_supabase(vec, metadata)
        else:
            self._store_locally(vec, metadata)

    def find_similar(
        self,
        current_situation: Dict[str, Any],
        k: int = 5,
        min_similarity: float = 0.8,
    ) -> List[HistoricalMatch]:
        """
        Find the k most similar historical situations.

        Parameters
        ----------
        current_situation : dict
            Current market data to match against.
        k : int
            Number of results to return.
        min_similarity : float
            Minimum cosine similarity threshold.

        Returns
        -------
        List[HistoricalMatch] sorted by similarity descending.
        """
        query_vec = self.encode_situation(current_situation)

        if self._supabase is not None:
            matches = self._search_supabase(query_vec, k, min_similarity)
        else:
            matches = self._search_locally(query_vec, k, min_similarity)

        logger.debug("PatternMatcher found %d matches (k=%d, min_sim=%.2f)", len(matches), k, min_similarity)
        return matches

    def get_win_rate_for_situation(
        self, current_situation: Dict[str, Any], k: int = 10
    ) -> Tuple[float, int]:
        """
        Estimate win rate for the current situation based on similar historical matches.

        Returns
        -------
        Tuple[win_rate, num_matches]
        """
        matches = self.find_similar(current_situation, k=k, min_similarity=0.75)
        if not matches:
            return 0.5, 0  # No data → neutral
        wins = sum(1 for m in matches if m.outcome == "win")
        return wins / len(matches), len(matches)

    # ------------------------------------------------------------------
    # Supabase backend
    # ------------------------------------------------------------------

    def _store_in_supabase(self, vec: np.ndarray, metadata: Dict[str, Any]) -> None:
        try:
            self._supabase.table("pattern_vectors").insert({
                "vector": vec.tolist(),
                "outcome": metadata["outcome"],
                "pnl": metadata["pnl"],
                "strategy": metadata["strategy"],
                "timestamp": metadata["timestamp"],
                "situation_snapshot": str(metadata["situation"])[:2000],  # Truncate
            }).execute()
        except Exception as exc:
            logger.warning("Supabase vector store failed: %s", exc)
            # Fall back to local
            self._store_locally(vec, metadata)

    def _search_supabase(
        self, query_vec: np.ndarray, k: int, min_similarity: float
    ) -> List[HistoricalMatch]:
        """
        Use Supabase pgvector match_pattern_vectors RPC for ANN search.
        Falls back to local if the RPC is unavailable.
        """
        try:
            resp = self._supabase.rpc(
                "match_pattern_vectors",
                {
                    "query_embedding": query_vec.tolist(),
                    "match_count": k,
                    "min_similarity": min_similarity,
                },
            ).execute()
            records = resp.data or []
            return [
                HistoricalMatch(
                    situation={},
                    similarity=float(r.get("similarity", 0)),
                    outcome=r.get("outcome", "unknown"),
                    pnl=float(r.get("pnl", 0)),
                    strategy=r.get("strategy", "unknown"),
                    timestamp=r.get("timestamp", ""),
                )
                for r in records
            ]
        except Exception as exc:
            logger.warning("pgvector search failed, falling back to local: %s", exc)
            return self._search_locally(query_vec, k, min_similarity)

    # ------------------------------------------------------------------
    # Local brute-force fallback
    # ------------------------------------------------------------------

    def _store_locally(self, vec: np.ndarray, metadata: Dict[str, Any]) -> None:
        if len(self._local_vectors) >= self._max_local_cache:
            # Evict oldest entry
            self._local_vectors.pop(0)
            self._local_metadata.pop(0)
        self._local_vectors.append(vec)
        self._local_metadata.append(metadata)

    def _search_locally(
        self, query_vec: np.ndarray, k: int, min_similarity: float
    ) -> List[HistoricalMatch]:
        if not self._local_vectors:
            return []

        matrix = np.stack(self._local_vectors, axis=0)  # (N, FEATURE_DIM)
        # Cosine similarity: vectors are already L2-normalised
        scores = matrix @ query_vec  # (N,)

        # Filter by minimum similarity
        valid_mask = scores >= min_similarity
        if not np.any(valid_mask):
            return []

        valid_indices = np.where(valid_mask)[0]
        valid_scores = scores[valid_indices]

        # Top-k
        top_k_local = min(k, len(valid_indices))
        top_k_idx = np.argsort(valid_scores)[::-1][:top_k_local]

        results = []
        for idx in top_k_idx:
            global_idx = valid_indices[idx]
            meta = self._local_metadata[global_idx]
            results.append(
                HistoricalMatch(
                    situation=meta.get("situation", {}),
                    similarity=float(valid_scores[idx]),
                    outcome=meta.get("outcome", "unknown"),
                    pnl=float(meta.get("pnl", 0.0)),
                    strategy=meta.get("strategy", "unknown"),
                    timestamp=meta.get("timestamp", ""),
                )
            )
        return results

    def count_stored(self) -> int:
        """Return number of locally stored situations."""
        return len(self._local_vectors)

    def __repr__(self) -> str:
        return (
            f"<PatternMatcher backend={'supabase' if self._supabase else 'local'} "
            f"stored={self.count_stored()} dim={FEATURE_DIM}>"
        )
