"""
End-to-end integration tests for ZigZag filter pipeline (plan §3.7, Fix 1).

Verifies that the complete path:
  run_single_backtest → BacktestResult.filter_diagnostics → build_signal_events

works without errors for both ``zigzag`` and ``zigzag_and_volume`` modes and
that ``filter_diagnostics`` carries the required ``allow_entry`` /
``filtered_reason`` keys (plan §3.7 contract).

These tests would have caught the BLOCKER identified in the audit:
  - ``run.py`` not storing ``allow_entry``/``filtered_reason`` in
    ``filter_diagnostics``, causing ``build_signal_events`` to raise
    ``ValueError`` for any zz mode.
"""
from __future__ import annotations

import numpy as np
import pytest

from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.testing.signal_events import build_signal_events
from supertrend_optimizer.utils.enums import ExecutionModel
from tests.fixtures.data_generator import make_daily_ohlc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zz_cfg(mode: str = "zigzag", **zz_overrides) -> dict:
    zz = {
        "reversal_threshold": 0.005,
        "min_legs_global": 5,
        "q_strong": 0.80,
        "k_local": 5,
        "entry_side": "counter_trend",
        "arm_timeout_bars_since_extreme": 24,
        "arm_timeout_bars_hard": 78,
    }
    zz.update(zz_overrides)
    cfg = {
        "mode": mode,
        "zigzag": zz,
        "volatility": {"min_atr_pct": None, "max_atr_pct": None},
        "amplitude": {
            "n": 20, "min_separation": None, "lookback": 500,
            "q": 0.60, "atr_period": 14, "atr_floor": 0.0,
        },
        "volume": {
            "volume_column": "Volume",
            "volume_ma_column": "Volume MA",
            "min_ratio": 2.0 if mode == "zigzag_and_volume" else None,
            "max_ratio": None,
        },
    }
    return cfg


def _run(df, filters_cfg, volume_ma=None, global_volume_ma_mean=None,
         execution_model=ExecutionModel.OPEN_TO_OPEN):
    return run_single_backtest(
        open_prices=df["open"].values,
        high=df["high"].values,
        low=df["low"].values,
        close=df["close"].values,
        index=df.index,
        atr_period=14,
        multiplier=3.0,
        trade_mode="revers",
        commission=0.0,
        warmup_period=0,
        early_exit_enabled=False,
        periods_per_year=252.0,
        min_trades_required=0,
        extract_trades_flag=True,
        execution_model=execution_model,
        filters_cfg=filters_cfg,
        volume_ma=volume_ma,
        global_volume_ma_mean=global_volume_ma_mean,
    )


def _volume_ma(df, val: float = 1000.0):
    arr = np.full(len(df), val, dtype=np.float64)
    return arr, float(val)


# ---------------------------------------------------------------------------
# §3.7 contract: filter_diagnostics must carry allow_entry / filtered_reason
# ---------------------------------------------------------------------------


class TestFilterDiagnosticsContract:
    """
    Verifies that run_single_backtest populates allow_entry and filtered_reason
    in filter_diagnostics for ZigZag modes (plan §3.7, Fix 1).
    """

    def test_zigzag_fd_has_allow_entry(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, _zz_cfg("zigzag"))
        fd = res.filter_diagnostics
        assert "allow_entry" in fd, (
            "filter_diagnostics is missing 'allow_entry' for mode=zigzag; "
            "build_signal_events would raise ValueError (Fix 1 BLOCKER)"
        )

    def test_zigzag_fd_has_filtered_reason(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, _zz_cfg("zigzag"))
        fd = res.filter_diagnostics
        assert "filtered_reason" in fd, (
            "filter_diagnostics is missing 'filtered_reason' for mode=zigzag"
        )

    def test_zigzag_and_volume_fd_has_allow_entry(self):
        df = make_daily_ohlc(n_bars=300, seed=10)
        vol_ma, gvmm = _volume_ma(df)
        res = _run(df, _zz_cfg("zigzag_and_volume"), volume_ma=vol_ma,
                   global_volume_ma_mean=gvmm)
        fd = res.filter_diagnostics
        assert "allow_entry" in fd
        assert "filtered_reason" in fd

    def test_allow_entry_length_matches_positions(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, _zz_cfg("zigzag"))
        fd = res.filter_diagnostics
        n = res.positions.shape[0]
        assert fd["allow_entry"].shape[0] == n
        assert fd["filtered_reason"].shape[0] == n

    def test_allow_entry_dtype_bool(self):
        df = make_daily_ohlc(n_bars=200, seed=5)
        res = _run(df, _zz_cfg("zigzag"))
        fd = res.filter_diagnostics
        assert fd["allow_entry"].dtype == bool

    def test_mode_none_fd_no_allow_entry(self):
        """mode=none should NOT have allow_entry (both remain None)."""
        df = make_daily_ohlc(n_bars=200, seed=1)
        res = _run(df, {"mode": "none"})
        fd = res.filter_diagnostics
        assert "allow_entry" not in fd
        assert "filtered_reason" not in fd


