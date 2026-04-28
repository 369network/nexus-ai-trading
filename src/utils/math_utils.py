"""
NEXUS ALPHA — Mathematical Utilities
======================================
Statistical and financial math functions used across strategies,
risk management, and performance analytics.
"""

from __future__ import annotations

import math
from typing import Sequence


# ---------------------------------------------------------------------------
# Kelly Criterion
# ---------------------------------------------------------------------------


def kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> tuple[float, float]:
    """
    Calculate the full Kelly fraction and the quarter-Kelly (recommended safe fraction).

    The Kelly criterion determines the optimal bet/position size to maximize
    long-run geometric growth of capital.

    Formula: f* = W/L - (1-W) / (W/L)
    Simplified: f* = (b * p - q) / b
    where b = avg_win/avg_loss, p = win_rate, q = 1 - win_rate

    Args:
        win_rate: Probability of a winning trade (0.0 to 1.0).
        avg_win: Average profit on winning trades (positive number, e.g. 0.05 = 5%).
        avg_loss: Average loss on losing trades (positive number, e.g. 0.02 = 2%).

    Returns:
        Tuple of (full_kelly, quarter_kelly), both as decimals (e.g. 0.25 = 25%).
        Returns (0.0, 0.0) if Kelly is negative (expected value is negative).

    Raises:
        ValueError: If inputs are out of valid range.
    """
    if not 0.0 <= win_rate <= 1.0:
        raise ValueError(f"win_rate must be between 0 and 1, got {win_rate}")
    if avg_win <= 0:
        raise ValueError(f"avg_win must be positive, got {avg_win}")
    if avg_loss <= 0:
        raise ValueError(f"avg_loss must be positive, got {avg_loss}")

    loss_rate = 1.0 - win_rate
    b = avg_win / avg_loss  # Win/loss ratio

    # Full Kelly formula: (b * p - q) / b
    full_kelly = (b * win_rate - loss_rate) / b

    # Clamp: negative Kelly means negative edge, return 0
    full_kelly = max(0.0, full_kelly)

    # Quarter Kelly is the commonly recommended conservative fraction
    quarter_kelly = full_kelly * 0.25

    return full_kelly, quarter_kelly


# ---------------------------------------------------------------------------
# Brier Score (probability calibration)
# ---------------------------------------------------------------------------


def brier_score(
    forecasts: Sequence[float],
    outcomes: Sequence[float],
) -> float:
    """
    Calculate the Brier Score for probability forecasts.

    The Brier Score measures the accuracy of probabilistic predictions.
    Lower is better. A perfect forecaster scores 0.0, a random one scores 0.25.

    Formula: BS = (1/N) * Σ(forecast_i - outcome_i)²

    Args:
        forecasts: Sequence of predicted probabilities (0.0 to 1.0).
        outcomes: Sequence of actual binary outcomes (0 or 1).

    Returns:
        Brier Score (float between 0.0 and 1.0).

    Raises:
        ValueError: If sequences have different lengths or are empty.
    """
    if len(forecasts) != len(outcomes):
        raise ValueError(
            f"forecasts and outcomes must have equal length: "
            f"{len(forecasts)} != {len(outcomes)}"
        )
    if not forecasts:
        raise ValueError("forecasts and outcomes must not be empty")

    n = len(forecasts)
    score = sum((f - o) ** 2 for f, o in zip(forecasts, outcomes)) / n
    return score


# ---------------------------------------------------------------------------
# Sharpe Ratio
# ---------------------------------------------------------------------------


