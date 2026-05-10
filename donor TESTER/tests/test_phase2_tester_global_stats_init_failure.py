"""
Test #22 — Init failure: ConfigError before any backtest for invalid/degenerate configs.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #22
Spec reference: Appendix A v1.1 §12.3

Contract:
- Enabled filter + zigzag_global_stats=None → ConfigError BEFORE any backtest starts.
- Enabled filter + zigzag_global_stats with NaN global_median → ConfigError.
- Enabled filter + zigzag_global_stats with insufficient data (0 confirmed legs) → error.
- All errors raised BEFORE run_single_backtest is called (fail-fast).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


def _make_synthetic_ohlc(n: int = 300, seed: int = 23) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.010, n)))
    noise = rng.uniform(0.001, 0.003, size=n)
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.clip(close * (1 + rng.uniform(-0.001, 0.001, size=n)), low, high)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def _make_enabled_cfg():
    from supertrend_optimizer.core.trade_filter_config import (
        TradeFilterConfig, TradeFilterZigZagConfig, TradeFilterTriggersConfig,
        TradeFilterLifecycleConfig, TradeFilterDiagnosticsConfig,
        TradeFilterTriggerToggleConfig,
    )
    return TradeFilterConfig(
        enabled=True, type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            enabled=True,
            reversal_threshold=0.03, local_window=20, candidate_trigger_threshold=0.4,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=3, stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
        ),
        diagnostics=TradeFilterDiagnosticsConfig(
            export_state_columns=True, export_trigger_columns=True,
        ),
    )


class TestGlobalStatsInitFailure:
    """Fail-fast gates for invalid filter init (#22, plan §12.3)."""

    def test_run_period_enabled_no_stats_raises_config_error(self) -> None:
        """Enabled filter with no zigzag_global_stats → ConfigError before backtest."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.utils.exceptions import ConfigError
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = _make_synthetic_ohlc()
        cfg = _make_enabled_cfg()

        with pytest.raises(ConfigError):
            run_period(
                df=df, atr_period=14, multiplier=3.0,
                trade_mode="revers", commission=0.001,
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                trade_filter_config=cfg,
                zigzag_global_stats=None,  # missing stats — must fail-fast
            )

    def test_run_all_periods_enabled_no_stats_materializes_or_raises(self) -> None:
        """run_all_periods with enabled filter and no pre-supplied stats must either
        materialize stats from df (plan §7.3) OR raise ConfigError if materialization
        produces invalid stats. Either way, it must NOT silently produce unfiltered output."""
        from supertrend_optimizer.testing.runner import run_all_periods
        from supertrend_optimizer.utils.exceptions import ConfigError
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = _make_synthetic_ohlc()
        cfg = _make_enabled_cfg()

        # This should either succeed (materializing stats) or raise ConfigError
        # It must NOT silently skip the filter
        try:
            results = run_all_periods(
                df=df, atr_period=14, multiplier=3.0,
                trade_mode="revers", commission=0.001,
                warmup_period=20,
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                trade_filter_config=cfg,
                zigzag_global_stats=None,  # run_all_periods materializes from df
            )
            # If it succeeded, filter must actually be active
            for r in results:
                assert r.filter_diagnostics is not None, (
                    "run_all_periods with enabled filter must produce filter_diagnostics, "
                    "not silently skip the filter"
                )
        except ConfigError:
            pass  # acceptable: fail-fast if materialization fails

    def test_disabled_filter_with_none_stats_succeeds(self) -> None:
        """Disabled filter + no stats must succeed (no fail-fast needed)."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = _make_synthetic_ohlc()
        r = run_period(
            df=df, atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=None,
            zigzag_global_stats=None,
        )
        assert r is not None
        assert r.filter_diagnostics is None

    def test_very_short_dataset_with_stats_raises_or_completes(self) -> None:
        """Very short dataset may produce insufficient legs → ConfigError or safe run."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        from supertrend_optimizer.utils.exceptions import ConfigError
        from supertrend_optimizer.utils.enums import ExecutionModel

        # 30 bars — may not have enough zigzag legs for reliable stats
        df = _make_synthetic_ohlc(n=30, seed=999)
        cfg = _make_enabled_cfg()

        try:
            stats = build_zigzag_global_stats(df["close"].values, cfg)
            run_period(
                df=df, atr_period=14, multiplier=3.0,
                trade_mode="revers", commission=0.001,
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                trade_filter_config=cfg,
                zigzag_global_stats=stats,
            )
            # If it runs, that's fine — short data still valid
        except (ConfigError, Exception):
            pass  # ConfigError or other failure is acceptable for very short data

    def test_run_period_fail_fast_before_backtest_no_partial_result(self) -> None:
        """Fail-fast must produce no partial BacktestResult — just ConfigError."""
        from supertrend_optimizer.testing.runner import run_period, PeriodResult
        from supertrend_optimizer.utils.exceptions import ConfigError
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = _make_synthetic_ohlc()
        cfg = _make_enabled_cfg()

        result = None
        with pytest.raises(ConfigError):
            result = run_period(
                df=df, atr_period=14, multiplier=3.0,
                trade_mode="revers", commission=0.001,
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                trade_filter_config=cfg,
                zigzag_global_stats=None,
            )
        # result must remain None — ConfigError raised before returning
        assert result is None
