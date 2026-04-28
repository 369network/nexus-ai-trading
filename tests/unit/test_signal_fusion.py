"""
NEXUS ALPHA - Unit Tests: Signal Fusion Engine
Tests weighted vote aggregation, edge detection, and multi-timeframe confirmation.
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Inline signal fusion implementation (mirrors src/strategies/signal_fusion.py)
# ---------------------------------------------------------------------------

AGENT_WEIGHTS = {
    "TrendFollower":     0.20,
    "MeanReversion":     0.15,
    "BreakoutHunter":    0.15,
    "RiskSentinel":      0.20,
    "MacroAnalyst":      0.10,
    "PatternRecognizer": 0.10,
    "VolumeProfiler":    0.10,
}

LONG_THRESHOLD  = +0.25
SHORT_THRESHOLD = -0.25

ALL_AGENTS = list(AGENT_WEIGHTS.keys())


@dataclass
class AgentVote:
    agent: str
    vote: str           # LONG / SHORT / NEUTRAL
    confidence: float   # 0.0 – 1.0


@dataclass
class CandidateSignal:
    symbol: str = "BTCUSDT"
    market: str = "crypto"
    timeframe: str = "1h"
    direction: str = "LONG"
    entry_price: float = 50_000.0
    stop_loss: float = 48_500.0
    take_profit: float = 54_000.0
    confidence: float = 0.7
    strategy_name: str = "TrendMomentum"
    agent_votes: List[AgentVote] = field(default_factory=list)


@dataclass
class FusedSignal:
    direction: str          # LONG / SHORT / NEUTRAL
    fusion_score: float     # Weighted vote score
    agent_scores: Dict[str, float] = field(default_factory=dict)
    agent_votes: List[AgentVote] = field(default_factory=list)
    multi_tf_confirmed: bool = False
    higher_tf_bias: Optional[str] = None


@dataclass
class EdgeResult:
    edge_detected: bool
    expected_value: float
    kelly_fraction: float = 0.0
    reason: str = ""


def compute_fusion_score(votes: List[AgentVote]) -> float:
    """
    Compute weighted fusion score.
    LONG votes contribute +confidence, SHORT contribute -confidence.
    """
    total_score = 0.0
    for vote in votes:
        weight = AGENT_WEIGHTS.get(vote.agent, 0.0)
        if vote.vote == "LONG":
            total_score += weight * vote.confidence
        elif vote.vote == "SHORT":
            total_score -= weight * vote.confidence
        # NEUTRAL: 0 contribution

    return total_score


def fuse_signals(
    candidate: CandidateSignal,
    votes: List[AgentVote],
    higher_tf_direction: Optional[str] = None,
) -> FusedSignal:
    """Core fusion logic."""
    score = compute_fusion_score(votes)

    if score >= LONG_THRESHOLD:
        direction = "LONG"
    elif score <= SHORT_THRESHOLD:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # Multi-timeframe confirmation
    multi_tf_confirmed = True
    if higher_tf_direction and higher_tf_direction != "NEUTRAL":
        if higher_tf_direction != direction:
            multi_tf_confirmed = False

    agent_scores = {
        v.agent: (v.confidence if v.vote == "LONG" else
                  -v.confidence if v.vote == "SHORT" else 0.0)
        for v in votes
    }

    return FusedSignal(
        direction=direction,
        fusion_score=score,
        agent_scores=agent_scores,
        agent_votes=votes,
        multi_tf_confirmed=multi_tf_confirmed,
        higher_tf_bias=higher_tf_direction,
    )


def evaluate_edge(
    fused: FusedSignal,
    win_rate_estimate: float = 0.55,
    avg_win_pct: float = 0.03,
    avg_loss_pct: float = 0.015,
    min_ev_threshold: float = 0.002,
) -> EdgeResult:
    """
    Estimate expected value and Kelly fraction.
    EV = win_rate * avg_win - (1 - win_rate) * avg_loss
    """
    if fused.direction == "NEUTRAL":
        return EdgeResult(edge_detected=False, expected_value=0.0, reason="NEUTRAL signal")

    ev = win_rate_estimate * avg_win_pct - (1 - win_rate_estimate) * avg_loss_pct

    kelly = (win_rate_estimate / avg_loss_pct) - ((1 - win_rate_estimate) / avg_win_pct)
    kelly = max(kelly, 0.0)

    edge = ev >= min_ev_threshold
    return EdgeResult(
        edge_detected=edge,
        expected_value=ev,
        kelly_fraction=kelly,
        reason="" if edge else f"EV {ev:.4f} < min {min_ev_threshold}",
    )


# ---------------------------------------------------------------------------
# Helpers to build vote lists
# ---------------------------------------------------------------------------

def all_votes(direction: str, confidence: float = 0.8) -> List[AgentVote]:
    """All agents vote the same direction."""
    return [AgentVote(agent=a, vote=direction, confidence=confidence) for a in ALL_AGENTS]


def mixed_votes(
    long_agents: List[str],
    short_agents: List[str],
    neutral_agents: List[str],
    confidence: float = 0.75,
) -> List[AgentVote]:
    votes = []
    for a in long_agents:
        votes.append(AgentVote(a, "LONG", confidence))
    for a in short_agents:
        votes.append(AgentVote(a, "SHORT", confidence))
    for a in neutral_agents:
        votes.append(AgentVote(a, "NEUTRAL", confidence))
    return votes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def candidate():
    return CandidateSignal()


@pytest.fixture
def all_long_votes():
    return all_votes("LONG", confidence=0.80)


@pytest.fixture
def all_short_votes():
    return all_votes("SHORT", confidence=0.80)


@pytest.fixture
def all_neutral_votes():
    return all_votes("NEUTRAL", confidence=0.80)


# ---------------------------------------------------------------------------
# Fusion Score Tests
# ---------------------------------------------------------------------------

class TestFusionScore:
    def test_all_positive_scores_produce_long_signal(self, candidate, all_long_votes):
        """All agents LONG → fused signal LONG."""
        fused = fuse_signals(candidate, all_long_votes)
        assert fused.direction == "LONG", f"Expected LONG, got {fused.direction}"
        assert fused.fusion_score > LONG_THRESHOLD

    def test_all_negative_scores_produce_short_signal(self, candidate, all_short_votes):
        """All agents SHORT → fused signal SHORT."""
        fused = fuse_signals(candidate, all_short_votes)
        assert fused.direction == "SHORT", f"Expected SHORT, got {fused.direction}"
        assert fused.fusion_score < SHORT_THRESHOLD

    def test_all_neutral_produces_neutral_signal(self, candidate, all_neutral_votes):
        """All agents NEUTRAL → fused signal NEUTRAL."""
        fused = fuse_signals(candidate, all_neutral_votes)
        assert fused.direction == "NEUTRAL"
        assert abs(fused.fusion_score) < 1e-6

    def test_mixed_near_zero_produces_neutral(self, candidate):
        """Balanced LONG and SHORT votes → NEUTRAL."""
        # Half agents each way with same confidence
        votes = mixed_votes(
            long_agents=["TrendFollower", "BreakoutHunter", "MacroAnalyst"],
            short_agents=["MeanReversion", "RiskSentinel", "PatternRecognizer"],
            neutral_agents=["VolumeProfiler"],
            confidence=0.7,
        )
        fused = fuse_signals(candidate, votes)
        # Score should be near zero (balanced)
        assert abs(fused.fusion_score) < LONG_THRESHOLD or fused.direction == "NEUTRAL"

    def test_fusion_score_is_bounded(self, candidate):
        """Fusion score must be in [-1, 1] given confidence ≤ 1.0."""
        for direction in ["LONG", "SHORT"]:
            votes = all_votes(direction, confidence=1.0)
            fused = fuse_signals(candidate, votes)
            assert -1.1 <= fused.fusion_score <= 1.1

    def test_higher_confidence_gives_higher_score(self, candidate):
        """Higher agent confidence → higher absolute fusion score."""
        low_conf_votes = all_votes("LONG", confidence=0.5)
        high_conf_votes = all_votes("LONG", confidence=0.9)
        fused_low  = fuse_signals(candidate, low_conf_votes)
        fused_high = fuse_signals(candidate, high_conf_votes)
        assert fused_high.fusion_score > fused_low.fusion_score

    def test_risk_sentinel_higher_weight_matters(self, candidate):
        """RiskSentinel has weight 0.20 — its vote should move the score more."""
        # Only RiskSentinel votes LONG, all others NEUTRAL
        votes = [
            AgentVote("RiskSentinel",    "LONG",    0.9),
            AgentVote("TrendFollower",   "NEUTRAL", 0.0),
            AgentVote("MeanReversion",   "NEUTRAL", 0.0),
            AgentVote("BreakoutHunter",  "NEUTRAL", 0.0),
            AgentVote("MacroAnalyst",    "NEUTRAL", 0.0),
            AgentVote("PatternRecognizer","NEUTRAL",0.0),
            AgentVote("VolumeProfiler",  "NEUTRAL", 0.0),
        ]
        score = compute_fusion_score(votes)
        expected = 0.20 * 0.9  # weight × confidence
        assert abs(score - expected) < 1e-9


# ---------------------------------------------------------------------------
# Edge Filter Tests
# ---------------------------------------------------------------------------

class TestEdgeFilter:
    def test_ev_below_threshold_no_edge(self, candidate):
        """Low EV → edge_detected = False."""
        fused = fuse_signals(candidate, all_votes("LONG", 0.6))
        edge = evaluate_edge(
            fused,
            win_rate_estimate=0.40,  # Bad win rate
            avg_win_pct=0.01,
            avg_loss_pct=0.015,
            min_ev_threshold=0.002,
        )
        # EV = 0.40 * 0.01 - 0.60 * 0.015 = 0.004 - 0.009 = -0.005 → no edge
        assert edge.edge_detected is False
        assert edge.expected_value < 0

    def test_positive_ev_triggers_edge(self, candidate):
        """Positive EV → edge_detected = True."""
        fused = fuse_signals(candidate, all_votes("LONG", 0.8))
        edge = evaluate_edge(
            fused,
            win_rate_estimate=0.60,
            avg_win_pct=0.03,
            avg_loss_pct=0.015,
            min_ev_threshold=0.002,
        )
        # EV = 0.60 * 0.03 - 0.40 * 0.015 = 0.018 - 0.006 = 0.012 → edge
        assert edge.edge_detected is True
        assert edge.expected_value > 0.002

    def test_neutral_signal_has_no_edge(self, candidate, all_neutral_votes):
        """NEUTRAL fused signal → no edge (not tradeable)."""
        fused = fuse_signals(candidate, all_neutral_votes)
        edge = evaluate_edge(fused)
        assert edge.edge_detected is False
        assert "NEUTRAL" in edge.reason

    def test_expected_value_calculation(self, candidate):
        """EV = win_rate * avg_win - (1 - win_rate) * avg_loss."""
        fused = fuse_signals(candidate, all_votes("LONG", 0.8))
        wr, aw, al = 0.55, 0.03, 0.015
        expected_ev = wr * aw - (1 - wr) * al
        edge = evaluate_edge(fused, wr, aw, al, min_ev_threshold=0.001)
        assert abs(edge.expected_value - expected_ev) < 1e-9


# ---------------------------------------------------------------------------
# Multi-Timeframe Confirmation Tests
# ---------------------------------------------------------------------------

class TestMultiTimeframeConfirmation:
    def test_long_signal_confirmed_by_higher_tf_long(self, candidate, all_long_votes):
        """LONG signal + LONG higher TF → confirmed."""
        fused = fuse_signals(candidate, all_long_votes, higher_tf_direction="LONG")
        assert fused.multi_tf_confirmed is True

    def test_long_signal_not_confirmed_by_higher_tf_down(self, candidate, all_long_votes):
        """LONG signal + SHORT higher TF → NOT confirmed."""
        fused = fuse_signals(candidate, all_long_votes, higher_tf_direction="SHORT")
        assert fused.multi_tf_confirmed is False

    def test_short_signal_confirmed_by_higher_tf_short(self, candidate, all_short_votes):
        """SHORT signal + SHORT higher TF → confirmed."""
        fused = fuse_signals(candidate, all_short_votes, higher_tf_direction="SHORT")
        assert fused.multi_tf_confirmed is True

    def test_no_higher_tf_defaults_to_confirmed(self, candidate, all_long_votes):
        """Without higher TF data, multi_tf_confirmed = True (no contradiction)."""
        fused = fuse_signals(candidate, all_long_votes, higher_tf_direction=None)
        assert fused.multi_tf_confirmed is True

    def test_signal_vs_neutral_higher_tf_is_confirmed(self, candidate, all_long_votes):
        """Higher TF NEUTRAL doesn't contradict LONG signal."""
        fused = fuse_signals(candidate, all_long_votes, higher_tf_direction="NEUTRAL")
        assert fused.multi_tf_confirmed is True

    def test_higher_tf_stored_in_fused(self, candidate, all_long_votes):
        """Higher TF bias should be stored in the fused signal."""
        fused = fuse_signals(candidate, all_long_votes, higher_tf_direction="SHORT")
        assert fused.higher_tf_bias == "SHORT"

    def test_agent_scores_populated(self, candidate, all_long_votes):
        """agent_scores dict should contain all agents."""
        fused = fuse_signals(candidate, all_long_votes)
        assert len(fused.agent_scores) == len(ALL_AGENTS)
        for agent in ALL_AGENTS:
            assert agent in fused.agent_scores