def sharpe_ratio(
    returns: Sequence[float],
    risk_free: float = 0.02,
    periods_per_year: int = 252,
) -> float:
    """
    Calculate the annualized Sharpe Ratio.

    Args:
        returns: Sequence of periodic returns (e.g., daily returns as decimals).
        risk_free: Annual risk-free rate (default 2% = 0.02).
        periods_per_year: Number of periods per year (252 for daily, 52 for weekly,
                          365 for calendar days, 8760 for hourly).

    Returns:
        Annualized Sharpe Ratio. Returns 0.0 if standard deviation is zero.

    Raises:
        ValueError: If returns sequence is empty.
    """
    if not returns:
        raise ValueError("returns sequence must not be empty")

    n = len(returns)
    mean_return = sum(returns) / n

    # Population standard deviation of returns
    variance = sum((r - mean_return) ** 2 for r in returns) / n
    std_dev = math.sqrt(variance)

    if std_dev == 0.0:
        return 0.0

    # Periodic risk-free rate
    periodic_rf = risk_free / periods_per_year

    # Annualize: multiply by sqrt(periods_per_year)
    sharpe = (mean_return - periodic_rf) / std_dev * math.sqrt(periods_per_year)
    return sharpe


def sortino_ratio(
    returns: Sequence[float],
    risk_free: float = 0.02,
    periods_per_year: int = 252,
) -> float:
    """
    Calculate the annualized Sortino Ratio.
    Like Sharpe but only penalizes downside volatility.

    Args:
        returns: Sequence of periodic returns.
        risk_free: Annual risk-free rate.
        periods_per_year: Periods per year for annualization.

    Returns:
        Annualized Sortino Ratio. Returns 0.0 if downside std is zero.
    """
    if not returns:
        raise ValueError("returns sequence must not be empty")

    n = len(returns)
    mean_return = sum(returns) / n
    periodic_rf = risk_free / periods_per_year

    # Downside deviation (only negative deviations count)
    downside_returns = [r for r in returns if r < periodic_rf]
    if not downside_returns:
        return float("inf")  # No downside — perfect

    downside_variance = sum((r - periodic_rf) ** 2 for r in downside_returns) / n
    downside_std = math.sqrt(downside_variance)

    if downside_std == 0.0:
        return 0.0

    sortino = (mean_return - periodic_rf) / downside_std * math.sqrt(periods_per_year)
    return sortino


# ---------------------------------------------------------------------------
# Maximum Drawdown
# ---------------------------------------------------------------------------


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """
    Calculate the maximum drawdown of an equity curve.

    Maximum drawdown is the largest peak-to-trough decline, expressed as
    a percentage of the peak value.

    Args:
        equity_curve: Sequence of portfolio equity values over time.

    Returns:
        Maximum drawdown as a percentage (e.g., -25.3 means 25.3% drawdown).
        Returns 0.0 if equity never falls below the starting value.

    Raises:
        ValueError: If equity_curve has fewer than 2 values.
    """
    if len(equity_curve) < 2:
        raise ValueError("equity_curve must have at least 2 values")

    peak = equity_curve[0]
    max_dd = 0.0

    for value in equity_curve:
        if value > peak:
            peak = value
        drawdown = (value - peak) / peak * 100  # Negative percentage
        if drawdown < max_dd:
            max_dd = drawdown

    return max_dd  # Will be 0.0 or negative


def calmar_ratio(
    annual_return: float,
    equity_curve: Sequence[float],
) -> float:
    """
    Calculate the Calmar Ratio (annual return / max drawdown).

    Args:
        annual_return: Annualized return as a percentage (e.g., 15.0 for 15%).
        equity_curve: Equity curve values.

    Returns:
        Calmar Ratio. Returns 0.0 if max drawdown is zero.
    """
    dd = abs(max_drawdown(equity_curve))
    if dd == 0.0:
        return 0.0
    return annual_return / dd


# ---------------------------------------------------------------------------
# Profit Factor
# ---------------------------------------------------------------------------


def profit_factor(
    wins: Sequence[float],
    losses: Sequence[float],
) -> float:
    """
    Calculate the Profit Factor.

    Profit Factor = Gross Profit / Gross Loss.
    Values > 1.0 indicate a profitable system.

    Args:
        wins: Sequence of winning trade profits (positive values).
        losses: Sequence of losing trade losses (positive values, or negative
                will be converted via abs()).

    Returns:
        Profit factor (float). Returns float('inf') if no losses.
        Returns 0.0 if no wins.

    Raises:
        ValueError: If both sequences are empty.
    """
    if not wins and not losses:
        raise ValueError("Both wins and losses are empty")

    gross_profit = sum(abs(w) for w in wins)
    gross_loss = sum(abs(l) for l in losses)

    if gross_loss == 0.0:
        return float("inf")  # All wins, no losses
    if gross_profit == 0.0:
        return 0.0  # No wins at all

    return gross_profit / gross_loss


