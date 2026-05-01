"""
Tests for annualization_factor="auto" correctly propagating
annualization_basis and market from config in optimizer CLI and walk_forward.

Two primary cases:
  1. auto + market=stocks  -> TRADING  (bars_per_active_day_median * 252)
  2. auto + no market/basis -> CALENDAR (bars_per_calendar_day_mean * 365.25)
Plus bonus: explicit_basis overrides market.
"""

import pandas as pd
import pytest

from supertrend_optimizer.data.timeframe import (
    detect_timeframe,
    resolve_periods_per_year_from_config,
)
from supertrend_optimizer.utils.enums import MarketType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_intraday_stocks_index(
    n_days: int = 20,
    bars_per_day: int = 9,
    start: str = "2022-01-03",
    hour_start: int = 9,
) -> pd.DatetimeIndex:
    """
    Build a deterministic 60-min-like DatetimeIndex for stock trading.

    Only weekdays, ``bars_per_day`` equally-spaced hours per day.
    The timestamps within each day are fixed (no randomness), so median
    bars-per-active-day is exactly ``bars_per_day``.
    """
    business_days = pd.bdate_range(start=start, periods=n_days, freq="B")
    timestamps = []
    for day in business_days:
        for h in range(bars_per_day):
            timestamps.append(
                pd.Timestamp(
                    year=day.year,
                    month=day.month,
                    day=day.day,
                    hour=hour_start + h,
                )
            )
    return pd.DatetimeIndex(sorted(timestamps))


# ---------------------------------------------------------------------------
# Tests for resolve_periods_per_year_from_config directly
# (pure unit — no CLI / WF wiring needed)
# ---------------------------------------------------------------------------

class TestAnnualizationPropagation:

    def test_auto_stocks_uses_trading_basis(self):
        """
        auto + market=STOCKS -> TRADING: periods = bars_per_active_day_median * 252.

        With a perfectly uniform intraday index (9 bars / active day),
        the median is exactly 9.0, so expected = 9 * 252 = 2268.0.
        """
        n_days = 20
        bars_per_day = 9
        index = _make_intraday_stocks_index(n_days=n_days, bars_per_day=bars_per_day)

        periods = resolve_periods_per_year_from_config(
            config_value="auto",
            index=index,
            explicit_basis=None,
            market=MarketType.STOCKS,
        )

        expected = bars_per_day * 252.0
        assert abs(periods - expected) < 1e-6, (
            f"Expected {expected} (TRADING basis), got {periods}"
        )

    def test_auto_without_market_defaults_calendar(self):
        """
        auto + market=None + explicit_basis=None -> CALENDAR:
        periods = bars_per_calendar_day_mean * 365.25.
        """
        n_days = 20
        bars_per_day = 9
        index = _make_intraday_stocks_index(n_days=n_days, bars_per_day=bars_per_day)

        periods = resolve_periods_per_year_from_config(
            config_value="auto",
            index=index,
            explicit_basis=None,
            market=None,
        )

        stats = detect_timeframe(index)
        expected = stats.bars_per_calendar_day_mean * 365.25
        assert abs(periods - expected) < 1e-9, (
            f"Expected {expected} (CALENDAR basis), got {periods}"
        )

    def test_explicit_basis_overrides_market(self):
        """
        explicit_basis="calendar" overrides market=STOCKS -> CALENDAR is used.
        Result must match bars_per_calendar_day_mean * 365.25.
        """
        index = _make_intraday_stocks_index(n_days=20, bars_per_day=9)

        periods = resolve_periods_per_year_from_config(
            config_value="auto",
            index=index,
            explicit_basis="calendar",
            market=MarketType.STOCKS,
        )

        stats = detect_timeframe(index)
        expected = stats.bars_per_calendar_day_mean * 365.25
        assert abs(periods - expected) < 1e-9, (
            f"explicit_basis='calendar' should override market=STOCKS; "
            f"expected {expected}, got {periods}"
        )

    def test_explicit_basis_trading_on_calendar_index(self):
        """
        explicit_basis="trading" + market=None -> TRADING even on a calendar-day index.
        """
        # Daily data, every calendar day (like crypto)
        index = pd.date_range(start="2022-01-01", periods=30, freq="D")

        periods = resolve_periods_per_year_from_config(
            config_value="auto",
            index=index,
            explicit_basis="trading",
            market=None,
        )

        stats = detect_timeframe(index)
        expected = stats.bars_per_active_day_median * 252.0
        assert abs(periods - expected) < 1e-9, (
            f"explicit_basis='trading' should use TRADING; "
            f"expected {expected}, got {periods}"
        )

    def test_integer_factor_ignores_market_and_basis(self):
        """
        Integer annualization_factor must bypass auto-detection entirely,
        regardless of market or explicit_basis.
        """
        index = _make_intraday_stocks_index(n_days=20, bars_per_day=9)

        periods = resolve_periods_per_year_from_config(
            config_value=252,
            index=index,
            explicit_basis="calendar",
            market=MarketType.CRYPTO,
        )

        assert periods == 252.0, (
            f"Integer factor=252 should be returned as-is; got {periods}"
        )


