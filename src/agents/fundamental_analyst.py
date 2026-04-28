# src/agents/fundamental_analyst.py
"""Fundamental Analyst Agent — market-specific fundamental analysis."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base_agent import AgentDecision, AgentOutput, BaseAgent
from ..llm.prompt_templates import AGENT_DECISION_SCHEMA, format_news

logger = logging.getLogger(__name__)

FUNDAMENTAL_SYSTEM_PROMPT = """You are the Fundamental Analyst for NEXUS ALPHA, a multi-market AI trading system.

Your role is to assess the fundamental value and macro environment for the asset under review.
You provide an objective fundamental view — not biased bull or bear — but grounded in data.

Your analysis must be market-specific:

CRYPTO markets:
- Network metrics: active addresses, transaction volume, hash rate, NVT ratio
- Token economics: inflation rate, staking yield, circulating supply changes
- Development activity: GitHub commits, protocol upgrades, TVL trends
- Regulatory environment: recent regulatory actions, exchange listings/delistings
- Institutional adoption: ETF flows, corporate treasury adoption

FOREX markets:
- Central bank policy: interest rate differentials, forward guidance
- Economic data: GDP growth, inflation (CPI/PPI), employment (NFP, unemployment)
- Trade balance, current account, fiscal deficit
- Political risk, geopolitical events
- Currency strength index vs peers

COMMODITY markets:
- Supply/demand balance: production data, inventory levels, seasonal patterns
- OPEC decisions (oil), crop reports (agricultural), mining output (metals)
- Geopolitical supply risk
- Industrial demand outlook
- Dollar strength impact

STOCKS (Indian):
- Valuation: P/E vs sector, P/B, EV/EBITDA
- Profitability: ROE, ROCE, profit margins, revenue growth
- Promoter holding % and changes (key Indian market indicator)
- FII/DII flows
- Sectoral tailwinds/headwinds
- GST data, PMI, IIP as macro indicators

STOCKS (US):
- Valuation: P/E vs S&P 500, PEG, EV/Sales
- Free cash flow yield
- Revenue/EPS growth rate and guidance
- Competitive moat, market share trends
- Fed policy impact on sector
- Options market positioning (PCR, gamma exposure)

Output format: You MUST respond with valid JSON matching the schema provided.
Your confidence should reflect how clear and strong the fundamental signal is."""


class FundamentalAnalystAgent(BaseAgent):
    """Provides market-specific fundamental analysis."""

    def __init__(self, llm_ensemble, market: str = "crypto") -> None:
        super().__init__(
            role="fundamental_analyst",
            llm_ensemble=llm_ensemble,
            system_prompt=FUNDAMENTAL_SYSTEM_PROMPT,
            market=market,
        )

    async def analyze(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> AgentOutput:
        """Analyse fundamental data for the given symbol."""
        symbol = market_data.get("symbol", "UNKNOWN")
        news = market_data.get("news", [])
        fundamental = market_data.get("fundamental", {})
        macro = market_data.get("macro", {})
        onchain = market_data.get("onchain", {})

        user_prompt = self._build_prompt(
            symbol=symbol,
            news=news,
            fundamental=fundamental,
            macro=macro,
            onchain=onchain,
            context=context,
        )

        parsed = await self._query_llm(user_prompt)
        output = self._build_output(
            parsed,
            data_used=self._build_data_used(market_data),
        )

        self._record_prediction(output.decision, output.confidence)
        logger.info(
            "[FundamentalAnalyst] %s → %s (%.2f confidence)",
            symbol, output.decision.value, output.confidence,
        )
        return output

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        symbol: str,
        news: List[Dict[str, Any]],
        fundamental: Dict[str, Any],
        macro: Dict[str, Any],
        onchain: Dict[str, Any],
        context: Dict[str, Any],
    ) -> str:
        market_section = self._build_market_section(fundamental, macro, onchain)

        prompt = f"""FUNDAMENTAL ANALYST ASSESSMENT
Symbol: {symbol} | Market: {self.market.upper()}

{market_section}

RECENT NEWS & EVENTS:
{format_news(news)}

MACRO ENVIRONMENT:
  Global Risk Sentiment: {macro.get('risk_sentiment', 'N/A')}
  DXY (USD Index): {macro.get('dxy', 'N/A')}
  10Y Treasury Yield: {macro.get('us10y', 'N/A')}%
  VIX: {macro.get('vix', 'N/A')}
  Recent Central Bank Actions: {macro.get('cb_actions', 'N/A')}

YOUR PERFORMANCE:
{self._format_recent_performance()}

TASK: Provide a fundamental assessment of {symbol}.
Focus on VALUE and MOMENTUM from a fundamental perspective.
Weight recent news events appropriately for their market impact.

