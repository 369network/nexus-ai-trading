# src/signals/signal_types.py
"""Core signal dataclasses and enumerations for NEXUS ALPHA."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SignalDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class SignalStrength(Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    SLIGHT_BUY = "SLIGHT_BUY"
    NEUTRAL = "NEUTRAL"
    SLIGHT_SELL = "SLIGHT_SELL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"

    @property
    def numeric(self) -> float:
        mapping = {
            "STRONG_BUY": 1.0,
            "BUY": 0.6,
            "SLIGHT_BUY": 0.3,
            "NEUTRAL": 0.0,
            "SLIGHT_SELL": -0.3,
            "SELL": -0.6,
            "STRONG_SELL": -1.0,
        }
        return mapping[self.value]

    @classmethod
    def from_numeric(cls, value: float) -> "SignalStrength":
        """Convert a numeric signal (-1 to +1) to SignalStrength."""
        if value >= 0.75:
            return cls.STRONG_BUY
        elif value >= 0.4:
            return cls.BUY
        elif value >= 0.1:
            return cls.SLIGHT_BUY
        elif value <= -0.75:
            return cls.STRONG_SELL
        elif value <= -0.4:
            return cls.SELL
        elif value <= -0.1:
            return cls.SLIGHT_SELL
        else:
            return cls.NEUTRAL


# ---------------------------------------------------------------------------
# Fused signal (intermediate, before edge filter)
# ---------------------------------------------------------------------------

@dataclass
class FusedSignal:
    """Weighted fusion of technical, LLM, sentiment, and on-chain signals."""

    symbol: str
    market: str
    timeframe: str

    # Component signals (-1.0 to +1.0)
    technical_signal: float = 0.0
    llm_signal: float = 0.0
    sentiment_signal: float = 0.0
    onchain_signal: float = 0.0

    # Weights applied during fusion
    technical_weight: float = 0.35
    llm_weight: float = 0.35
    sentiment_weight: float = 0.20
    onchain_weight: float = 0.10

    # Fused output
    fused_score: float = 0.0       # weighted average (-1 to +1)
    confidence: float = 0.0        # 0.0–1.0
    direction: SignalDirection = SignalDirection.NEUTRAL
    strength: SignalStrength = SignalStrength.NEUTRAL

    # Expected Value calculation
    win_rate: float = 0.48         # historical win rate (default conservative prior)
    risk_reward: float = 2.0       # target R:R
    expected_value: float = 0.0   # EV = win_rate * rr - (1-win_rate)

    # Multi-TF confirmation
    mtf_confirmed: bool = False
    mtf_alignment_score: float = 0.0

    # Metadata
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def compute_fused_score(self) -> float:
        """Compute and store the weighted fusion score."""
        total_weight = (
            self.technical_weight
            + self.llm_weight
            + self.sentiment_weight
            + self.onchain_weight
        )
        if total_weight == 0:
            self.fused_score = 0.0
        else:
            self.fused_score = (
                self.technical_signal * self.technical_weight
                + self.llm_signal * self.llm_weight
                + self.sentiment_signal * self.sentiment_weight
                + self.onchain_signal * self.onchain_weight
            ) / total_weight
        return self.fused_score

    def compute_expected_value(self) -> float:
        """Compute expected value: EV = wr * rr - (1-wr) * 1.0"""
        self.expected_value = (
            self.win_rate * self.risk_reward - (1 - self.win_rate) * 1.0
        )
        return self.expected_value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "timeframe": self.timeframe,
            "technical_signal": self.technical_signal,
            "llm_signal": self.llm_signal,
            "sentiment_signal": self.sentiment_signal,
            "onchain_signal": self.onchain_signal,
            "fused_score": self.fused_score,
            "confidence": self.confidence,
            "direction": self.direction.value,
            "strength": self.strength.value,
            "win_rate": self.win_rate,
            "risk_reward": self.risk_reward,
            "expected_value": self.expected_value,
            "mtf_confirmed": self.mtf_confirmed,
            "mtf_alignment_score": self.mtf_alignment_score,
            "timestamp": self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# Trade signal (output of edge filter, action-ready)
# ---------------------------------------------------------------------------

@dataclass
class TradeSignal:
    """Complete, action-ready trade signal.

    This is the final output of the signal pipeline, ready to be sent to
    the execution layer.
    """

    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    market: str = ""               # crypto | forex | commodity | stocks_in | stocks_us
    timeframe: str = "1H"

    direction: SignalDirection = SignalDirection.NEUTRAL
    strength: SignalStrength = SignalStrength.NEUTRAL
    confidence: float = 0.0        # 0.0–1.0

    # Trade levels
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    take_profit_3: float = 0.0
    risk_reward: float = 0.0

    # Position sizing
    size_pct: float = 0.0          # % of portfolio to allocate

    # Source metadata
    strategy: str = "debate_engine"
    reasoning: str = ""
    key_factors: List[str] = field(default_factory=list)
    fused_signal: Optional[FusedSignal] = field(default=None, repr=False)

    # Quality metrics
    expected_value: float = 0.0
    brier_confidence: float = 0.5  # model calibration confidence

    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    expires_at: Optional[datetime] = None

    # Status tracking
    status: str = "active"         # active | executed | expired | cancelled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "market": self.market,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "strength": self.strength.value,
            "confidence": self.confidence,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "take_profit_3": self.take_profit_3,
            "risk_reward": self.risk_reward,
            "size_pct": self.size_pct,
            "strategy": self.strategy,
            "reasoning": self.reasoning,
            "key_factors": self.key_factors,
            "expected_value": self.expected_value,
            "brier_confidence": self.brier_confidence,
            "timestamp": self.timestamp.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status,
        }

    @property
    def is_actionable(self) -> bool:
        """Return True if signal has valid entry and stop levels."""
        return (
            self.direction != SignalDirection.NEUTRAL
            and self.entry > 0
            and self.stop_loss > 0
            and self.confidence >= 0.3
            and self.expected_value > 0
        )