# ---------------------------------------------------------------------------
# E2E: run_single_backtest → build_signal_events must not raise
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    """
    Full pipeline test: run_single_backtest → build_signal_events.

    Before Fix 1, build_signal_events raised:
      ValueError: filter_diagnostics is missing 'allow_entry' or
                  'filtered_reason' for zz mode.
    """

    def test_zigzag_pipeline_no_valueerror(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, _zz_cfg("zigzag"))
        fd = res.filter_diagnostics

        signals_df = build_signal_events(
            df=df,
            trend=res.trend,
            atr_period=14,
            trade_mode="revers",
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            atr=res.atr,
            close=df["close"].values.astype(float),
            filters_cfg=_zz_cfg("zigzag"),
            filter_diagnostics=fd,
        )
        assert signals_df is not None
        assert len(signals_df) > 0

    def test_zigzag_and_volume_pipeline_no_valueerror(self):
        df = make_daily_ohlc(n_bars=300, seed=10)
        vol_ma, gvmm = _volume_ma(df)
        cfg = _zz_cfg("zigzag_and_volume")
        res = _run(df, cfg, volume_ma=vol_ma, global_volume_ma_mean=gvmm)
        fd = res.filter_diagnostics

        signals_df = build_signal_events(
            df=df,
            trend=res.trend,
            atr_period=14,
            trade_mode="revers",
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            atr=res.atr,
            close=df["close"].values.astype(float),
            volume_ma=vol_ma,
            global_volume_ma_mean=gvmm,
            filters_cfg=cfg,
            filter_diagnostics=fd,
        )
        assert signals_df is not None

    def test_signals_df_has_allow_entry_column(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, _zz_cfg("zigzag"))
        fd = res.filter_diagnostics

        signals_df = build_signal_events(
            df=df,
            trend=res.trend,
            atr_period=14,
            trade_mode="revers",
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            atr=res.atr,
            close=df["close"].values.astype(float),
            filters_cfg=_zz_cfg("zigzag"),
            filter_diagnostics=fd,
        )
        assert "allow_entry" in signals_df.columns
        assert "filtered_reason" in signals_df.columns

    def test_signals_allow_entry_consistent_with_fd(self):
        """For open-signal rows, allow_entry in signals_df matches filter_diagnostics."""
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, _zz_cfg("zigzag"))
        fd = res.filter_diagnostics

        signals_df = build_signal_events(
            df=df,
            trend=res.trend,
            atr_period=14,
            trade_mode="revers",
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            atr=res.atr,
            close=df["close"].values.astype(float),
            filters_cfg=_zz_cfg("zigzag"),
            filter_diagnostics=fd,
        )
        open_rows = signals_df[signals_df["event_type"] == "open_signal"]
        for _, row in open_rows.iterrows():
            dec_bar = int(row["entry_bar_index"])
            if 0 <= dec_bar < fd["allow_entry"].shape[0]:
                expected = bool(fd["allow_entry"][dec_bar])
                assert bool(row["allow_entry"]) == expected, (
                    f"allow_entry mismatch at entry_bar {dec_bar}: "
                    f"signals_df={row['allow_entry']} fd={expected}"
                )