{AGENT_DECISION_SCHEMA}"""

        return prompt

    def _build_market_section(
        self,
        fundamental: Dict[str, Any],
        macro: Dict[str, Any],
        onchain: Dict[str, Any],
    ) -> str:
        """Build the market-specific fundamental data section."""
        if self.market == "crypto":
            return self._crypto_section(fundamental, onchain)
        elif self.market == "forex":
            return self._forex_section(fundamental, macro)
        elif self.market == "commodity":
            return self._commodity_section(fundamental, macro)
        elif self.market == "stocks_in":
            return self._stocks_in_section(fundamental)
        elif self.market == "stocks_us":
            return self._stocks_us_section(fundamental)
        else:
            return f"FUNDAMENTAL DATA:\n{fundamental}"

    def _crypto_section(self, f: Dict, onchain: Dict) -> str:
        return f"""CRYPTO NETWORK METRICS:
  Active Addresses (24h): {f.get('active_addresses', 'N/A')}
  Transaction Volume    : {f.get('tx_volume', 'N/A')}
  Hash Rate             : {f.get('hash_rate', 'N/A')}
  NVT Ratio             : {f.get('nvt', 'N/A')} (>100 = expensive, <50 = cheap)
  Market Cap / Realized : {f.get('mvrv', 'N/A')} (MVRV, >3.7 = overvalued)
  Circulating Supply    : {f.get('circulating_supply', 'N/A')}
  Inflation Rate        : {f.get('inflation_rate', 'N/A')}%
  TVL (DeFi)            : {f.get('tvl', 'N/A')}
  Developer Activity    : {f.get('dev_activity', 'N/A')}
  Exchange Reserves     : {onchain.get('exchange_reserves', 'N/A')}
  Institutional Flows   : {f.get('institutional_flows', 'N/A')}"""

    def _forex_section(self, f: Dict, macro: Dict) -> str:
        return f"""FOREX FUNDAMENTAL DATA:
  Interest Rate Differential: {f.get('rate_differential', 'N/A')}%
  Base Country GDP Growth   : {f.get('gdp_growth', 'N/A')}%
  CPI (Inflation)           : {f.get('cpi', 'N/A')}%
  Unemployment Rate         : {f.get('unemployment', 'N/A')}%
  Trade Balance             : {f.get('trade_balance', 'N/A')}
  Current Account           : {f.get('current_account', 'N/A')}
  Central Bank Bias         : {f.get('cb_bias', 'N/A')} (Hawkish/Dovish/Neutral)
  PMI Manufacturing         : {f.get('pmi_mfg', 'N/A')}
  PMI Services              : {f.get('pmi_services', 'N/A')}
  Political Risk Level      : {f.get('political_risk', 'LOW')}"""

    def _commodity_section(self, f: Dict, macro: Dict) -> str:
        return f"""COMMODITY FUNDAMENTAL DATA:
  Supply Surplus/Deficit : {f.get('supply_balance', 'N/A')}
  Inventory Level        : {f.get('inventory', 'N/A')}
  Inventory vs 5Y Avg    : {f.get('inventory_vs_avg', 'N/A')}%
  Production Change YoY  : {f.get('production_change', 'N/A')}%
  Demand Growth          : {f.get('demand_growth', 'N/A')}%
  OPEC Cut/Increase      : {f.get('opec_decision', 'N/A')}
  Seasonal Factor        : {f.get('seasonal', 'N/A')}
  Speculative Positioning: {f.get('cot_net', 'N/A')} (COT Net)
  USD Impact             : {f.get('usd_impact', 'N/A')}"""

    def _stocks_in_section(self, f: Dict) -> str:
        return f"""INDIAN STOCK FUNDAMENTALS:
  P/E Ratio          : {f.get('pe', 'N/A')} (Sector avg: {f.get('sector_pe', 'N/A')})
  P/B Ratio          : {f.get('pb', 'N/A')}
  ROE                : {f.get('roe', 'N/A')}%
  ROCE               : {f.get('roce', 'N/A')}%
  Revenue Growth YoY : {f.get('revenue_growth', 'N/A')}%
  Net Profit Growth  : {f.get('profit_growth', 'N/A')}%
  Promoter Holding   : {f.get('promoter_holding', 'N/A')}%
  Promoter Pledge    : {f.get('promoter_pledge', 'N/A')}% (high pledge = risk)
  FII Holding Change : {f.get('fii_change', 'N/A')}% QoQ
  DII Holding Change : {f.get('dii_change', 'N/A')}% QoQ
  Debt to Equity     : {f.get('de_ratio', 'N/A')}
  Dividend Yield     : {f.get('div_yield', 'N/A')}%
  Market Cap         : {f.get('market_cap', 'N/A')}
  52W High/Low       : {f.get('high_52w', 'N/A')} / {f.get('low_52w', 'N/A')}"""

    def _stocks_us_section(self, f: Dict) -> str:
        return f"""US STOCK FUNDAMENTALS:
  P/E Ratio            : {f.get('pe', 'N/A')} (S&P avg: {f.get('sp500_pe', 'N/A')})
  Forward P/E          : {f.get('forward_pe', 'N/A')}
  PEG Ratio            : {f.get('peg', 'N/A')}
  EV/EBITDA            : {f.get('ev_ebitda', 'N/A')}
  FCF Yield            : {f.get('fcf_yield', 'N/A')}%
  Revenue Growth YoY   : {f.get('revenue_growth', 'N/A')}%
  EPS Growth (FWD)     : {f.get('eps_growth_fwd', 'N/A')}%
  Gross Margin         : {f.get('gross_margin', 'N/A')}%
  Net Margin           : {f.get('net_margin', 'N/A')}%
  Return on Equity     : {f.get('roe', 'N/A')}%
  Debt/EBITDA          : {f.get('debt_ebitda', 'N/A')}x
  Buyback Yield        : {f.get('buyback_yield', 'N/A')}%
  Analyst Consensus    : {f.get('analyst_consensus', 'N/A')}
  Earnings Surprise    : {f.get('earnings_surprise', 'N/A')}%
  Options PCR          : {f.get('put_call_ratio', 'N/A')}"""

    @staticmethod
    def _build_data_used(market_data: Dict[str, Any]) -> List[str]:
        used = ["fundamental"]
        if market_data.get("news"):
            used.append("news")
        if market_data.get("macro"):
            used.append("macro")
        if market_data.get("onchain"):
            used.append("onchain")
        return used
