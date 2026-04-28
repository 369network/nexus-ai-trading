# src/agents/__init__.py
from .base_agent import BaseAgent, AgentDecision, AgentOutput
from .bull_researcher import BullResearcherAgent
from .bear_researcher import BearResearcherAgent
from .fundamental_analyst import FundamentalAnalystAgent
from .technical_analyst import TechnicalAnalystAgent
from .sentiment_analyst import SentimentAnalystAgent
from .risk_manager import RiskManagerAgent
from .portfolio_manager import PortfolioManagerAgent
from .debate_engine import DebateEngine, DebateResult
from .agent_registry import AgentRegistry

__all__ = [
    "BaseAgent", "AgentDecision", "AgentOutput",
    "BullResearcherAgent",
    "BearResearcherAgent",
    "FundamentalAnalystAgent",
    "TechnicalAnalystAgent",
    "SentimentAnalystAgent",
    "RiskManagerAgent",
    "PortfolioManagerAgent",
    "DebateEngine", "DebateResult",
    "AgentRegistry",
]
