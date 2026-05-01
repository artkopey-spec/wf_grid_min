"""
Tests for ZigZag integration in testing/signal_events.py (plan §3.7).

Covers:
  - filter_diagnostics kwarg accepted and takes priority over per-field kwargs
  - mode=zigzag without filter_diagnostics raises ValueError (§3.7 contract)
  - zz_* columns present in output for zz modes, _NA in non-zz modes
  - allow_entry / filtered_reason come from filter_diagnostics (SSOT)
  - close rows have _NA for all zz columns
  - zigzag_and_volume mode: volume_pass computed independently
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.testing.signal_events import build_signal_events
from supertrend_optimizer.utils.enums import ExecutionModel
from tests.fixtures.data_generator import make_daily_ohlc


_NA = "N/A"

_ZZ_COLS = [
    "zz_leg_direction", "zz_cand_height_pct", "zz_global_median",
    "zz_global_p80", "zz_local_median", "zz_n_legs", "zz_regime_state",
    "zz_armed", "zz_armed_side",
]


def _zz_cfg(mode: str = "zigzag") -> dict:
    return {
        "mode": mode,
        "zigzag": {
            "reversal_threshold": 0.005,
            "min_legs_global": 5,
            "q_strong": 0.80,
            "k_local": 5,
            "entry_side": "counter_trend",
            "arm_timeout_bars_since_extreme": 24,
            "arm_timeout_bars_hard": 78,
        },
        "volatility": {"min_atr_pct": None, "max_atr_pct": None},
        "amplitude": {"n": 20, "min_separation": None, "lookback": 500,
                      "q": 0.60, "atr_period": 14, "atr_floor": 0.0},
        "volume": {
            "volume_column": "Volume",
            "volume_ma_column": "Volume MA",
            "min_ratio": None if mode == "zigzag" else 2.0,
            "max_ratio": None,
        },
    }


def _make_fake_fd(n: int, all_allow: bool = True) -> dict:
    """Minimal filter_diagnostics for zigzag mode with n bars."""
    rng = np.random.RandomState(0)
    allow = np.ones(n, dtype=bool) if all_allow else rng.rand(n) > 0.5
    reason = np.where(allow, "ok", "zz_not_armed").astype(object)
    return {
        "mode": "zigzag",
        "allow_entry": allow,
        "filtered_reason": reason,
        "zz_leg_direction": np.zeros(n, dtype=np.int8),
        "zz_cand_height_pct": np.full(n, 0.01),
        "zz_global_median": np.full(n, 0.02),
        "zz_global_p80": np.full(n, 0.03),
        "zz_local_median": np.full(n, 0.015),
        "zz_n_legs_before": np.zeros(n, dtype=np.int64),
        "zz_regime_state": np.zeros(n, dtype=np.int8),
        "zz_armed": np.zeros(n, dtype=np.int8),
        "zz_armed_side": np.zeros(n, dtype=np.int8),
    }


def _run_zz(df, fd, mode="zigzag", vol_ma=None, gvmm=None):
    trend = np.ones(len(df), dtype=np.int8)
    # Introduce one flip so there's at least one signal row.
    trend[len(df) // 2] = -1
    trend[len(df) // 2 + 1] = 1
    return build_signal_events(
        df=df,
        trend=trend,
        atr_period=14,
        trade_mode="revers",
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        filters_cfg=_zz_cfg(mode=mode),
        filter_diagnostics=fd,
        volume_ma=vol_ma,
        global_volume_ma_mean=gvmm,
    )


class TestZigZagFilterDiagnosticsRequired:
    """plan §3.7: missing filter_diagnostics for zz mode raises ValueError."""

    def test_raises_without_filter_diagnostics(self):
        df = make_daily_ohlc(n_bars=50, seed=0)
        trend = np.ones(len(df), dtype=np.int8)
        with pytest.raises(ValueError, match="requires filter_diagnostics"):
            build_signal_events(
                df=df,
                trend=trend,
                atr_period=14,
                trade_mode="revers",
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                filters_cfg=_zz_cfg("zigzag"),
                filter_diagnostics=None,  # explicitly None
            )

    def test_raises_with_empty_fd(self):
        df = make_daily_ohlc(n_bars=50, seed=0)
        trend = np.ones(len(df), dtype=np.int8)
        with pytest.raises(ValueError, match="requires filter_diagnostics"):
            build_signal_events(
                df=df,
                trend=trend,
                atr_period=14,
                trade_mode="revers",
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                filters_cfg=_zz_cfg("zigzag"),
                filter_diagnostics={},  # empty dict → falsy
            )

    def test_raises_zigzag_and_volume_without_fd(self):
        df = make_daily_ohlc(n_bars=50, seed=0)
        trend = np.ones(len(df), dtype=np.int8)
        n = len(df)
        vol_ma = np.full(n, 1000.0)
        with pytest.raises(ValueError, match="requires filter_diagnostics"):
            build_signal_events(
                df=df,
                trend=trend,
                atr_period=14,
                trade_mode="revers",
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                filters_cfg=_zz_cfg("zigzag_and_volume"),
                filter_diagnostics=None,
                volume_ma=vol_ma,
                global_volume_ma_mean=1000.0,
            )


class TestZigZagColumnsInOutput:
    """plan §3.7: zz_* columns appear for open rows; _NA for close rows."""

    @pytest.fixture(scope="class")
    def df(self):
        return make_daily_ohlc(n_bars=100, seed=42)

    @pytest.fixture(scope="class")
    def signals(self, df):
        fd = _make_fake_fd(len(df))
        return _run_zz(df, fd)

    def test_zz_columns_present(self, signals):
        for col in _ZZ_COLS:
            assert col in signals.columns, f"missing column {col}"

    def test_open_rows_have_zz_values(self, signals):
        open_rows = signals[signals["event_type"].str.contains("open")]
        assert len(open_rows) > 0, "need at least one open row"
        for col in _ZZ_COLS:
            assert (open_rows[col] != _NA).any(), (
                f"all open rows are _NA for {col}"
            )

    def test_close_rows_have_na_for_all_zz_cols(self, signals):
        close_rows = signals[signals["event_type"].str.contains("close")]
        assert len(close_rows) > 0, "need at least one close row"
        for col in _ZZ_COLS:
            assert (close_rows[col] == _NA).all(), (
                f"close rows have non-NA in {col}"
            )

    def test_allow_entry_comes_from_fd(self, df):
        """SSOT: allow_entry column must equal fd['allow_entry'] at signal bars."""
        n = len(df)
        fd = _make_fake_fd(n, all_allow=False)
        trend = np.ones(n, dtype=np.int8)
        trend[n // 2] = -1
        trend[n // 2 + 1] = 1
        sigs = build_signal_events(
            df=df,
            trend=trend,
            atr_period=14,
            trade_mode="revers",
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            filters_cfg=_zz_cfg(),
            filter_diagnostics=fd,
        )
        open_rows = sigs[sigs["event_type"].str.contains("open")]
        for _, row in open_rows.iterrows():
            t = int(row["signal_bar_index"])
            expected = bool(fd["allow_entry"][t])
            assert row["allow_entry"] == expected, (
                f"bar {t}: allow_entry={row['allow_entry']} != fd[{t}]={expected}"
            )

    def test_filtered_reason_comes_from_fd(self, df):
        n = len(df)
        fd = _make_fake_fd(n, all_allow=False)
        trend = np.ones(n, dtype=np.int8)
        trend[n // 2] = -1
        trend[n // 2 + 1] = 1
        sigs = build_signal_events(
            df=df,
            trend=trend,
            atr_period=14,
            trade_mode="revers",
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            filters_cfg=_zz_cfg(),
            filter_diagnostics=fd,
        )
        open_rows = sigs[sigs["event_type"].str.contains("open")]
        for _, row in open_rows.iterrows():
            t = int(row["signal_bar_index"])
            assert row["filtered_reason"] == str(fd["filtered_reason"][t])


class TestNonZzModesHaveNaZzCols:
    """zz_* columns are _NA for non-zz filter modes."""

    @pytest.mark.parametrize("mode", ["none", "amplitude"])
    def test_na_in_non_zz_modes(self, mode):
        df = make_daily_ohlc(n_bars=80, seed=7)
        trend = np.ones(len(df), dtype=np.int8)
        trend[40] = -1
        trend[41] = 1
        cfg = {
            "mode": mode,
            "volatility": {"min_atr_pct": None, "max_atr_pct": None},
            "amplitude": {"n": 10, "min_separation": None, "lookback": 30,
                          "q": 0.60, "atr_period": 14, "atr_floor": 0.0},
            "volume": {"volume_column": "Volume", "volume_ma_column": "Volume MA",
                       "min_ratio": None, "max_ratio": None},
        }
        sigs = build_signal_events(
            df=df,
            trend=trend,
            atr_period=14,
            trade_mode="revers",
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            filters_cfg=cfg,
        )
        assert len(sigs) > 0
        for col in _ZZ_COLS:
            assert col in sigs.columns
            assert (sigs[col] == _NA).all(), (
                f"mode={mode}: column {col} has non-NA values"
            )


class TestFilterDiagnosticsOverridesPerFieldKwargs:
    """filter_diagnostics has priority over per-field kwargs (plan §3.7)."""

    def test_fd_overrides_amp_n_arr(self):
        """
        When filter_diagnostics["amp_n"] is provided, it replaces amp_n_arr kwarg.
        We verify by passing BOTH amp_n and amp_threshold in fd and checking
        the amp_n column uses fd values (not the kwarg array).
        """
        df = make_daily_ohlc(n_bars=80, seed=10)
        n = len(df)
        fake_amp_n_fd = np.full(n, 9.99)
        fake_amp_thr_fd = np.full(n, 1.0)   # threshold < amp_n → amp_ok True
        fake_amp_n_kwarg = np.full(n, 1.11)  # different value — should NOT appear
        cfg_amp = {
            "mode": "amplitude",
            "volatility": {"min_atr_pct": None, "max_atr_pct": None},
            "amplitude": {"n": 10, "min_separation": None, "lookback": 30,
                          "q": 0.60, "atr_period": 14, "atr_floor": 0.0},
            "volume": {"volume_column": "Volume", "volume_ma_column": "Volume MA",
                       "min_ratio": None, "max_ratio": None},
        }
        fd = {
            "mode": "amplitude",
            "amp_n": fake_amp_n_fd,
            "amp_threshold": fake_amp_thr_fd,
        }
        trend = np.ones(n, dtype=np.int8)
        trend[40] = -1
        trend[41] = 1
        sigs = build_signal_events(
            df=df,
            trend=trend,
            atr_period=14,
            trade_mode="revers",
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            filters_cfg=cfg_amp,
            filter_diagnostics=fd,
            amp_n_arr=fake_amp_n_kwarg,  # should be overridden by fd
        )
        open_rows = sigs[sigs["event_type"].str.contains("open")]
        assert len(open_rows) > 0
        # amp_n column should reflect fd value (9.99), not kwarg (1.11)
        amp_vals = open_rows["amp_n"]
        non_na = [v for v in amp_vals if v != _NA and not (isinstance(v, float) and np.isnan(v))]
        assert len(non_na) > 0, "expected non-NA amp_n values"
        assert all(abs(float(v) - 9.99) < 1e-9 for v in non_na), (
            f"expected fd amp_n (9.99), got: {non_na}"
        )


class TestZigZagAndVolumeSignalEvents:
    """zigzag_and_volume: volume_pass is independent, allow/reason from fd."""

    def test_volume_pass_is_present(self):
        df = make_daily_ohlc(n_bars=80, seed=20)
        n = len(df)
        fd = _make_fake_fd(n)
        fd["mode"] = "zigzag_and_volume"
        vol_ma = np.full(n, 1000.0)
        sigs = _run_zz(df, fd, mode="zigzag_and_volume",
                       vol_ma=vol_ma, gvmm=1000.0)
        open_rows = sigs[sigs["event_type"].str.contains("open")]
        assert len(open_rows) > 0
        # volume_pass should be True (ratio=1.0 >= None threshold → always pass)
        assert "volume_pass" in sigs.columns
        assert (open_rows["volume_pass"] != _NA).any()

    def test_raises_missing_volume_ma_in_zz_vol_mode(self):
        df = make_daily_ohlc(n_bars=50, seed=21)
        n = len(df)
        fd = _make_fake_fd(n)
        fd["mode"] = "zigzag_and_volume"
        trend = np.ones(n, dtype=np.int8)
        with pytest.raises((ValueError, TypeError)):
            build_signal_events(
                df=df,
                trend=trend,
                atr_period=14,
                trade_mode="revers",
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                filters_cfg=_zz_cfg("zigzag_and_volume"),
                filter_diagnostics=fd,
                volume_ma=None,  # missing
                global_volume_ma_mean=None,
            )
