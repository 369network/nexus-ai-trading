"""
Long-Term Memory for NEXUS ALPHA Learning System.

Permanent knowledge store. Retains:
    - Market truths (immutable facts about market behaviour)
    - Optimised strategy parameters from Dream Mode
    - Historical parameter evolution

Pre-populated with common market truths on first initialisation.
Backed by Supabase for durability.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Pre-populated market truths used to warm the store on first init
_SEED_MARKET_TRUTHS = [
    {
        "market": "crypto",
        "fact": "BTC tends to lead altcoin moves by 30-60 minutes",
        "confidence": 0.80,
        "evidence_count": 50,
    },
    {
        "market": "crypto",
        "fact": "Positive funding rates > 0.1% often precede short-term corrections",
        "confidence": 0.75,
        "evidence_count": 100,
    },
    {
        "market": "crypto",
        "fact": "Fear & Greed < 20 historically correlates with medium-term bottoms",
        "confidence": 0.70,
        "evidence_count": 30,
    },
    {
        "market": "forex",
        "fact": "London-New York session overlap (13:00-17:00 UTC) has highest liquidity and directional moves",
        "confidence": 0.90,
        "evidence_count": 200,
    },
    {
        "market": "forex",
        "fact": "NFP release causes the largest intraday moves in USD pairs on the first Friday of the month",
        "confidence": 0.95,
        "evidence_count": 150,
    },
    {
        "market": "forex",
        "fact": "Carry trades tend to unwind sharply during risk-off events (VIX spikes)",
        "confidence": 0.85,
        "evidence_count": 80,
    },
    {
        "market": "commodities",
        "fact": "Gold is inversely correlated with real interest rates over multi-month periods",
        "confidence": 0.88,
        "evidence_count": 120,
    },
    {
        "market": "commodities",
        "fact": "Crude oil shows seasonal demand strength in Q2 due to US driving season",
        "confidence": 0.72,
        "evidence_count": 60,
    },
    {
        "market": "indian",
        "fact": "FII net buying > INR 2000 Cr sustained for 5 days historically leads Nifty higher",
        "confidence": 0.78,
        "evidence_count": 40,
    },
    {
        "market": "indian",
        "fact": "NSE options max pain exerts gravitational pull on expiry Thursdays within ±1.5%",
        "confidence": 0.73,
        "evidence_count": 52,
    },
    {
        "market": "us",
        "fact": "S&P 500 tends to drift higher in the week before expiry (OpEx effect)",
        "confidence": 0.65,
        "evidence_count": 35,
    },
    {
        "market": "us",
        "fact": "ORB strategies have highest success rates when gap from previous close > 0.3%",
        "confidence": 0.70,
        "evidence_count": 80,
    },
    {
        "market": "all",
        "fact": "Volatility clusters: periods of high volatility tend to be followed by high volatility",
        "confidence": 0.92,
        "evidence_count": 500,
    },
    {
        "market": "all",
        "fact": "Trend strategies outperform in trending regimes; mean reversion outperforms in ranging",
        "confidence": 0.88,
        "evidence_count": 300,
    },
]


class LongTermMemory:
    """
    Permanent knowledge base for NEXUS ALPHA.

    Stores market truths and optimised strategy parameters that
    persist indefinitely (no expiry). Updates are additive —
    older records are never deleted but confidence is updated.

    Parameters
    ----------
    supabase_client : optional
        Supabase client. If None, operates in local-only mode.
    """

    def __init__(self, supabase_client: Optional[Any] = None) -> None:
        self._supabase = supabase_client

        # Local stores
        self._market_truths: List[Dict[str, Any]] = []
        self._strategy_params: Dict[str, Dict[str, Any]] = {}

        # Seed with common market truths
        self._seed_truths()
        logger.info("LongTermMemory initialised with %d seed truths", len(self._market_truths))

    # ------------------------------------------------------------------
    # Market truths
    # ------------------------------------------------------------------

    def store_market_truth(
        self,
        fact: str,
        confidence: float,
        evidence_count: int,
        market: str = "all",
        source: str = "system",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Store or update a permanent market truth.

        If an identical fact already exists, updates its confidence
        using a weighted average and increments evidence_count.

        Parameters
        ----------
        fact : str
            Human-readable description of the market truth.
        confidence : float
            0.0–1.0 confidence score.
        evidence_count : int
            Number of observations supporting this fact.
        market : str
            Applicable market: 'crypto', 'forex', 'all', etc.
        source : str
            Where this truth originated: 'backtest', 'llm', 'system', etc.
        """
        confidence = max(0.0, min(1.0, confidence))
        existing = self._find_similar_truth(fact, market)

        if existing:
            # Update via weighted average
            old_count = existing.get("evidence_count", 1)
            new_count = old_count + evidence_count
            old_conf = existing.get("confidence", 0.5)
            new_conf = (old_conf * old_count + confidence * evidence_count) / new_count
            existing["confidence"] = round(new_conf, 4)
            existing["evidence_count"] = new_count
            existing["last_updated"] = datetime.now(timezone.utc).isoformat()
            self._persist("long_term_market_truths", existing)
            logger.debug("Updated market truth: '%s...' conf=%.3f", fact[:60], new_conf)
        else:
            entry = {
                "fact": fact,
                "market": market,
                "confidence": round(confidence, 4),
                "evidence_count": evidence_count,
                "source": source,
                "metadata": metadata or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            self._market_truths.append(entry)
            self._persist("long_term_market_truths", entry)
            logger.info("Stored new market truth [%s]: '%s...'", market, fact[:60])

    def get_market_truths(
        self,
        market: str,
        min_confidence: float = 0.6,
        min_evidence: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Return market truths applicable to a given market.

        Parameters
        ----------
        market : str
            Market to filter. Also includes 'all' truths.
        min_confidence : float
            Only return truths above this confidence level.
        min_evidence : int
            Minimum number of observations required.

        Returns
        -------
        List[dict] sorted by confidence descending.
        """
        result = [
            t for t in self._market_truths
            if t.get("market") in (market, "all")
            and t.get("confidence", 0) >= min_confidence
            and t.get("evidence_count", 0) >= min_evidence
        ]
        result.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return result

    def get_all_truths(self) -> List[Dict[str, Any]]:
        """Return all stored market truths."""
        return list(self._market_truths)

    # ------------------------------------------------------------------
    # Strategy parameters
    # ------------------------------------------------------------------

    def update_strategy_params(
        self,
        strategy_name: str,
        params: Dict[str, Any],
        performance_metrics: Dict[str, Any],
        approved_by: str = "auto",
    ) -> None:
        """
        Store or update optimised parameters for a strategy.

        Called by Dream Mode after approval (or auto-apply).

        Parameters
        ----------
        strategy_name : str
            Name of the strategy (must match BaseStrategy.name).
        params : dict
            The new parameter set to store.
        performance_metrics : dict
            BacktestMetric summary showing why these params are better.
        approved_by : str
            Who approved: 'auto', 'human:{user_id}', etc.
        """
        entry = {
            "strategy_name": strategy_name,
            "params": params,
            "performance_metrics": performance_metrics,
            "approved_by": approved_by,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if strategy_name in self._strategy_params:
            # Append to history, keep latest as current
            history = self._strategy_params[strategy_name].get("history", [])
            history.append(self._strategy_params[strategy_name].get("current", {}))
            entry["history"] = history[-10:]  # Keep last 10 versions
        else:
            entry["history"] = []

        entry["current"] = params
        self._strategy_params[strategy_name] = entry
        self._persist("long_term_strategy_params", entry)
        logger.info(
            "Updated strategy params: %s | Sharpe=%.3f",
            strategy_name,
            performance_metrics.get("sharpe_ratio", 0.0),
        )

    def get_optimized_params(self, strategy_name: str) -> Optional[Dict[str, Any]]:
        """
        Return the current best parameter set for a strategy.

        Returns
        -------
        dict or None
            Current optimised params, or None if not found.
        """
        entry = self._strategy_params.get(strategy_name)
        if entry:
            return entry.get("current")
        # Try Supabase fallback
        return self._load_params_from_supabase(strategy_name)

    def get_param_history(self, strategy_name: str) -> List[Dict[str, Any]]:
        """Return the history of parameter updates for a strategy."""
        entry = self._strategy_params.get(strategy_name, {})
        return entry.get("history", [])

    def list_strategies_with_params(self) -> List[str]:
        """Return names of all strategies that have stored parameters."""
        return list(self._strategy_params.keys())

    # ------------------------------------------------------------------
    # Supabase persistence
    # ------------------------------------------------------------------

    def _persist(self, table: str, entry: Dict[str, Any]) -> None:
        if self._supabase is None:
            return
        try:
            self._supabase.table(table).upsert(entry).execute()
        except Exception as exc:
            logger.warning("Supabase persist failed [%s]: %s", table, exc)

    def _load_params_from_supabase(self, strategy_name: str) -> Optional[Dict[str, Any]]:
        if self._supabase is None:
            return None
        try:
            resp = (
                self._supabase.table("long_term_strategy_params")
                .select("*")
                .eq("strategy_name", strategy_name)
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )
            if resp.data:
                return resp.data[0].get("params")
        except Exception as exc:
            logger.warning("Supabase param load failed: %s", exc)
        return None

    def load_from_supabase(self) -> None:
        """Warm up local stores from Supabase."""
        if self._supabase is None:
            return
        try:
            # Market truths
            resp = self._supabase.table("long_term_market_truths").select("*").execute()
            if resp.data:
                self._market_truths = resp.data
                logger.info("Loaded %d market truths from Supabase", len(resp.data))

            # Strategy params
            resp2 = self._supabase.table("long_term_strategy_params").select("*").execute()
            if resp2.data:
                for row in resp2.data:
                    name = row.get("strategy_name", "")
                    if name:
                        self._strategy_params[name] = row
                logger.info("Loaded %d strategy param sets from Supabase", len(resp2.data))
        except Exception as exc:
            logger.warning("Supabase long-term load failed: %s", exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _seed_truths(self) -> None:
        """Pre-populate with common market truths if not already loaded."""
        for truth in _SEED_MARKET_TRUTHS:
            # Avoid duplicating seeds if already loaded from Supabase
            existing = self._find_similar_truth(truth["fact"], truth["market"])
            if not existing:
                entry = {
                    **truth,
                    "source": "seed",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "metadata": {},
                }
                self._market_truths.append(entry)

    def _find_similar_truth(self, fact: str, market: str) -> Optional[Dict[str, Any]]:
        """Find an existing truth with similar fact text using substring match."""
        fact_lower = fact.lower().strip()
        for t in self._market_truths:
            existing_lower = t.get("fact", "").lower().strip()
            # Simple similarity: matching first 40 chars
            if (
                existing_lower[:40] == fact_lower[:40]
                and t.get("market") == market
            ):
                return t
        return None

    def __repr__(self) -> str:
        return (
            f"<LongTermMemory truths={len(self._market_truths)} "
            f"strategies={len(self._strategy_params)}>"
        )
