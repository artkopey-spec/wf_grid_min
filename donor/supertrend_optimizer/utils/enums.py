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
    """Execution model controlling PnL calculation and trade timing.

    Only OPEN_TO_OPEN is supported.

    CLOSE_TO_CLOSE was removed because it contained an inherent look-ahead
    bias: the SuperTrend signal at bar t is derived from close[t], and
    executing at the same bar's close price requires knowing close[t] before
    the bar has finished — which is impossible in live trading.  Any backtest
    using CLOSE_TO_CLOSE produced artificially inflated performance.
    """
    OPEN_TO_OPEN = "open_to_open"


class AnnualizationBasis(str, Enum):
    """Basis for annualizing returns (calendar days vs trading days)."""
    CALENDAR = "calendar"  # 365.25 days/year (crypto, forex 24/7)
    TRADING = "trading"    # 252 trading days/year (stocks, futures)


# Market → default annualization_basis mapping.
#
# FOREX default is TRADING (252) rather than CALENDAR because most FX
# data in backtests is sourced from broker feeds that follow a Mon–Fri
# schedule (24/5, not 24/7). Using CALENDAR on 5-day data would
# underestimate periods_per_year by ~30 %.
#
# If you are working with true spot FX data that includes weekends, or
# with a synthetic 24/7 FX feed, override this by setting
# annualization_basis: "calendar" explicitly in your config.
MARKET_DEFAULT_ANNUALIZATION: dict[MarketType, AnnualizationBasis] = {
    MarketType.STOCKS:  AnnualizationBasis.TRADING,
    MarketType.CRYPTO:  AnnualizationBasis.CALENDAR,
    MarketType.FUTURES: AnnualizationBasis.TRADING,
    MarketType.FOREX:   AnnualizationBasis.TRADING,   # see note above
}

