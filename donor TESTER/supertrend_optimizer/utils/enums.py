"""
Enums for SuperTrend Optimizer.

This module defines type-safe enums for market types, execution models,
and annualization bases.
"""

from enum import Enum


class MarketType(str, Enum):
    """Market type for semantic metadata and validation hints."""
    STOCKS = "stocks"
    CRYPTO = "crypto"
    FUTURES = "futures"
    FOREX = "forex"


class ExecutionModel(str, Enum):
    """Execution model controlling PnL calculation and trade timing."""
    OPEN_TO_OPEN = "open_to_open"
    CLOSE_TO_CLOSE = "close_to_close"


class AnnualizationBasis(str, Enum):
    """Basis for annualizing returns (calendar days vs trading days)."""
    CALENDAR = "calendar"  # 365.25 days/year (crypto, forex 24/7)
    TRADING = "trading"    # 252 trading days/year (stocks, futures)


# Market → default annualization_basis mapping
MARKET_DEFAULT_ANNUALIZATION: dict[MarketType, AnnualizationBasis] = {
    MarketType.STOCKS:  AnnualizationBasis.TRADING,
    MarketType.CRYPTO:  AnnualizationBasis.CALENDAR,
    MarketType.FUTURES: AnnualizationBasis.TRADING,
    MarketType.FOREX:   AnnualizationBasis.TRADING,
}

