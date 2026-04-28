# src/signals/fusion_engine.py
"""Signal Fusion Engine — combines technical, LLM, sentiment, and on-chain signals."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .signal_types import FusedSignal, SignalDirection, SignalStrength, TradeSignal
from .technical_signals import TechnicalSignalGenerator
from .llm_signals import LLMSignalGenerator
from .sentiment_signals import SentimentSignalGenerator
from .onchain_signals import OnChainSignalGenerator

logger = logging.getLogger(__name__)

# Default configuration — can be overridden per market/symbol
DEFAULT_CONFIG: Dict[str, Any] = {
    "weights": {
        "technical": 0.35,
        "llm": 0.35,
        "sentiment": 0.20,
        "onchain": 0.10,
    },
    "non_crypto_onchain_redist": True,  # redistribute onchain weight to technical for non-crypto
    "ev_threshold": 0.0,               # minimum expected value to pass edge filter
    "min_confidence": 0.30,            # minimum confidence to generate signal
    "default_win_rate": 0.48,          # conservative prior win rate
    "mtf_confirm_required": False,      # require multi-TF confirmation for all signals
    "base_size_pct": 3.0,              # base position size %
}


class SignalFusionEngine:
    """Fuse signals from all sources into a single actionable FusedSignal.

    Fixes applied vs naive implementation:
    1. EV uses historical win rate from BrierTracker if available, else 48% prior
    2. Edge filter thresholds are config-driven
    3. Non-crypto markets redistribute the 10% on-chain weight to technical
    4. MTF confirmation properly validates direction consistency
    """

    def __init__(
        self,
        brier_tracker=None,           # BrierTracker for historical win rates
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._brier = brier_tracker
        self._config = {**DEFAULT_CONFIG, **(config or {})}

        self._tech_gen = TechnicalSignalGenerator()
        self._llm_gen = LLMSignalGenerator()
        self._sent_gen = SentimentSignalGenerator()
        self._onchain_gen = OnChainSignalGenerator()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fuse(
        self,
        symbol: str,
        market: str,
        timeframe: str,
        df,                         # pd.DataFrame with indicators
        debate_result=None,         # DebateResult | None
        agent_outputs=None,         # Dict[str, AgentOutput] | None
        news=None,                  # List[dict]
        social=None,                # Dict
        fear_greed: Optional[int] = None,
        onchain_data: Optional[Dict[str, Any]] = None,
        mtf_analysis=None,          # MTFAnalysis | None
        portfolio: Optional[Dict[str, Any]] = None,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit_1: Optional[float] = None,
        take_profit_2: Optional[float] = None,
        take_profit_3: Optional[float] = None,
    ) -> FusedSignal:
        """Fuse all signals into a single :class:`FusedSignal`.

        Parameters
        ----------
        symbol:
            Trading symbol.
        market:
            Market type: crypto | forex | commodity | stocks_in | stocks_us.
        timeframe:
            Primary analysis timeframe.
        df:
            OHLCV + indicator DataFrame.
        debate_result:
            Output of DebateEngine.run_debate().
        agent_outputs:
            Alternative to debate_result.
        news, social, fear_greed, onchain_data:
            Sentiment and on-chain inputs.
        mtf_analysis:
            MTFAnalysis from MultiTimeframeAnalyzer.
        portfolio:
            Current portfolio state.
        entry_price, stop_loss, take_profit_1/2/3:
            Pre-computed trade levels from technical analyst.
        """
        weights = self._get_weights(market)

        # --- Generate component signals ---
        tech_signal = self._tech_gen.generate(df, market, symbol)

        llm_signal = self._llm_gen.generate(
            market_data={},
            context={},
            market=market,
            debate_result=debate_result,
            agent_outputs=agent_outputs,
        )

        sent_signal = self._sent_gen.generate(
            news=news or [],
            social=social,
            fear_greed=fear_greed,
            market=market,
        )

        onchain_signal = self._onchain_gen.generate(
            symbol=symbol,
            market=market,
            onchain_data=onchain_data,
        )

        # --- Build FusedSignal ---
        fused = FusedSignal(
            symbol=symbol,
            market=market,
            timeframe=timeframe,
            technical_signal=tech_signal,
            llm_signal=llm_signal,
            sentiment_signal=sent_signal,
            onchain_signal=onchain_signal,
            technical_weight=weights["technical"],
            llm_weight=weights["llm"],
            sentiment_weight=weights["sentiment"],
            onchain_weight=weights["onchain"],
        )

        # --- Compute fused score ---
        fused.compute_fused_score()

        # --- Confidence: abs(score) × alignment factor ---
        alignment_factor = 1.0
        if mtf_analysis:
            mtf_score = getattr(mtf_analysis, "alignment_score", 0.5)
            fused.mtf_alignment_score = mtf_score
            alignment_factor = 0.5 + 0.5 * mtf_score  # 0.5–1.0

        fused.confidence = min(1.0, abs(fused.fused_score) * alignment_factor)

        # --- MTF confirmation ---
        fused.mtf_confirmed = self._check_mtf_confirmation(
            fused.fused_score, mtf_analysis
        )

        # --- Direction and strength ---
        if fused.fused_score > 0.05:
            fused.direction = SignalDirection.LONG
        elif fused.fused_score < -0.05:
            fused.direction = SignalDirection.SHORT
        else:
            fused.direction = SignalDirection.NEUTRAL

        fused.strength = SignalStrength.from_numeric(fused.fused_score)

        # --- Expected Value ---
        win_rate = self._get_win_rate(symbol, market)
        fused.win_rate = win_rate

        # Determine R:R from provided levels
        if entry_price and stop_loss and take_profit_1:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit_1 - entry_price)
            fused.risk_reward = reward / risk if risk > 0 else 2.0
        else:
            fused.risk_reward = 2.0  # conservative default

        fused.compute_expected_value()

        logger.info(
            "FusedSignal %s/%s: tech=%.2f llm=%.2f sent=%.2f onchain=%.2f → "
            "fused=%.2f conf=%.2f direction=%s EV=%.3f",
            symbol, market, tech_signal, llm_signal, sent_signal, onchain_signal,
            fused.fused_score, fused.confidence, fused.direction.value,
            fused.expected_value,
        )

        return fused

    def to_trade_signal(
        self,
        fused: FusedSignal,
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float = 0.0,
        take_profit_3: float = 0.0,
        agent_reasoning: str = "",
        key_factors=None,
    ) -> TradeSignal:
        """Convert a :class:`FusedSignal` to an actionable :class:`TradeSignal`."""
        risk = abs(entry_price - stop_loss)
        reward_1 = abs(take_profit_1 - entry_price)
        rr = reward_1 / risk if risk > 0 else 0.0

        base_size = float(self._config.get("base_size_pct", 3.0))
        size_pct = base_size * fused.confidence

        return TradeSignal(
            symbol=fused.symbol,
            market=fused.market,
            timeframe=fused.timeframe,
            direction=fused.direction,
            strength=fused.strength,
            confidence=fused.confidence,
            entry=entry_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            take_profit_3=take_profit_3,
            risk_reward=rr,
            size_pct=size_pct,
            strategy="fusion_engine",
            reasoning=agent_reasoning,
            key_factors=list(key_factors or []),
            fused_signal=fused,
            expected_value=fused.expected_value,
            timestamp=datetime.now(tz=timezone.utc),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_weights(self, market: str) -> Dict[str, float]:
        """Get fusion weights, redistributing on-chain weight for non-crypto."""
        base = dict(self._config.get("weights", DEFAULT_CONFIG["weights"]))

        is_crypto = market.lower() in ("crypto", "defi", "nft")
        redist = self._config.get("non_crypto_onchain_redist", True)

        if not is_crypto and redist:
            onchain_w = base.get("onchain", 0.10)
            base["onchain"] = 0.0
            base["technical"] = base.get("technical", 0.35) + onchain_w

        # Normalise weights to sum to 1.0
        total = sum(base.values())
        if total > 0:
            base = {k: v / total for k, v in base.items()}

        return base

    def _get_win_rate(self, symbol: str, market: str) -> float:
        """Get historical win rate from BrierTracker, or use conservative prior."""
        if self._brier is None:
            return float(self._config.get("default_win_rate", 0.48))

        # Get the best model's Brier score and convert to implied win rate
        all_scores = self._brier.compute_all_scores()
        market_scores = {
            model: scores.get(market, None)
            for model, scores in all_scores.items()
        }
        valid_scores = {m: s for m, s in market_scores.items() if s is not None}

        if not valid_scores:
            return float(self._config.get("default_win_rate", 0.48))

        # Best Brier score → implied accuracy
        best_brier = min(valid_scores.values())
        # Brier score: BS = (f-o)^2, for perfect calibration: BS = p*(1-p)
        # Rough inverse: win_rate ≈ 0.5 + sqrt(0.25 - best_brier)
        import math
        discriminant = max(0, 0.25 - best_brier)
        implied_wr = 0.5 + math.sqrt(discriminant)
        # Blend with prior (don't over-trust small samples)
        prior = float(self._config.get("default_win_rate", 0.48))
        blended = 0.7 * implied_wr + 0.3 * prior
        return max(0.3, min(0.7, blended))

    def _check_mtf_confirmation(
        self, fused_score: float, mtf_analysis
    ) -> bool:
        """Return True if the higher timeframes confirm the signal direction."""
        if mtf_analysis is None:
            return False

        bias = getattr(mtf_analysis, "overall_bias", "NEUTRAL")
        daily = getattr(mtf_analysis, "trend_daily", "N/A")
        weekly = getattr(mtf_analysis, "trend_weekly", "N/A")

        signal_bull = fused_score > 0
        signal_bear = fused_score < 0

        daily_confirms = (
            (signal_bull and daily in ("UP",)) or
            (signal_bear and daily in ("DOWN",))
        )
        weekly_confirms = (
            (signal_bull and weekly in ("UP",)) or
            (signal_bear and weekly in ("DOWN",)) or
            weekly == "N/A"  # no weekly data = don't penalise
        )

        return daily_confirms and weekly_confirms