# ---------------------------------------------------------------------------
# Integration-style tests: verify optimizer.py config reading
# (we call the same resolve helper with the same args the fixed optimizer uses)
# ---------------------------------------------------------------------------

class TestOptimizerConfigReading:
    """
    Simulate how the fixed optimizer.py reads market/annualization_basis
    from bt_cfg and passes them to resolve_periods_per_year_from_config.
    """

    def _simulate_optimizer_resolve(self, bt_cfg: dict, index: pd.DatetimeIndex) -> float:
        """Mimics the fixed code in cli/optimizer.py."""
        ann_factor = bt_cfg.get("annualization_factor", 252)
        explicit_basis = bt_cfg.get("annualization_basis", None)
        market_str = bt_cfg.get("market", None)
        market_enum = MarketType(market_str) if market_str is not None else None
        return resolve_periods_per_year_from_config(
            ann_factor,
            index,
            explicit_basis=explicit_basis,
            market=market_enum,
        )

    def test_config_with_market_stocks_auto_uses_trading(self):
        """config: annualization_factor=auto, market=stocks -> TRADING."""
        index = _make_intraday_stocks_index(n_days=20, bars_per_day=9)
        bt_cfg = {
            "annualization_factor": "auto",
            "market": "stocks",
        }
        periods = self._simulate_optimizer_resolve(bt_cfg, index)
        assert abs(periods - 9 * 252.0) < 1e-6

    def test_config_with_basis_trading_overrides_no_market(self):
        """config: annualization_factor=auto, annualization_basis=trading -> TRADING."""
        index = _make_intraday_stocks_index(n_days=20, bars_per_day=9)
        bt_cfg = {
            "annualization_factor": "auto",
            "annualization_basis": "trading",
        }
        periods = self._simulate_optimizer_resolve(bt_cfg, index)
        assert abs(periods - 9 * 252.0) < 1e-6

    def test_config_no_market_no_basis_defaults_calendar(self):
        """config: annualization_factor=auto, no market/basis -> CALENDAR."""
        index = _make_intraday_stocks_index(n_days=20, bars_per_day=9)
        bt_cfg = {"annualization_factor": "auto"}
        periods = self._simulate_optimizer_resolve(bt_cfg, index)

        stats = detect_timeframe(index)
        expected = stats.bars_per_calendar_day_mean * 365.25
        assert abs(periods - expected) < 1e-9

    def test_config_integer_factor_always_wins(self):
        """config: annualization_factor=252 (int) -> always 252.0."""
        index = _make_intraday_stocks_index(n_days=20, bars_per_day=9)
        bt_cfg = {
            "annualization_factor": 252,
            "market": "crypto",
            "annualization_basis": "calendar",
        }
        periods = self._simulate_optimizer_resolve(bt_cfg, index)
        assert periods == 252.0
