"""
Reinforcement Learning Trainer for NEXUS ALPHA.

Implements a simple DQN (Deep Q-Network) for learning trading decisions.

Architecture:
    - State vector: 25 features (indicators + portfolio state + sentiment)
    - Action space: {LONG, SHORT, HOLD} × size 0-100% → discretised to 9 actions
    - Reward: Sharpe-adjusted return - drawdown penalty - overtrading penalty
    - Network: 2-layer MLP, 64 hidden units

Note: Full PPO/SAC planned for v2. Feature flag: rl_training=False by default.
"""

from __future__ import annotations

import logging
import os
import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# DQN constants
STATE_DIM = 25
NUM_ACTIONS = 9       # 3 directions × 3 sizes: (0.25, 0.5, 1.0 of risk budget)
HIDDEN_DIM = 64
REPLAY_BUFFER_SIZE = 10_000
BATCH_SIZE = 64
GAMMA = 0.99          # Discount factor
LR = 1e-3             # Learning rate
EPSILON_START = 1.0   # Exploration
EPSILON_END = 0.05
EPSILON_DECAY = 0.995
TARGET_UPDATE_FREQ = 50  # Steps between target network updates

# Action encoding
ACTIONS: List[Tuple[str, float]] = [
    ("LONG", 0.25), ("LONG", 0.50), ("LONG", 1.0),
    ("SHORT", 0.25), ("SHORT", 0.50), ("SHORT", 1.0),
    ("HOLD", 0.0), ("HOLD", 0.25), ("HOLD", 0.50),
]

# Reward shaping constants
OVERTRADING_PENALTY = 0.001  # Per unnecessary trade
DRAWDOWN_PENALTY_SCALE = 2.0


@dataclass
class Experience:
    """Single experience tuple for replay buffer."""
    state: np.ndarray
    action: int
    reward: float
    next_state: np.ndarray
    done: bool


class SimpleLinearNetwork:
    """
    Lightweight 2-layer MLP implemented in pure NumPy.

    Used when PyTorch is not available. Suitable for inference
    and simplified training on small datasets.

    Architecture: Input(25) → ReLU(64) → ReLU(64) → Output(9)
    """

    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int) -> None:
        scale = np.sqrt(2.0 / input_dim)
        self.W1 = np.random.randn(input_dim, hidden_dim) * scale
        self.b1 = np.zeros(hidden_dim)
        self.W2 = np.random.randn(hidden_dim, hidden_dim) * scale
        self.b2 = np.zeros(hidden_dim)
        self.W3 = np.random.randn(hidden_dim, output_dim) * np.sqrt(2.0 / hidden_dim)
        self.b3 = np.zeros(output_dim)

    def forward(self, x: np.ndarray) -> np.ndarray:
        h1 = np.maximum(0, x @ self.W1 + self.b1)
        h2 = np.maximum(0, h1 @ self.W2 + self.b2)
        return h2 @ self.W3 + self.b3

    def copy(self) -> "SimpleLinearNetwork":
        """Create a deep copy (for target network)."""
        net = SimpleLinearNetwork(
            self.W1.shape[0], self.W1.shape[1], self.W3.shape[1]
        )
        net.W1 = self.W1.copy()
        net.b1 = self.b1.copy()
        net.W2 = self.W2.copy()
        net.b2 = self.b2.copy()
        net.W3 = self.W3.copy()
        net.b3 = self.b3.copy()
        return net

    def update(
        self,
        states: np.ndarray,    # (B, input_dim)
        targets: np.ndarray,   # (B, output_dim)
        lr: float = LR,
    ) -> float:
        """Vanilla SGD step with MSE loss. Returns loss."""
        # Forward
        h1 = np.maximum(0, states @ self.W1 + self.b1)
        h2 = np.maximum(0, h1 @ self.W2 + self.b2)
        out = h2 @ self.W3 + self.b3

        # Loss
        diff = out - targets
        loss = float(np.mean(diff ** 2))

        # Backprop (simplified)
        d_out = 2 * diff / len(states)
        d_W3 = h2.T @ d_out
        d_b3 = d_out.sum(axis=0)
        d_h2 = d_out @ self.W3.T * (h2 > 0)
        d_W2 = h1.T @ d_h2
        d_b2 = d_h2.sum(axis=0)
        d_h1 = d_h2 @ self.W2.T * (h1 > 0)
        d_W1 = states.T @ d_h1
        d_b1 = d_h1.sum(axis=0)

        # Gradient descent
        self.W3 -= lr * d_W3
        self.b3 -= lr * d_b3
        self.W2 -= lr * d_W2
        self.b2 -= lr * d_b2
        self.W1 -= lr * d_W1
        self.b1 -= lr * d_b1

        return loss


