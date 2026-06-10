"""Multi-agent system for Xianyu commerce.

OpenClaw is the user-facing orchestrator. These are the specialist agents it
can route to directly.
"""
from .after_sales_agent import AfterSalesAgent
from .customer_service import CustomerServiceAgent
from .listing import ListingAgent
from .discovery import DiscoveryAgent
from .order import OrderAgent
from .github_agent import GitHubAgent
from .analytics import AnalyticsAgent
from .resale_research import ResaleResearchAgent
from .resale_risk import ResaleRiskAgent
from .resale_pricing import ResalePricingAgent
from .sourcing import SourcingAgent
from .strategy import StrategyAgent

__all__ = [
    "AfterSalesAgent",
    "CustomerServiceAgent",
    "ListingAgent",
    "DiscoveryAgent",
    "OrderAgent",
    "GitHubAgent",
    "AnalyticsAgent",
    "ResaleResearchAgent",
    "ResaleRiskAgent",
    "ResalePricingAgent",
    "SourcingAgent",
    "StrategyAgent",
]


def create_all_agents():
    """Instantiate all agents."""
    return {
        "customer_service": CustomerServiceAgent(),
        "listing": ListingAgent(),
        "discovery": DiscoveryAgent(),
        "order": OrderAgent(),
        "github": GitHubAgent(),
        "analytics": AnalyticsAgent(),
        "resale_research": ResaleResearchAgent(),
        "resale_risk": ResaleRiskAgent(),
        "resale_pricing": ResalePricingAgent(),
        # New agents for physical product dropshipping
        "sourcing": SourcingAgent(),
        "strategy": StrategyAgent(),
        "after_sales": AfterSalesAgent(),
    }