# ---------------------------------------------------------------------------
# ATR-Based Position Sizing
# ---------------------------------------------------------------------------


def atr_position_size(
    capital: float,
    atr: float,
    atr_pct: float,
    risk_pct: float,
) -> float:
    """
    Calculate position size in units based on ATR volatility and risk budget.

    The formula allocates a fixed percentage of capital to a trade, where the
    stop-loss distance is defined as a multiple of ATR.

    Formula:
        dollar_risk = capital * risk_pct / 100
        stop_distance = atr * atr_pct
        position_size = dollar_risk / stop_distance

    Args:
        capital: Total tradeable capital in account (USD or quote currency).
        atr: Average True Range of the instrument (in price units).
        atr_pct: How many ATRs to set the stop at (e.g., 1.5 means stop = 1.5 * ATR).
        risk_pct: Percentage of capital to risk on this trade (e.g., 1.0 = 1%).

    Returns:
        Position size in units (e.g., number of shares, BTC, etc.).
        Returns 0.0 if stop distance is zero or inputs are invalid.

    Raises:
        ValueError: If any input is negative or capital/atr is zero.

    Example:
        capital = 100_000  # $100k account
        atr = 50.0         # ETH price moves ~$50 per ATR
        atr_pct = 1.5      # Stop at 1.5x ATR = $75 away
        risk_pct = 1.0     # Risk 1% = $1,000
        → size = 1000 / 75 = 13.33 units (ETH)
    """
    if capital <= 0:
        raise ValueError(f"capital must be positive, got {capital}")
    if atr < 0:
        raise ValueError(f"atr must be non-negative, got {atr}")
    if atr_pct <= 0:
        raise ValueError(f"atr_pct must be positive, got {atr_pct}")
    if risk_pct <= 0:
        raise ValueError(f"risk_pct must be positive, got {risk_pct}")

    dollar_risk = capital * (risk_pct / 100.0)
    stop_distance = atr * atr_pct

    if stop_distance == 0.0:
        return 0.0

    position_size = dollar_risk / stop_distance
    return position_size


def fixed_fractional_size(
    capital: float,
    risk_pct: float,
    stop_distance: float,
) -> float:
    """
    Position size using fixed fractional method.

    Args:
        capital: Tradeable capital.
        risk_pct: Percentage of capital to risk (e.g., 1.0 = 1%).
        stop_distance: Distance from entry to stop in price units.

    Returns:
        Position size in units.
    """
    if capital <= 0 or stop_distance <= 0 or risk_pct <= 0:
        return 0.0
    dollar_risk = capital * (risk_pct / 100.0)
    return dollar_risk / stop_distance


# ---------------------------------------------------------------------------
# Expectancy
# ---------------------------------------------------------------------------


def expectancy(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """
    Calculate trade expectancy (expected value per trade as % of risk).

    Formula: E = (win_rate * avg_win) - (loss_rate * avg_loss)

    Args:
        win_rate: Fraction of trades that are winners (0.0–1.0).
        avg_win: Average win as a percentage of risked amount.
        avg_loss: Average loss as a percentage of risked amount.

    Returns:
        Expectancy per trade as a percentage. Positive = profitable edge.
    """
    loss_rate = 1.0 - win_rate
    return (win_rate * avg_win) - (loss_rate * avg_loss)


def risk_reward_ratio(entry: float, stop: float, target: float) -> float:
    """
    Calculate risk/reward ratio for a trade.

    Args:
        entry: Entry price.
        stop: Stop-loss price.
        target: Take-profit price.

    Returns:
        R:R ratio (e.g., 2.0 means reward is 2x the risk).
    """
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk == 0:
        return 0.0
    return reward / risk