class RLTrainer:
    """
    DQN-based reinforcement learning trainer for trading decisions.

    Feature flag: rl_training must be enabled in config before this
    module actively trains. In disabled mode it still exposes predict()
    using a pre-loaded model.

    Parameters
    ----------
    model_dir : str
        Directory for saving/loading trained models.
    feature_flag : bool
        Enable training (default: False).
    """

    def __init__(
        self,
        model_dir: str = "./models/rl",
        feature_flag: bool = False,
    ) -> None:
        self._enabled = feature_flag
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)

        # Networks
        self._q_network = SimpleLinearNetwork(STATE_DIM, HIDDEN_DIM, NUM_ACTIONS)
        self._target_network = self._q_network.copy()

        # Training state
        self._replay_buffer: deque = deque(maxlen=REPLAY_BUFFER_SIZE)
        self._epsilon = EPSILON_START
        self._step_count = 0
        self._trained_steps = 0

        logger.info(
            "RLTrainer initialised | enabled=%s | state_dim=%d | actions=%d",
            feature_flag, STATE_DIM, NUM_ACTIONS,
        )

    # ------------------------------------------------------------------
    # State encoding
    # ------------------------------------------------------------------

    def build_state_vector(
        self,
        market_data: Dict[str, Any],
        portfolio_state: Dict[str, Any],
        sentiment: Dict[str, Any],
    ) -> np.ndarray:
        """
        Build 25-feature state vector for the DQN.

        Features (indices):
            0: RSI normalised
            1: MACD hist (tanh normalised)
            2: BB position (0=lower, 1=upper)
            3: ATR pct
            4: Volume ratio
            5: SMA trend alignment
            6: RSI slope
            7: Fear & Greed
            8-11: Regime one-hot (trending_up, trending_down, ranging, hi_vol)
            12-14: LLM consensus (bullish, neutral, bearish)
            15: Portfolio equity ratio (current / initial)
            16: Open positions count (normalised to 0-1 by max 10)
            17: Daily P&L (tanh)
            18: Max drawdown (trailing 5 days)
            19: Unrealised PnL ratio
            20: Funding rate (crypto)
            21: VIX normalised (if available)
            22: Hour of day (sin encoded)
            23: Hour of day (cos encoded)
            24: Days since last losing streak
        """
        ind = market_data.get("indicators", {})
        close = max(float(ind.get("close", 1.0)), 1e-9)
        atr = max(float(ind.get("atr", 1.0)), 1e-9)

        # RSI
        rsi = float(ind.get("rsi", 50.0)) / 100.0
        rsi_prev = float(ind.get("rsi_prev", ind.get("rsi", 50.0))) / 100.0
        rsi_slope = (rsi - rsi_prev) * 10.0

        # MACD
        macd_hist = np.tanh(float(ind.get("macd_hist", 0.0)) / atr)

        # Bollinger Band position
        bb_upper = float(ind.get("bb_upper", close * 1.02))
        bb_lower = float(ind.get("bb_lower", close * 0.98))
        bb_range = max(bb_upper - bb_lower, 1e-9)
        bb_pct = (close - bb_lower) / bb_range

        atr_pct = np.tanh(atr / close * 100)

        vol_ratio = min(float(ind.get("volume_ratio", 1.0)), 5.0) / 5.0

        sma_fast = float(ind.get("sma_fast", close))
        sma_slow = float(ind.get("sma_slow", close))
        trend_align = np.tanh((sma_fast - sma_slow) / max(sma_slow, 1e-9) * 20)

        fg = float(sentiment.get("fear_greed", 50)) / 100.0

        # Regime one-hot
        regime = market_data.get("regime", "ranging")
        regime_map = {"trending_up": 0, "trending_down": 1, "ranging": 2, "high_volatility": 3}
        r_idx = regime_map.get(regime, 2)
        regime_vec = np.zeros(4)
        regime_vec[r_idx] = 1.0

        # Sentiment 3-class
        consensus = sentiment.get("llm_consensus", "HOLD")
        sent_vec = np.zeros(3)
        if "BUY" in consensus:
            sent_vec[0] = 1.0
        elif "SELL" in consensus:
            sent_vec[2] = 1.0
        else:
            sent_vec[1] = 1.0

        # Portfolio features
        initial_eq = max(float(portfolio_state.get("initial_equity", 1.0)), 1.0)
        current_eq = float(portfolio_state.get("current_equity", initial_eq))
        eq_ratio = np.tanh((current_eq / initial_eq - 1.0) * 10)
        open_positions = min(int(portfolio_state.get("open_positions", 0)), 10) / 10.0
        daily_pnl = np.tanh(float(portfolio_state.get("daily_pnl_pct", 0.0)) * 20)
        max_dd = float(portfolio_state.get("trailing_drawdown", 0.0))
        unrealised = np.tanh(float(portfolio_state.get("unrealised_pnl_pct", 0.0)) * 20)

        # Additional features
        funding_rate = np.tanh(float(sentiment.get("funding_rate", 0.0)) * 10)
        vix = float(sentiment.get("vix", 20.0)) / 80.0  # Normalise 0-80 VIX

        # Time encoding
        now = datetime.now(timezone.utc)
        hour_sin = np.sin(2 * np.pi * now.hour / 24)
        hour_cos = np.cos(2 * np.pi * now.hour / 24)

        # Losing streak days (normalised)
        losing_streak = min(int(portfolio_state.get("days_since_losing_streak", 0)), 30) / 30.0

        state = np.array([
            rsi, float(macd_hist), float(bb_pct), float(atr_pct), float(vol_ratio),
            float(trend_align), float(rsi_slope), float(fg),
            *regime_vec,
            *sent_vec,
            float(eq_ratio), float(open_positions), float(daily_pnl),
            float(max_dd), float(unrealised),
            float(funding_rate), float(vix),
            float(hour_sin), float(hour_cos),
            float(losing_streak),
        ], dtype=np.float32)

        assert len(state) == STATE_DIM, f"State dim mismatch: {len(state)}"
        return state

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(
        self,
        symbol: str,
        market: str,
        historical_data: Any,
        days: int = 90,
        epochs: int = 5,
    ) -> Dict[str, float]:
        """
        Train the DQN on historical data via experience replay.

        Parameters
        ----------
        symbol : str
            Trading instrument.
        market : str
            Market identifier.
        historical_data : DataFrame-like
            At minimum: 'close', 'high', 'low', 'volume'.
        days : int
            How many days of data to use.
        epochs : int
            Number of passes over the data.

        Returns
        -------
        dict
            Training metrics: {total_reward, avg_loss, episodes}.
        """
        if not self._enabled:
            logger.info("RLTrainer: rl_training=False; skipping training for %s", symbol)
            return {"total_reward": 0.0, "avg_loss": 0.0, "episodes": 0}

        logger.info("RLTrainer training %s/%s | days=%d | epochs=%d", symbol, market, days, epochs)

        try:
            closes = np.array(historical_data["close"], dtype=float)
        except (KeyError, TypeError):
            logger.error("RLTrainer: cannot extract 'close' from historical_data")
            return {"total_reward": 0.0, "avg_loss": 0.0, "episodes": 0}

        n = len(closes)
        if n < 30:
            logger.warning("Insufficient data for RL training: %d bars", n)
            return {"total_reward": 0.0, "avg_loss": 0.0, "episodes": 0}

        total_reward = 0.0
        loss_sum = 0.0
        loss_count = 0
        episodes = 0

        for epoch in range(epochs):
            # Simulate episode over historical data
            portfolio = {"cash": 1.0, "position": 0.0, "entry": 0.0, "equity": 1.0}
            prev_equity = 1.0

            for i in range(20, n - 1):
                state = self._make_training_state(closes, i)
                action_idx = self._select_action(state)
                action_name, size = ACTIONS[action_idx]

                # Execute action
                next_equity, reward = self._simulate_step(
                    closes, i, action_name, size, portfolio, prev_equity
                )
                next_state = self._make_training_state(closes, i + 1)
                done = i == n - 2

                # Store experience
                self._replay_buffer.append(
                    Experience(state, action_idx, reward, next_state, done)
                )
                total_reward += reward
                prev_equity = next_equity
                self._step_count += 1

                # Train on batch
                if len(self._replay_buffer) >= BATCH_SIZE:
                    loss = self._train_step()
                    loss_sum += loss
                    loss_count += 1

                # Update target network
                if self._step_count % TARGET_UPDATE_FREQ == 0:
                    self._target_network = self._q_network.copy()

            # Decay epsilon
            self._epsilon = max(EPSILON_END, self._epsilon * EPSILON_DECAY)
            episodes += 1
            logger.debug(
                "RL epoch %d/%d | epsilon=%.3f | replay_size=%d",
                epoch + 1, epochs, self._epsilon, len(self._replay_buffer),
            )

        self._trained_steps += epochs * n
        avg_loss = loss_sum / max(loss_count, 1)
        logger.info(
            "RL training complete | total_reward=%.3f | avg_loss=%.5f | epsilon=%.3f",
            total_reward, avg_loss, self._epsilon,
        )
        return {
            "total_reward": float(total_reward),
            "avg_loss": float(avg_loss),
            "episodes": int(episodes),
        }

    def predict(self, state_vector: np.ndarray) -> Tuple[str, float]:
        """
        Select action from current Q-network (greedy, no exploration).

        Parameters
        ----------
        state_vector : np.ndarray
            25-dimensional state vector.

        Returns
        -------
        Tuple[action_name, confidence]
            action_name: 'LONG', 'SHORT', or 'HOLD'
            confidence: 0.0–1.0 (softmax of Q-values normalised)
        """
        q_vals = self._q_network.forward(state_vector.reshape(1, -1)).flatten()
        # Softmax for confidence
        exp_q = np.exp(q_vals - np.max(q_vals))
        probs = exp_q / exp_q.sum()

        best_idx = int(np.argmax(q_vals))
        action_name, size = ACTIONS[best_idx]
        confidence = float(probs[best_idx])
        logger.debug("RL predict: %s (size=%.2f) confidence=%.3f", action_name, size, confidence)
        return action_name, confidence

    # ------------------------------------------------------------------
    # Model persistence
    # ------------------------------------------------------------------

    def save_model(self, symbol: str, market: str) -> str:
        """Save model weights to disk. Returns file path."""
        path = self._model_dir / f"{market}_{symbol}_dqn.npz"
        np.savez(
            str(path),
            W1=self._q_network.W1, b1=self._q_network.b1,
            W2=self._q_network.W2, b2=self._q_network.b2,
            W3=self._q_network.W3, b3=self._q_network.b3,
            epsilon=np.array([self._epsilon]),
            step_count=np.array([self._step_count]),
        )
        logger.info("RL model saved: %s", path)
        return str(path)

    def load_model(self, symbol: str, market: str) -> bool:
        """Load model weights from disk. Returns True on success."""
        path = self._model_dir / f"{market}_{symbol}_dqn.npz"
        if not path.exists():
            logger.warning("No RL model found at %s", path)
            return False
        try:
            data = np.load(str(path))
            self._q_network.W1 = data["W1"]
            self._q_network.b1 = data["b1"]
            self._q_network.W2 = data["W2"]
            self._q_network.b2 = data["b2"]
            self._q_network.W3 = data["W3"]
            self._q_network.b3 = data["b3"]
            self._epsilon = float(data["epsilon"][0])
            self._step_count = int(data["step_count"][0])
            self._target_network = self._q_network.copy()
            logger.info("RL model loaded from %s | epsilon=%.3f", path, self._epsilon)
            return True
        except Exception as exc:
            logger.error("Failed to load RL model: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _select_action(self, state: np.ndarray) -> int:
        """Epsilon-greedy action selection."""
        if random.random() < self._epsilon:
            return random.randint(0, NUM_ACTIONS - 1)
        q_vals = self._q_network.forward(state.reshape(1, -1)).flatten()
        return int(np.argmax(q_vals))

    def _train_step(self) -> float:
        """Sample batch and perform one gradient update."""
        batch = random.sample(self._replay_buffer, BATCH_SIZE)
        states = np.stack([e.state for e in batch])
        actions = np.array([e.action for e in batch])
        rewards = np.array([e.reward for e in batch])
        next_states = np.stack([e.next_state for e in batch])
        dones = np.array([e.done for e in batch], dtype=float)

        # Current Q-values
        q_curr = self._q_network.forward(states)

        # Target Q-values
        q_next = self._target_network.forward(next_states)
        q_target = q_curr.copy()
        for i in range(BATCH_SIZE):
            td_target = rewards[i] + GAMMA * np.max(q_next[i]) * (1 - dones[i])
            q_target[i, actions[i]] = td_target

        return self._q_network.update(states, q_target, lr=LR)

    @staticmethod
    def _make_training_state(closes: np.ndarray, idx: int) -> np.ndarray:
        """Build a simplified state vector from close prices for training."""
        period = min(14, idx)
        window = closes[max(0, idx - period):idx + 1]

        rsi_val = 50.0
        if len(window) > 1:
            deltas = np.diff(window)
            g = np.mean(np.where(deltas > 0, deltas, 0))
            l_ = np.mean(np.where(deltas < 0, -deltas, 0))
            rsi_val = 100 - 100 / (1 + g / max(l_, 1e-9))

        close = closes[idx]
        sma20 = np.mean(closes[max(0, idx - 20):idx + 1])
        sma50 = np.mean(closes[max(0, idx - 50):idx + 1])
        atr_approx = np.std(closes[max(0, idx - 14):idx + 1]) * 1.5

        state = np.zeros(STATE_DIM, dtype=np.float32)
        state[0] = rsi_val / 100.0
        state[5] = np.tanh((sma20 - sma50) / max(sma50, 1e-9) * 20)
        state[3] = np.tanh(atr_approx / max(close, 1e-9) * 100)
        state[16] = float(idx) / len(closes)
        return state

    @staticmethod
    def _simulate_step(
        closes: np.ndarray,
        idx: int,
        action: str,
        size: float,
        portfolio: Dict[str, float],
        prev_equity: float,
    ) -> Tuple[float, float]:
        """Simplified step simulation for RL training."""
        next_close = closes[idx + 1]
        curr_close = closes[idx]
        ret = (next_close - curr_close) / max(curr_close, 1e-9)

        if action == "LONG":
            step_pnl = ret * size
        elif action == "SHORT":
            step_pnl = -ret * size
        else:
            step_pnl = 0.0

        new_equity = prev_equity + step_pnl

        # Reward: Sharpe-inspired (step return) - overtrading penalty
        reward = step_pnl - OVERTRADING_PENALTY if action != "HOLD" else step_pnl
        # Drawdown penalty
        if new_equity < prev_equity:
            reward -= abs(step_pnl) * DRAWDOWN_PENALTY_SCALE

        portfolio["equity"] = new_equity
        return new_equity, reward
