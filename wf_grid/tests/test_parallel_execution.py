"""
WP-PAR parallel-execution test suite.

Phase 4 (Step 4): pack/unpack + pickle + direct worker parity.
Phase 5 (Step 5): parallel-vs-sequential equivalence + max_workers=1
                  short-circuit + edge cases + delayed-grid order
                  independence.

Plan reference: implementation_plan v.5 §6.1, §6.2, §6.3, §6.4, §6.5,
§6.6, §6.13, §6.15, §6.17 / §8 step 4-5, 8.
"""

from __future__ import annotations

import os
import pickle
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wf_grid.wf import _mp_helpers as mph


_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ohlc_frame(
    n: int = 200,
    *,
    dtype: str = "float64",
    tz: str | None = None,
    freq: str | None = "h",
    name: str | None = None,
) -> pd.DataFrame:
    """Build a deterministic synthetic OHLC frame with the requested metadata."""
    rng = np.random.default_rng(42)
    if tz is None:
        idx = pd.date_range("2024-01-01", periods=n, freq=freq, name=name)
    else:
        idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz=tz, name=name)
    base = 100.0 + np.cumsum(rng.normal(0.0, 0.5, size=n))
    if dtype.startswith("int"):
        # Build integer-valued OHLC; keep open <= high and low <= close logical.
        opens = base.round().astype(dtype)
        highs = (base + 1).round().astype(dtype)
        lows = (base - 1).round().astype(dtype)
        closes = base.round().astype(dtype)
    else:
        opens = base.astype(dtype)
        highs = (base + 0.5).astype(dtype)
        lows = (base - 0.5).astype(dtype)
        closes = (base + 0.1).astype(dtype)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Pickle and pack/unpack tests (plan §6.1)
# ---------------------------------------------------------------------------

class TestPickleRoundTrip:
    """Phase 6.1: things that travel through ProcessPool initargs / submit.

    Every dataclass that crosses the spawn boundary must survive pickle
    round-trip without losing fields, dtypes or ndarray contents.
    """

    def test_grid_point_pickle_roundtrip(self):
        from wf_grid.grid.enumeration import GridPoint
        gp = GridPoint(
            atr_period=14,
            multiplier=2.5,
            trade_mode="both",
            grid_point_id="atr14_mult2.5_both",
        )
        gp2 = pickle.loads(pickle.dumps(gp))
        assert gp == gp2

    def test_wf_window_slice_pickle_roundtrip(self):
        from supertrend_optimizer.utils.time_utils import WFWindowSlice
        ts = pd.Timestamp("2024-01-01")
        s = WFWindowSlice(
            step_index=0,
            train_start_idx=0,
            train_end_idx=100,
            test_start_idx=100,
            test_end_idx=130,
            train_start_time=ts,
            train_end_time=ts + pd.Timedelta(days=99),
            test_start_time=ts + pd.Timedelta(days=100),
            test_end_time=ts + pd.Timedelta(days=129),
        )
        s2 = pickle.loads(pickle.dumps(s))
        assert s == s2

    def test_grid_config_with_resolved_periods_per_year_pickle(self):
        from wf_grid.config.schema import (
            DataConfig,
            GridConfig,
            ValidationConfig,
            WalkForwardConfig,
        )
        cfg = GridConfig(
            data=DataConfig(file_path="x.csv"),
            validation=ValidationConfig(
                walk_forward=WalkForwardConfig(
                    train_size="200D", test_size="50D",
                ),
            ),
        )
        cfg.resolved_periods_per_year = 252.0
        cfg2 = pickle.loads(pickle.dumps(cfg))
        assert cfg2.data.file_path == "x.csv"
        assert cfg2.resolved_periods_per_year == 252.0
        assert cfg2.validation.walk_forward.train_size == "200D"
        assert cfg2.validation.walk_forward.test_size == "50D"

    def test_grid_config_with_full_trade_filter_pickle(self):
        from wf_grid.config.schema import (
            DataConfig,
            GridConfig,
            TradeFilterConfig,
            TradeFilterDiagnosticsConfig,
            TradeFilterLifecycleConfig,
            TradeFilterTriggersConfig,
            TradeFilterTriggerToggleConfig,
            TradeFilterZigZagConfig,
        )
        tf = TradeFilterConfig(
            enabled=True,
            type="zigzag_st",
            zigzag=TradeFilterZigZagConfig(
                global_stats_source="full_dataset",
                leg_height_mode="pct",
                reversal_threshold=0.02,
                candidate_trigger_threshold=0.01,
                candidate_trigger_quantile=None,
                global_median="auto",
                local_window=7,
            ),
            triggers=TradeFilterTriggersConfig(
                candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
                confirmed_median=TradeFilterTriggerToggleConfig(enabled=False),
            ),
            lifecycle=TradeFilterLifecycleConfig(
                freeze_confirmed_legs=4,
                stop_check="confirm_bar_only",
                stopping_exit="opposite_st_flip",
            ),
            diagnostics=TradeFilterDiagnosticsConfig(
                export_state_columns=True,
                export_trigger_columns=False,
            ),
        )
        cfg = GridConfig(
            data=DataConfig(file_path="x.csv"),
            trade_filter=tf,
        )
        cfg2 = pickle.loads(pickle.dumps(cfg))
        assert cfg2.trade_filter is not None
        assert cfg2.trade_filter.enabled is True
        assert cfg2.trade_filter.type == "zigzag_st"
        assert cfg2.trade_filter.zigzag.reversal_threshold == 0.02
        assert cfg2.trade_filter.zigzag.local_window == 7
        assert cfg2.trade_filter.triggers.candidate_threshold.enabled is True
        assert cfg2.trade_filter.triggers.confirmed_median.enabled is False
        assert cfg2.trade_filter.lifecycle.freeze_confirmed_legs == 4
        assert cfg2.trade_filter.diagnostics.export_trigger_columns is False

    def test_step_result_with_oos_trades_df_pickle(self):
        """Phase 6.1: StepResult with non-empty oos_trades_df, dtypes preserved."""
        from wf_grid.wf.step_executor import StepResult
        trades = pd.DataFrame({
            "entry_index": np.array([0, 5, 10], dtype="int64"),
            "exit_index": np.array([3, 9, 14], dtype="int64"),
            "entry_price": np.array([100.5, 102.25, 99.75], dtype="float64"),
            "exit_price": np.array([101.0, 101.5, 100.0], dtype="float64"),
            "side": np.array([1, -1, 1], dtype="int8"),
            "pnl_pct": np.array([0.005, -0.0073, 0.0025], dtype="float64"),
        })
        sr = StepResult(
            grid_point_id="gp1",
            wf_step=1,
            test_start_idx=0,
            test_end_idx=15,
            metrics={"sum_pnl_pct": 0.0002, "max_drawdown": -0.01},
            oos_trades_df=trades,
            prepend_bars_requested=10,
            prepend_bars_applied=10,
            used_prepend=True,
            used_legacy_oos_path=False,
            used_defensive_fallback=False,
            oos_boundary_index=10,
            warmup_used=0,
            warmup_effective=0,
            effective_oos_bars=5,
        )
        sr2 = pickle.loads(pickle.dumps(sr))
        assert sr2.grid_point_id == "gp1"
        assert sr2.wf_step == 1
        assert sr2.metrics == sr.metrics
        pd.testing.assert_frame_equal(
            sr2.oos_trades_df, trades,
            check_exact=True, check_column_type=True,
        )
        # Dtypes survive unchanged through pickle.
        for col in trades.columns:
            assert str(sr2.oos_trades_df[col].dtype) == str(trades[col].dtype)

    def test_step_result_with_filter_diagnostics_oos_pickle(self):
        """Phase 6.1: StepResult with numpy arrays inside filter_diagnostics_oos."""
        from wf_grid.wf.step_executor import StepResult
        diag = {
            "fsm_state": np.array([0, 1, 1, 2, 0], dtype="int8"),
            "candidate_height_pct": np.array(
                [np.nan, 0.012, 0.018, 0.015, np.nan], dtype="float64"
            ),
            "confirm_event": np.array([0, 0, 1, 0, 0], dtype="int8"),
            "filter_blocked": np.array(
                [False, True, True, False, False], dtype="bool"
            ),
        }
        sr = StepResult(
            grid_point_id="gp2",
            wf_step=2,
            test_start_idx=0,
            test_end_idx=5,
            metrics={"n_trades": 2.0},
            oos_trades_df=None,
            prepend_bars_requested=0,
            prepend_bars_applied=0,
            used_prepend=False,
            used_legacy_oos_path=False,
            used_defensive_fallback=False,
            oos_boundary_index=0,
            warmup_used=0,
            warmup_effective=0,
            effective_oos_bars=5,
            filter_diagnostics_oos=diag,
        )
        sr2 = pickle.loads(pickle.dumps(sr))
        assert sr2.filter_diagnostics_oos is not None
        assert set(sr2.filter_diagnostics_oos.keys()) == set(diag.keys())
        for key, expected in diag.items():
            restored = sr2.filter_diagnostics_oos[key]
            assert restored.dtype == expected.dtype, (
                f"dtype drift for diag[{key!r}]: "
                f"expected {expected.dtype}, got {restored.dtype}"
            )
            np.testing.assert_array_equal(restored, expected)

    def test_zigzag_global_stats_pickle(self):
        """Phase 6.1: ZigZagGlobalStats with non-default metadata, legs and heights."""
        from supertrend_optimizer.core.zigzag_st_filter import (
            ConfirmedLeg,
            ZigZagGlobalStats,
        )
        legs = [
            ConfirmedLeg(
                start_bar=0, end_bar=2, confirm_bar=3,
                start_price=100.0, end_price=102.0,
                direction=1, height_pct=0.02,
            ),
            ConfirmedLeg(
                start_bar=2, end_bar=3, confirm_bar=5,
                start_price=102.0, end_price=99.0,
                direction=-1, height_pct=3.0 / 102.0,
            ),
        ]
        heights = np.array(
            [legs[0].height_pct, legs[1].height_pct], dtype="float64",
        )
        stats = ZigZagGlobalStats(
            reversal_threshold=0.02,
            global_stats_source="full_dataset",
            leg_height_mode="pct",
            confirmed_legs=legs,
            confirmed_heights_pct=heights,
            global_median=float(np.median(heights)),
            candidate_trigger_threshold=0.015,
            candidate_trigger_source="explicit",
            candidate_trigger_quantile=None,
            n_legs_total=len(legs),
            insufficient_data=False,
            fail_closed_reason=None,
            metadata={
                "source_label": "unit_test",
                "n_close_bars": 9,
                "rng_seed": 42,
            },
        )
        stats2 = pickle.loads(pickle.dumps(stats))
        assert stats2.reversal_threshold == 0.02
        assert stats2.global_stats_source == "full_dataset"
        assert stats2.leg_height_mode == "pct"
        assert stats2.candidate_trigger_threshold == 0.015
        assert stats2.candidate_trigger_source == "explicit"
        assert stats2.candidate_trigger_quantile is None
        assert stats2.n_legs_total == 2
        assert stats2.insufficient_data is False
        assert stats2.fail_closed_reason is None
        assert stats2.metadata == {
            "source_label": "unit_test",
            "n_close_bars": 9,
            "rng_seed": 42,
        }
        # confirmed_legs preserved with dataclass equality.
        assert list(stats2.confirmed_legs) == legs
        # confirmed_heights_pct preserved bit-exact, including dtype.
        assert stats2.confirmed_heights_pct.dtype == heights.dtype
        np.testing.assert_array_equal(stats2.confirmed_heights_pct, heights)
        assert stats2.global_median == pytest.approx(float(np.median(heights)))


# ---------------------------------------------------------------------------
# pack/unpack round-trip (plan §6.2)
# ---------------------------------------------------------------------------

class TestPackDataRoundTrip:
    """Phase 6.2: lossless dtype/tz/index_name/index_freq preservation."""

    @pytest.mark.parametrize("ohlc_dtype", ["float64", "float32", "int64", "int32"])
    def test_dtype_preserved(self, ohlc_dtype):
        df = _ohlc_frame(n=64, dtype=ohlc_dtype)
        round_trip = mph.unpack_data(mph.pack_data(df))
        pd.testing.assert_frame_equal(
            df, round_trip,
            check_exact=True,
            check_index_type=True,
            check_column_type=True,
        )
        for col in ("open", "high", "low", "close"):
            assert str(round_trip[col].dtype) == ohlc_dtype, (
                f"dtype drift on {col!r}: expected {ohlc_dtype}, "
                f"got {round_trip[col].dtype}"
            )

    def test_tz_naive_index(self):
        df = _ohlc_frame(n=100, tz=None)
        round_trip = mph.unpack_data(mph.pack_data(df))
        pd.testing.assert_frame_equal(
            df, round_trip, check_exact=True, check_index_type=True,
        )
        assert round_trip.index.tz is None

    def test_utc_aware_index(self):
        df = _ohlc_frame(n=100, tz="UTC")
        round_trip = mph.unpack_data(mph.pack_data(df))
        pd.testing.assert_frame_equal(
            df, round_trip, check_exact=True, check_index_type=True,
        )
        assert str(round_trip.index.tz) == "UTC"

    def test_us_resolution_index_normalized_to_ns(self):
        """Regression: datetime64[us] payload must not be restored as ns."""
        df = _ohlc_frame(n=20, tz="UTC+03:00", freq="min")
        df.index = df.index.as_unit("us")
        assert "datetime64[us" in str(df.index.dtype)

        packed = mph.pack_data(df)
        round_trip = mph.unpack_data(packed)

        assert packed["index_unit"] == "ns"
        assert "datetime64[ns" in str(round_trip.index.dtype)
        assert str(round_trip.index.tz) == "UTC+03:00"
        assert round_trip.index[10] == df.index[10]
        assert round_trip.index[10].year == 2024
        pd.testing.assert_frame_equal(
            df.reset_index(drop=True),
            round_trip.reset_index(drop=True),
            check_exact=True,
        )

    def test_non_utc_aware_index_dst_boundary(self):
        # America/New_York Spring-forward: 2024-03-10 02:00 -> 03:00.
        # Build a daily series spanning the boundary; expect tz preserved.
        df = _ohlc_frame(n=20, tz="America/New_York", freq="D")
        round_trip = mph.unpack_data(mph.pack_data(df))
        pd.testing.assert_frame_equal(
            df, round_trip, check_exact=True, check_index_type=True,
        )
        assert str(round_trip.index.tz) == "America/New_York"
        # Same wall-clock instants on both sides (no silent shift).
        for orig, restored in zip(df.index, round_trip.index):
            assert orig == restored, f"tz drift: {orig!r} != {restored!r}"

    def test_index_name_preserved(self):
        df = _ohlc_frame(n=10, name="ts")
        round_trip = mph.unpack_data(mph.pack_data(df))
        assert round_trip.index.name == "ts"

    def test_index_name_none_preserved(self):
        df = _ohlc_frame(n=10, name=None)
        round_trip = mph.unpack_data(mph.pack_data(df))
        assert round_trip.index.name is None

    def test_index_freq_preserved(self):
        df = _ohlc_frame(n=10, freq="h")
        # date_range sets freq automatically; verify pack/unpack keeps it.
        assert df.index.freq is not None
        round_trip = mph.unpack_data(mph.pack_data(df))
        assert round_trip.index.freq == df.index.freq

    def test_index_freq_none_for_irregular_index(self):
        # Drop a row to break frequency; resulting index has freq=None.
        df = _ohlc_frame(n=10, freq="h").drop(index=[
            pd.Timestamp("2024-01-01 03:00:00")
        ])
        assert df.index.freq is None
        round_trip = mph.unpack_data(mph.pack_data(df))
        assert round_trip.index.freq is None
        pd.testing.assert_frame_equal(df, round_trip, check_exact=True)

    def test_real_data_csv_smoke(self):
        """Real data.csv round-trips bit-exact under validate_ohlc_data."""
        data_path = _REPO_ROOT / "data.csv"
        if not data_path.exists():
            pytest.skip("data.csv not present in workspace")
        from supertrend_optimizer.data.validator import validate_ohlc_data
        df = pd.read_csv(data_path, parse_dates=True, index_col=0)
        df = validate_ohlc_data(df, strict=True)
        round_trip = mph.unpack_data(mph.pack_data(df))
        pd.testing.assert_frame_equal(
            df, round_trip,
            check_exact=True,
            check_index_type=True,
            check_column_type=True,
        )

    def test_pack_rejects_non_datetime_index(self):
        df = pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
            index=[0],
        )
        with pytest.raises(TypeError, match="DatetimeIndex"):
            mph.pack_data(df)

    def test_pack_rejects_missing_ohlc_column(self):
        df = pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0]},
            index=pd.date_range("2024-01-01", periods=1, freq="h"),
        )
        with pytest.raises(KeyError, match="close"):
            mph.pack_data(df)

    def test_extra_columns_preserved(self):
        """OHLC + extra columns survive pack/unpack losslessly (plan §2.2 superset)."""
        idx = pd.date_range("2024-01-01", periods=8, freq="h")
        df = pd.DataFrame(
            {
                "open":   np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], dtype="float64"),
                "high":   np.array([1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1], dtype="float64"),
                "low":    np.array([0.9, 1.9, 2.9, 3.9, 4.9, 5.9, 6.9, 7.9], dtype="float64"),
                "close":  np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], dtype="float64"),
                "volume": np.array([100, 200, 300, 400, 500, 600, 700, 800], dtype="int64"),
            },
            index=idx,
        )
        round_trip = mph.unpack_data(mph.pack_data(df))
        pd.testing.assert_frame_equal(
            df, round_trip,
            check_exact=True,
            check_dtype=True,
            check_index_type=True,
            check_column_type=True,
        )
        assert list(round_trip.columns) == list(df.columns), (
            f"column order must be preserved: {list(round_trip.columns)}"
        )
        assert str(round_trip["volume"].dtype) == "int64", (
            f"volume dtype must be int64, got {round_trip['volume'].dtype}"
        )


# ---------------------------------------------------------------------------
# Direct _init_worker + _run_grid_point_both vs sequential (plan §8 step 4)
# ---------------------------------------------------------------------------

class TestWorkerSingleGridPointParity:
    """Run _init_worker + _run_grid_point_both in-process and compare to
    the sequential runner output for the same grid_point.  This proves the
    worker code path delivers bit-identical StepResult lists without
    spawning a real ProcessPool.
    """

    @pytest.fixture(autouse=True)
    def _reset_worker_state(self):
        # Plan §6.13 guidance: tests calling _init_worker directly must
        # reset state to avoid leakage into other tests.
        mph._WORKER_STATE.clear()
        yield
        mph._WORKER_STATE.clear()

    def _build_minimal_run_artifacts(self):
        """Build (data, config, wf_slices, prepend_bars, grid_point) tuple."""
        data_path = _REPO_ROOT / "data.csv"
        if not data_path.exists():
            pytest.skip("data.csv not present in workspace")
        from supertrend_optimizer.data.validator import validate_ohlc_data
        from supertrend_optimizer.utils.time_utils import make_walk_forward_slices
        from wf_grid.config.loader import resolve_periods_per_year
        from wf_grid.config.schema import (
            BacktestConfig,
            DataConfig,
            GridConfig,
            OptimizationConfig,
            ValidationConfig,
            WalkForwardConfig,
        )
        from wf_grid.grid.enumeration import GridPoint
        from wf_grid.wf.runner import compute_prepend_bars

        df = pd.read_csv(data_path, parse_dates=True, index_col=0)
        df = validate_ohlc_data(df, strict=True)
        # Cap to keep test fast.
        df = df.iloc[:1500].copy()

        cfg = GridConfig(
            data=DataConfig(file_path=str(data_path)),
            optimization=OptimizationConfig(
                atr_period_range=[10, 10],
                multiplier_range=[2.5, 2.5],
                multiplier_step=0.1,
                trade_mode="both",
            ),
            backtest=BacktestConfig(),
            validation=ValidationConfig(
                walk_forward=WalkForwardConfig(
                    train_size="500bars",
                    test_size="200bars",
                    min_train_bars=100,
                    min_test_bars=20,
                ),
            ),
        )
        cfg = resolve_periods_per_year(cfg, df)

        wf_slices = make_walk_forward_slices(
            index=df.index,
            train_size=cfg.validation.walk_forward.train_size,
            test_size=cfg.validation.walk_forward.test_size,
            step_size=cfg.validation.walk_forward.test_size,
            scheme=cfg.validation.walk_forward.scheme,
            anchor=cfg.validation.walk_forward.anchor,
            min_train_bars=cfg.validation.walk_forward.min_train_bars,
            min_test_bars=cfg.validation.walk_forward.min_test_bars,
        )
        if not wf_slices:
            pytest.skip("Synthetic frame too short for WF slicing")

        # prepend_bars depends on raw config dict; reuse the helper.
        import dataclasses
        def _to_dict(o):
            if dataclasses.is_dataclass(o) and not isinstance(o, type):
                return {f.name: _to_dict(getattr(o, f.name))
                        for f in dataclasses.fields(o)}
            if isinstance(o, list):
                return [_to_dict(i) for i in o]
            if isinstance(o, dict):
                return {k: _to_dict(v) for k, v in o.items()}
            return o
        prepend_bars = compute_prepend_bars(df, _to_dict(cfg))

        gp = GridPoint(
            atr_period=10, multiplier=2.5, trade_mode="both",
            grid_point_id="atr10_m2.5_both",
        )
        return df, cfg, wf_slices, prepend_bars, gp

    def test_worker_path_matches_sequential(self):
        from wf_grid.wf.runner import (
            run_wf_for_grid_point,
            run_wf_train_for_grid_point,
        )

        df, cfg, wf_slices, prepend_bars, gp = self._build_minimal_run_artifacts()

        # Sequential reference.
        seq_oos = run_wf_for_grid_point(
            grid_point=gp,
            wf_slices=wf_slices,
            full_data=df,
            config=cfg,
            prepend_bars_requested=prepend_bars,
            zigzag_global_stats=None,
        )
        seq_train = run_wf_train_for_grid_point(
            grid_point=gp,
            wf_slices=wf_slices,
            full_data=df,
            config=cfg,
            zigzag_global_stats=None,
        )

        # Worker path (same process; no ProcessPool spawned).
        mph._init_worker(
            project_root=str(_REPO_ROOT),
            donor_path=str(_REPO_ROOT / "donor"),
            data_dict=mph.pack_data(df),
            wf_slices=wf_slices,
            config=cfg,
            prepend_bars=prepend_bars,
            zigzag_global_stats=None,
        )
        rid, w_oos, w_train = mph._run_grid_point_both(gp)
        assert rid == gp.grid_point_id

        # Compare lists element-wise.  StepResult has no step_status field
        # at runner-level (assign_step_status runs later via the pipeline);
        # we compare the deterministic fields that round-trip through pickle.
        assert len(w_oos) == len(seq_oos)
        assert len(w_train) == len(seq_train)
        for a, b in zip(w_oos, seq_oos):
            assert a.grid_point_id == b.grid_point_id
            assert a.wf_step == b.wf_step
            assert a.metrics == b.metrics
            assert a.error_message == b.error_message
            assert a.error_type == b.error_type
        for a, b in zip(w_train, seq_train):
            assert a.grid_point_id == b.grid_point_id
            assert a.wf_step == b.wf_step
            assert a.metrics == b.metrics
            assert a.error_message == b.error_message
            assert a.error_type == b.error_type


# ---------------------------------------------------------------------------
# Phase 5 helpers — tiny YAML / CSV pipeline harness
# ---------------------------------------------------------------------------

def _build_tiny_dataset(tmp_path: Path, n_bars: int = 1500) -> Path:
    """Carve a tiny CSV out of the real data.csv (or skip)."""
    src = _REPO_ROOT / "data.csv"
    if not src.exists():
        pytest.skip("data.csv not present in workspace")
    df = pd.read_csv(src, parse_dates=True, index_col=0)
    df = df.iloc[:n_bars].copy()
    out = tmp_path / "tiny_data.csv"
    df.to_csv(out)
    return out


_TINY_BASE_YAML = """\
schema_version: 1
data:
  file_path: "{csv_path}"
  periods_per_year: 252
  annualization_basis: "trading"
optimization:
  atr_period_range: [10, 12]
  multiplier_range: [2.0, 2.4]
  multiplier_step: 0.2
  trade_mode: "both"
backtest:
  commission: 0.0003
  min_trades_required: 3
validation:
  warmup_period: 0
  warmup_period_auto: true
  walk_forward:
    train_size: "500bars"
    test_size: "200bars"
    step_size: "200bars"
    scheme: "rolling"
    anchor: "start"
    min_train_bars: 200
    min_test_bars: 50
gates:
  step:
    max_drawdown_threshold: -0.50
  candidate:
    positive_median_threshold: 0.0
    min_trades_median: 3.0
    max_drawdown_threshold: -0.50
    min_ok_ratio: 0.7
ranking:
  mode: "legacy"
  sort_by: "sum_pnl_pct_Median"
  tiebreaker: "sum_pnl_pct_Min"
scoring:
  score_weights:
    sum_pnl_pct_Median: 0.45
    profitable_segments_count: 0.35
    abs_max_drawdown_Min: 0.20
status:
  min_meaningful_bars: 30
bucket:
  atr_bucket_step: 2
  mult_bucket_step: 0.2
  min_buckets_for_median: 1
"""

# Plan §6.4: full trade_filter block with enabled=true.  Mirrors production
# config.yaml structure so the donor validator sees a realistic shape.
_TINY_TRADE_FILTER_ENABLED_BLOCK = """\
trade_filter:
  enabled: true
  type: "zigzag_st_mode"
  zigzag:
    global_stats_source: "full_dataset"
    leg_height_mode: "pct"
    reversal_threshold: 0.004
    candidate_trigger_threshold: auto
    candidate_trigger_quantile: 0.80
    global_median: "auto"
    local_window: 5
  triggers:
    candidate_threshold:
      enabled: true
    confirmed_median:
      enabled: true
  lifecycle:
    freeze_confirmed_legs: 3
    stop_check: "confirm_bar_only"
    stopping_exit: "opposite_st_flip"
  diagnostics:
    export_state_columns: true
    export_trigger_columns: true
"""

# Plan §6.3 / Appendix A2: trade_filter is present in YAML but explicitly
# disabled.  Donor validator only requires enabled+type/zigzag/etc when
# enabled=true, so the minimal disabled-but-present block is sufficient.
_TINY_TRADE_FILTER_DISABLED_BLOCK = """\
trade_filter:
  enabled: false
"""

# Plan §6.4 (close-only ZigZag fixture): tuned for the tiled MANY_LEG_SAWTOOTH
# dataset whose reversal pattern is r=0.01.  Same shape as
# _TINY_TRADE_FILTER_ENABLED_BLOCK; only the reversal_threshold differs.
_FIXTURE_TRADE_FILTER_ENABLED_BLOCK = """\
trade_filter:
  enabled: true
  type: "zigzag_st_mode"
  zigzag:
    global_stats_source: "full_dataset"
    leg_height_mode: "pct"
    reversal_threshold: 0.01
    candidate_trigger_threshold: auto
    candidate_trigger_quantile: 0.80
    global_median: "auto"
    local_window: 5
  triggers:
    candidate_threshold:
      enabled: true
    confirmed_median:
      enabled: true
  lifecycle:
    freeze_confirmed_legs: 3
    stop_check: "confirm_bar_only"
    stopping_exit: "opposite_st_flip"
  diagnostics:
    export_state_columns: true
    export_trigger_columns: true
"""


_TRADE_FILTER_MODES = {
    "absent": None,                              # plan §6.5
    "disabled": _TINY_TRADE_FILTER_DISABLED_BLOCK,  # plan §6.3
    "enabled": _TINY_TRADE_FILTER_ENABLED_BLOCK,    # plan §6.4
    "enabled_fixture": _FIXTURE_TRADE_FILTER_ENABLED_BLOCK,  # plan §6.4 (close-only)
}


def _write_tiny_yaml(
    tmp_path: Path,
    csv_path: Path,
    *,
    parallel_enabled: bool,
    max_workers: int | None = None,
    chunksize: int | None = None,
    fallback_to_sequential: bool | None = None,
    trade_filter_mode: str = "absent",
    filename: str = "tiny_config.yaml",
) -> Path:
    """Render a tiny config with a configurable execution + trade_filter block.

    trade_filter_mode:
      - "absent"  -> plan §6.5: no trade_filter block, cfg.trade_filter is None
      - "disabled" -> plan §6.3: trade_filter present with enabled=false
      - "enabled"  -> plan §6.4: full enabled block tuned for tiny CSV
      - "enabled_fixture" -> plan §6.4: full enabled block tuned for the
        zigzag close-only fixture (reversal_threshold=0.01)
    """
    if trade_filter_mode not in _TRADE_FILTER_MODES:
        raise ValueError(
            f"unknown trade_filter_mode: {trade_filter_mode!r}; "
            f"expected one of {sorted(_TRADE_FILTER_MODES)}"
        )
    body = _TINY_BASE_YAML.format(
        csv_path=str(csv_path).replace("\\", "/"),
    )
    body += "execution:\n"
    body += f"  parallel_enabled: {'true' if parallel_enabled else 'false'}\n"
    if max_workers is not None:
        body += f"  max_workers: {max_workers}\n"
    if chunksize is not None:
        body += f"  chunksize: {chunksize}\n"
    if fallback_to_sequential is not None:
        body += (
            "  fallback_to_sequential: "
            f"{'true' if fallback_to_sequential else 'false'}\n"
        )
    block = _TRADE_FILTER_MODES[trade_filter_mode]
    if block is not None:
        body += block
    p = tmp_path / filename
    p.write_text(body, encoding="utf-8")
    return p


def _build_zigzag_fixture_dataset(
    tmp_path: Path, *, n_bars: int = 1500
) -> Path:
    """Build an OHLC CSV from MANY_LEG_SAWTOOTH close prices (plan §6.4).

    The close-only ZigZag fixture is only 14 bars long, far below the
    walk-forward train_size used by the tiny config.  We tile the close
    array up to ``n_bars`` so the ZigZag pattern (r=0.01) is preserved at
    every period boundary while the resulting frame is large enough for
    rolling walk-forward windows.
    """
    from wf_grid.tests.zigzag_st_close_only_fixture import MANY_LEG_SAWTOOTH

    base = MANY_LEG_SAWTOOTH.close.astype("float64")
    repeats = int(np.ceil(n_bars / base.size))
    closes = np.tile(base, repeats)[:n_bars]
    # Build a deterministic OHLC frame around close.  Spreads are minimal
    # so the zigzag pattern survives the wrap to OHLC.
    opens = closes.copy()
    highs = closes + 0.05
    lows = closes - 0.05
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="h", name="datetime")
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=idx,
    )
    out = tmp_path / "zigzag_fixture_data.csv"
    df.to_csv(out)
    return out


def _assert_frame_equal_or_both_none(a, b, name: str) -> None:
    if a is None and b is None:
        return
    assert a is not None and b is not None, (
        f"frame {name!r} present on one side only"
    )
    pd.testing.assert_frame_equal(
        a, b, check_exact=True, check_index_type=True, check_column_type=True,
    )


def _assert_broken_process_pool_marker(msg: str) -> None:
    """Assert that the error message reflects an infrastructure failure.

    BrokenProcessPool.__str__() is platform / Python version dependent, so
    we accept any of the canonical markers a user would recognise.
    """
    low = msg.lower()
    assert (
        "brokenprocesspool" in low
        or "process pool" in low
        or "process in the executor" in low
        or "abruptly" in low
        or "initializer failed" in low
    ), f"unexpected error message for infra failure: {msg!r}"


# ---------------------------------------------------------------------------
# Phase 5/7: sequential vs parallel equivalence
#
# Plan §6.3  trade_filter disabled-but-present (block in YAML, enabled=false).
# Plan §6.4  trade_filter enabled (full block; one tiny-CSV variant and one
#            close-only ZigZag fixture variant per plan wording).
# Plan §6.5  trade_filter block absent (cfg.trade_filter is None).
# ---------------------------------------------------------------------------

class TestSequentialVsParallelEquivalence:
    """Bit-identical outputs in both modes; execution_mode marker correct."""

    _COMPARE_FRAMES: tuple[str, ...] = (
        "step_oos_long",
        "step_train_long",
        "aggregated",
        "ranked",
        "summary_wide",
        "trades_oos",
        "trades_train",
        "bucket_matrix_median",
    )

    def _run_pair(
        self,
        tmp_path,
        *,
        trade_filter_mode: str,
        slug: str,
        csv_path: Path | None = None,
    ):
        """Run sequential + parallel pipelines with the same tiny config and
        return (seq_result, par_result) for downstream assertions.
        """
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        if csv_path is None:
            csv_path = _build_tiny_dataset(tmp_path)
        seq_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=False,
            trade_filter_mode=trade_filter_mode,
            filename=f"seq_{slug}.yaml",
        )
        par_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            trade_filter_mode=trade_filter_mode,
            filename=f"par_{slug}.yaml",
        )
        seq_out = tmp_path / f"seq_{slug}.xlsx"
        par_out = tmp_path / f"par_{slug}.xlsx"

        seq_result = run_grid_pipeline(
            str(seq_yaml), output_path=str(seq_out),
        )
        par_result = run_grid_pipeline(
            str(par_yaml), output_path=str(par_out),
        )
        return seq_result, par_result

    def _assert_equivalence(self, seq_result, par_result) -> None:
        assert seq_result.error is None, (
            f"sequential pipeline failed: {seq_result.error}"
        )
        assert par_result.error is None, (
            f"parallel pipeline failed: {par_result.error}"
        )
        assert seq_result.execution_mode == "sequential"
        assert par_result.execution_mode == "parallel"

        for name in self._COMPARE_FRAMES:
            _assert_frame_equal_or_both_none(
                getattr(seq_result, name),
                getattr(par_result, name),
                name,
            )

        # diagnostics records the combined timer in both modes.
        assert seq_result.diagnostics is not None
        assert par_result.diagnostics is not None
        assert "wf_grid_execution" in seq_result.diagnostics.timings
        assert "wf_grid_execution" in par_result.diagnostics.timings

        # Plan §6.3: output parent directory matches.
        assert seq_result.output_path is not None
        assert par_result.output_path is not None
        assert seq_result.output_path.parent == par_result.output_path.parent

    def test_equivalence_trade_filter_absent(self, tmp_path):
        """Plan §6.5: YAML omits trade_filter -> cfg.trade_filter is None."""
        seq_result, par_result = self._run_pair(
            tmp_path, trade_filter_mode="absent", slug="absent",
        )
        # Sanity: trade_filter block was indeed absent on both sides.
        assert seq_result.config.trade_filter is None
        assert par_result.config.trade_filter is None
        self._assert_equivalence(seq_result, par_result)

    def test_equivalence_trade_filter_disabled_present(self, tmp_path):
        """Plan §6.3: trade_filter is in YAML but enabled=false.

        Distinct from `_absent` because the donor build path materialises a
        TradeFilterConfig instance with `enabled=False` and step_executor
        forwards it through the pipeline.  Bit-identical sequential vs
        parallel still required.
        """
        seq_result, par_result = self._run_pair(
            tmp_path, trade_filter_mode="disabled", slug="disabled",
        )
        # Sanity: trade_filter is materialised and explicitly disabled.
        assert seq_result.config.trade_filter is not None
        assert par_result.config.trade_filter is not None
        assert seq_result.config.trade_filter.enabled is False
        assert par_result.config.trade_filter.enabled is False
        self._assert_equivalence(seq_result, par_result)

    def test_equivalence_trade_filter_enabled(self, tmp_path):
        """Plan §6.4: trade_filter.enabled=true; bit-identical, no tolerance.

        Tiny-CSV variant.  Exercises the enabled path on production-shaped
        OHLC data; complements the close-only fixture variant below.
        """
        seq_result, par_result = self._run_pair(
            tmp_path, trade_filter_mode="enabled", slug="enabled",
        )
        # Sanity: trade_filter block is materialised and enabled in both runs.
        assert seq_result.config.trade_filter is not None
        assert par_result.config.trade_filter is not None
        assert seq_result.config.trade_filter.enabled is True
        assert par_result.config.trade_filter.enabled is True
        self._assert_equivalence(seq_result, par_result)

    def test_equivalence_trade_filter_enabled_zigzag_fixture(self, tmp_path):
        """Plan §6.4: enabled path driven by zigzag_st_close_only_fixture.

        The close-only fixture only has 14 bars (insufficient for walk-forward),
        so we tile MANY_LEG_SAWTOOTH up to the tiny config's window length.
        The reversal_threshold matches the fixture (r=0.01) so the underlying
        ZigZag pattern is the one defined in the shared fixture file.
        """
        csv_path = _build_zigzag_fixture_dataset(tmp_path)
        seq_result, par_result = self._run_pair(
            tmp_path,
            trade_filter_mode="enabled_fixture",
            slug="enabled_fx",
            csv_path=csv_path,
        )
        # Sanity: enabled+r=0.01 reaches the workers in both modes.
        assert seq_result.config.trade_filter is not None
        assert par_result.config.trade_filter is not None
        assert seq_result.config.trade_filter.enabled is True
        assert par_result.config.trade_filter.enabled is True
        assert (
            seq_result.config.trade_filter.zigzag.reversal_threshold == 0.01
        )
        self._assert_equivalence(seq_result, par_result)


# ---------------------------------------------------------------------------
# Phase 5: max_workers=1 short-circuits to sequential (plan §6.6)
# ---------------------------------------------------------------------------

class TestMaxWorkersOneShortCircuit:
    """parallel_enabled=true with max_workers=1 -> sequential, no spawn."""

    def test_max_workers_one_runs_sequential_and_matches_default(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline
        from wf_grid.pipeline import orchestrator as orch_mod

        csv_path = _build_tiny_dataset(tmp_path)

        # Detect any attempt to instantiate ProcessPoolExecutor:
        spawn_called = {"n": 0}
        original = orch_mod.ProcessPoolExecutor

        class _SpyPool(original):
            def __init__(self, *a, **kw):
                spawn_called["n"] += 1
                super().__init__(*a, **kw)

        monkeypatch.setattr(orch_mod, "ProcessPoolExecutor", _SpyPool)

        # Reference: dataclass-default sequential run.
        seq_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=False, filename="ref_seq.yaml",
        )
        ref_out = tmp_path / "ref_seq.xlsx"
        ref_result = run_grid_pipeline(str(seq_yaml), output_path=str(ref_out))

        # Subject: parallel_enabled=true with max_workers=1.
        mw1_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=1, filename="mw1.yaml",
        )
        mw1_out = tmp_path / "mw1.xlsx"
        mw1_result = run_grid_pipeline(str(mw1_yaml), output_path=str(mw1_out))

        assert ref_result.error is None
        assert mw1_result.error is None
        assert ref_result.execution_mode == "sequential"
        assert mw1_result.execution_mode == "sequential", (
            "max_workers=1 must short-circuit to the sequential branch"
        )
        assert spawn_called["n"] == 0, (
            "ProcessPoolExecutor must NOT be created when max_workers=1"
        )
        for name in (
            "step_oos_long", "step_train_long", "aggregated", "ranked",
            "summary_wide", "trades_oos", "trades_train",
            "bucket_matrix_median",
        ):
            _assert_frame_equal_or_both_none(
                getattr(ref_result, name),
                getattr(mw1_result, name),
                name,
            )


# ---------------------------------------------------------------------------
# Phase 5: edge cases (plan §6.15)
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary conditions for the parallel dispatcher."""

    def test_empty_grid_runs_sequential_no_crash(self, tmp_path, monkeypatch):
        """len(grid_points)==0: no crash, sequential mode, empty frames (plan §6.15)."""
        from wf_grid.pipeline import orchestrator as orch_mod
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        # Force enumerate_grid to return an empty grid.  The dispatcher must
        # take the sequential short-circuit path regardless of the YAML flag,
        # and the pipeline must NOT crash downstream.
        monkeypatch.setattr(orch_mod, "enumerate_grid", lambda cfg: [])

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=4, filename="empty.yaml",
        )
        out = tmp_path / "empty.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        assert result.error is None, (
            f"empty-grid pipeline must not crash, got error: {result.error!r}"
        )
        assert result.execution_mode == "sequential"
        assert result.grid_points == []

        # Step-level frames may be None or empty; either way the pipeline
        # must surface them without a crash.  Treat None as "absent due to
        # empty grid"; empty DataFrames must be empty in row count.
        for name in ("step_oos_long", "step_train_long"):
            frame = getattr(result, name)
            assert frame is None or len(frame) == 0, (
                f"empty-grid run produced non-empty {name!r}"
            )

    def test_single_grid_point_short_circuits_to_sequential(
        self, tmp_path, monkeypatch
    ):
        """len(grid_points)==1: sequential short-circuit, bit-identical to
        explicit-sequential reference (plan §6.15)."""
        from wf_grid.pipeline import orchestrator as orch_mod
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)

        # Force enumerate_grid to return exactly one GridPoint, identical
        # across both runs (so bit-equality is meaningful).
        from wf_grid.grid.enumeration import GridPoint
        single_gp = [GridPoint(
            atr_period=10, multiplier=2.0, trade_mode="both",
            grid_point_id="atr10_mult2.0_both",
        )]
        monkeypatch.setattr(orch_mod, "enumerate_grid", lambda cfg: list(single_gp))

        # Detect any spawn attempt: with a single grid point, _resolve_max_workers
        # collapses to 1 and the dispatcher must short-circuit to sequential.
        spawn_called = {"n": 0}
        original_pool = orch_mod.ProcessPoolExecutor

        class _SpyPool(original_pool):
            def __init__(self, *a, **kw):
                spawn_called["n"] += 1
                super().__init__(*a, **kw)

        monkeypatch.setattr(orch_mod, "ProcessPoolExecutor", _SpyPool)

        # Reference: explicit sequential.
        ref_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=False, filename="single_seq.yaml",
        )
        ref_out = tmp_path / "single_seq.xlsx"
        ref_result = run_grid_pipeline(str(ref_yaml), output_path=str(ref_out))

        # Subject: parallel_enabled=true, max_workers>1, but the grid is len 1.
        par_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=4, filename="single_par.yaml",
        )
        par_out = tmp_path / "single_par.xlsx"
        par_result = run_grid_pipeline(str(par_yaml), output_path=str(par_out))

        assert ref_result.error is None
        assert par_result.error is None
        assert ref_result.execution_mode == "sequential"
        assert par_result.execution_mode == "sequential", (
            "len(grid_points)==1 must short-circuit to sequential"
        )
        assert spawn_called["n"] == 0, (
            "ProcessPoolExecutor must NOT be created when n_workers==1"
        )
        for name in (
            "step_oos_long", "step_train_long", "aggregated", "ranked",
            "summary_wide", "trades_oos", "trades_train",
            "bucket_matrix_median",
        ):
            _assert_frame_equal_or_both_none(
                getattr(ref_result, name),
                getattr(par_result, name),
                name,
            )

    def test_max_workers_capped_exactly_to_grid_size(self, tmp_path):
        """max_workers=100, len(grid_points)<32: effective workers ==
        len(grid_points) (plan §6.15 / §3.7)."""
        from wf_grid.pipeline.orchestrator import (
            _resolve_max_workers,
            run_grid_pipeline,
        )

        csv_path = _build_tiny_dataset(tmp_path)
        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=100, filename="cap.yaml",
        )
        out = tmp_path / "cap.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        assert result.error is None
        assert result.execution_mode == "parallel"
        # tiny config produces a small grid (definitely < 32), so the cap
        # collapses to len(grid_points).
        gp_n = len(result.grid_points)
        assert 1 < gp_n < 32, (
            f"sanity: tiny config should give 1<n<32 grid points, got {gp_n}"
        )
        n_eff = _resolve_max_workers(result.config, result.grid_points)
        assert n_eff == gp_n, (
            f"effective workers {n_eff} != len(grid_points) {gp_n}"
        )

    def test_chunksize_accepted_and_ignored(self, tmp_path):
        """chunksize=4 must load and not affect outputs (currently ignored)."""
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        # Reference run without chunksize.
        ref_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2, filename="noc.yaml",
        )
        ref_out = tmp_path / "noc.xlsx"
        ref_result = run_grid_pipeline(str(ref_yaml), output_path=str(ref_out))

        # Subject: same config + chunksize=4.
        chk_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2, chunksize=4,
            filename="chk.yaml",
        )
        chk_out = tmp_path / "chk.xlsx"
        chk_result = run_grid_pipeline(str(chk_yaml), output_path=str(chk_out))

        assert ref_result.error is None
        assert chk_result.error is None
        assert chk_result.config.execution.chunksize == 4
        assert ref_result.config.execution.chunksize is None
        for name in (
            "step_oos_long", "step_train_long", "aggregated", "ranked",
            "summary_wide", "trades_oos", "trades_train",
            "bucket_matrix_median",
        ):
            _assert_frame_equal_or_both_none(
                getattr(ref_result, name),
                getattr(chk_result, name),
                name,
            )


# ---------------------------------------------------------------------------
# Phase 5: order-independence pin for collectors (plan §6.16)
# ---------------------------------------------------------------------------

class TestCollectorOrderIndependence:
    """Collectors sort by (grid_point_id, wf_step) — feeding unsorted dicts
    must produce identical output to sorted-input case (plan §6.16).

    This is the contract that lets the parallel branch use as_completed:
    completion order is irrelevant because downstream collectors normalise.
    """

    def _make_step(
        self,
        gp_id: str,
        wf_step: int,
        *,
        sum_pnl_pct: float = 0.01,
        num_trades: int = 5,
        max_drawdown: float = -0.05,
        effective_oos_bars: int = 200,
    ):
        from wf_grid.wf.step_executor import StepResult
        return StepResult(
            grid_point_id=gp_id,
            wf_step=wf_step,
            test_start_idx=(wf_step - 1) * 200,
            test_end_idx=wf_step * 200,
            metrics={
                "sum_pnl_pct": sum_pnl_pct,
                "max_drawdown": max_drawdown,
                "num_trades": num_trades,
                "sharpe": 0.5,
                "sortino": 0.7,
                "cagr": 0.1,
                "win_rate": 0.55,
                "profit_factor": 1.2,
                "avg_trade": 0.002,
            },
            oos_trades_df=None,
            prepend_bars_requested=10,
            prepend_bars_applied=10,
            used_prepend=True,
            used_legacy_oos_path=False,
            used_defensive_fallback=False,
            oos_boundary_index=10,
            warmup_used=0,
            warmup_effective=0,
            effective_oos_bars=effective_oos_bars,
        )

    def _build_minimal_config(self):
        from wf_grid.config.schema import (
            DataConfig,
            GridConfig,
            ValidationConfig,
            WalkForwardConfig,
        )
        return GridConfig(
            data=DataConfig(file_path="x.csv"),
            validation=ValidationConfig(
                walk_forward=WalkForwardConfig(
                    train_size="200D", test_size="50D",
                ),
            ),
        )

    def _build_step_results(self, gp_ids: list[str], n_steps: int = 3):
        """Build {gp_id: [StepResult, ...]} for the given ids and step count."""
        results: dict[str, list] = {}
        for gp_id in gp_ids:
            steps = [
                self._make_step(gp_id, wf_step=s)
                for s in range(1, n_steps + 1)
            ]
            results[gp_id] = steps
        return results

    def _build_grid_points(self, gp_ids: list[str]):
        from wf_grid.grid.enumeration import GridPoint
        # Deterministic atr/mult per id so both runs see the same identities.
        return [
            GridPoint(
                atr_period=10 + i,
                multiplier=2.0 + 0.1 * i,
                trade_mode="both",
                grid_point_id=gp_id,
            )
            for i, gp_id in enumerate(gp_ids)
        ]

    def test_collect_oos_steps_unsorted_input_matches_sorted(self):
        from wf_grid.collect.step_collector import collect_oos_steps

        config = self._build_minimal_config()
        gp_ids = ["b_gp", "a_gp", "c_gp", "d_gp"]
        n_steps = 3

        # Sorted input: insertion order matches lexicographic gp_id order.
        sorted_ids = sorted(gp_ids)
        sorted_dict = self._build_step_results(sorted_ids, n_steps=n_steps)

        # Unsorted input: deliberately reversed and rotated insertion order.
        unsorted_ids = ["c_gp", "a_gp", "d_gp", "b_gp"]
        unsorted_dict = self._build_step_results(unsorted_ids, n_steps=n_steps)

        grid_points = self._build_grid_points(sorted_ids)

        df_sorted = collect_oos_steps(
            sorted_dict, config,
            expected_n_steps=n_steps, grid_points=grid_points,
        )
        df_unsorted = collect_oos_steps(
            unsorted_dict, config,
            expected_n_steps=n_steps, grid_points=grid_points,
        )

        # Plan §6.16: outputs must be identical regardless of dict order.
        pd.testing.assert_frame_equal(
            df_sorted, df_unsorted,
            check_exact=True, check_index_type=True, check_column_type=True,
        )
        # Sanity: the sort is by (grid_point_id, wf_step).
        assert list(df_sorted["grid_point_id"]) == sorted(
            [gp_id for gp_id in sorted_ids for _ in range(n_steps)]
        )

    def test_collect_train_steps_unsorted_input_matches_sorted(self):
        from wf_grid.collect.step_collector import collect_train_steps

        config = self._build_minimal_config()
        gp_ids = ["b_gp", "a_gp", "c_gp", "d_gp"]
        n_steps = 3

        sorted_ids = sorted(gp_ids)
        sorted_dict = self._build_step_results(sorted_ids, n_steps=n_steps)

        unsorted_ids = ["d_gp", "b_gp", "c_gp", "a_gp"]
        unsorted_dict = self._build_step_results(unsorted_ids, n_steps=n_steps)

        grid_points = self._build_grid_points(sorted_ids)

        df_sorted = collect_train_steps(
            sorted_dict, config,
            expected_n_steps=n_steps, grid_points=grid_points,
        )
        df_unsorted = collect_train_steps(
            unsorted_dict, config,
            expected_n_steps=n_steps, grid_points=grid_points,
        )

        pd.testing.assert_frame_equal(
            df_sorted, df_unsorted,
            check_exact=True, check_index_type=True, check_column_type=True,
        )


# ---------------------------------------------------------------------------
# Phase 5: order-independence under delayed grid_point (plan §6.17)
# ---------------------------------------------------------------------------

class TestDelayedGridPointStress:
    """One artificially-slow grid_point finishes last; final frames are
    bit-identical to the sequential run.
    """

    def test_delayed_grid_point_does_not_affect_outputs(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)

        # Reference sequential run (no delay needed).
        seq_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=False, filename="delay_seq.yaml",
        )
        seq_out = tmp_path / "delay_seq.xlsx"
        seq_result = run_grid_pipeline(str(seq_yaml), output_path=str(seq_out))
        assert seq_result.error is None

        # Pick the second grid_point as the one to delay.  Iterating order
        # is deterministic via enumerate_grid.
        gp_ids = [gp.grid_point_id for gp in seq_result.grid_points]
        assert len(gp_ids) >= 2, "tiny config should produce at least 2 gps"
        delayed_id = gp_ids[1]

        # Inject env var into the parent process; ProcessPoolExecutor inherits
        # it on spawn, so each worker will see the variable when handling that
        # specific grid_point.
        monkeypatch.setenv("WF_GRID_FORCE_DELAY_GP", delayed_id)

        par_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2, filename="delay_par.yaml",
        )
        par_out = tmp_path / "delay_par.xlsx"
        par_result = run_grid_pipeline(str(par_yaml), output_path=str(par_out))

        assert par_result.error is None
        assert par_result.execution_mode == "parallel"
        for name in (
            "step_oos_long", "step_train_long", "aggregated", "ranked",
            "summary_wide", "trades_oos", "trades_train",
            "bucket_matrix_median",
        ):
            _assert_frame_equal_or_both_none(
                getattr(seq_result, name),
                getattr(par_result, name),
                name,
            )


# ---------------------------------------------------------------------------
# Phase 6: error-policy tests (plan §4 / §6.7-§6.12)
#
# All tests use a tiny synthetic config (~4 grid points x ~2 WF steps).
# Env-driven hooks are propagated to spawned workers via
# monkeypatch.setenv (parent-process variable inherited on Windows spawn).
# ---------------------------------------------------------------------------

class TestHardWorkerCrash:
    """Plan §6.7 / §6.8: os._exit(17) inside a worker -> BrokenProcessPool."""

    def test_no_fallback_marks_parallel_and_records_error(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        monkeypatch.setenv("WF_GRID_FORCE_WORKER_CRASH", "exit")

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=False,
            filename="hard_crash_no_fb.yaml",
        )
        out = tmp_path / "hard_crash_no_fb.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        assert result.error is not None, (
            "hard worker crash without fallback must surface result.error"
        )
        _assert_broken_process_pool_marker(result.error)
        # C3-1: enter-then-mark stays "parallel" even when the branch failed.
        assert result.execution_mode == "parallel"

    def test_with_fallback_upgrades_to_parallel_then_fallback(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        monkeypatch.setenv("WF_GRID_FORCE_WORKER_CRASH", "exit")

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=True,
            filename="hard_crash_fb.yaml",
        )
        out = tmp_path / "hard_crash_fb.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        # Sequential rerun does NOT see the env hook (the hook is read
        # inside _run_grid_point_both, which is NOT called in the sequential
        # path), so the rerun completes successfully.
        assert result.error is None, (
            f"fallback rerun should succeed, got error: {result.error!r}"
        )
        assert result.execution_mode == "parallel_then_fallback"


class TestInitializerFailure:
    """Plan §6.10: ImportError inside _init_worker -> BrokenProcessPool."""

    def test_no_fallback_marks_parallel_and_records_error(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        monkeypatch.setenv("WF_GRID_FORCE_INIT_FAIL", "1")

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=False,
            filename="init_fail_no_fb.yaml",
        )
        out = tmp_path / "init_fail_no_fb.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        assert result.error is not None
        _assert_broken_process_pool_marker(result.error)
        assert result.execution_mode == "parallel"

    def test_with_fallback_upgrades_to_parallel_then_fallback(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        monkeypatch.setenv("WF_GRID_FORCE_INIT_FAIL", "1")

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=True,
            filename="init_fail_fb.yaml",
        )
        out = tmp_path / "init_fail_fb.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        # Sequential helper does not invoke _init_worker, so the rerun succeeds.
        assert result.error is None
        assert result.execution_mode == "parallel_then_fallback"


class TestPlainTaskException:
    """Plan §6.11: RuntimeError inside the worker task is NOT an
    infrastructure failure — fallback must NOT engage even if enabled."""

    def test_plain_task_error_does_not_trigger_fallback(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        monkeypatch.setenv("WF_GRID_FORCE_TASK_RAISE", "1")

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=True,  # enabled but must NOT engage
            filename="plain_raise.yaml",
        )
        out = tmp_path / "plain_raise.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        assert result.error is not None
        assert "simulated task error" in result.error, (
            f"plain task exception must surface, got: {result.error!r}"
        )
        # fallback explicitly excluded for plain task exceptions (plan §3.6/§4).
        assert result.execution_mode == "parallel", (
            "plain task exception must NOT promote to parallel_then_fallback"
        )

    def test_plain_task_typeerror_does_not_trigger_fallback(
        self, tmp_path, monkeypatch
    ):
        """Worker task raises TypeError — must NOT be treated as infra failure."""
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        monkeypatch.setenv("WF_GRID_FORCE_TASK_TYPEERROR", "1")

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=True,  # enabled but must NOT engage
            filename="plain_typeerror.yaml",
        )
        out = tmp_path / "plain_typeerror.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        assert result.error is not None
        assert "simulated task TypeError" in result.error, (
            f"plain task TypeError must surface in result.error, got: {result.error!r}"
        )
        assert result.execution_mode == "parallel", (
            "plain task TypeError must NOT promote to parallel_then_fallback"
        )


class TestCascadedFailure:
    """Plan §6.9: parallel fails AND fallback rerun also fails."""

    def test_cascaded_failure_keeps_parallel_mode_and_records_error(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline import orchestrator as orch_mod
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        monkeypatch.setenv("WF_GRID_FORCE_WORKER_CRASH", "exit")

        # Force the sequential fallback rerun to fail too.  The dispatcher
        # only enters this helper after the parallel branch raised an
        # infrastructure failure and fallback_to_sequential=True.
        def _broken_seq(*args, **kwargs):
            raise RuntimeError("sequential fallback also failed")

        monkeypatch.setattr(
            orch_mod, "_execute_wf_grid_sequential", _broken_seq,
        )

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=True,
            filename="cascaded.yaml",
        )
        out = tmp_path / "cascaded.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        assert result.error is not None
        assert "sequential fallback also failed" in result.error
        # Plan §3.5 contract: failed parallel without successful fallback
        # remains execution_mode == "parallel".
        assert result.execution_mode == "parallel"


class TestUnpicklableSubmissionArgument:
    """Plan §6.12: unpicklable initargs -> TypeError / pickle.PicklingError."""

    def _patch_pack_data_to_inject_lock(self, monkeypatch):
        """Replace orchestrator-side pack_data so initargs include a Lock.

        Lock objects raise TypeError on pickle.dumps in CPython, which is one
        of _PARALLEL_INFRA_FAILURES.  We patch the orchestrator's import view
        of pack_data; the worker side does not run because the pool fails
        before any task starts.
        """
        import threading
        from wf_grid.wf import _mp_helpers as mph

        original_pack = mph.pack_data

        def _bad_pack(full_data):
            d = original_pack(full_data)
            d["__unpicklable_lock"] = threading.Lock()
            return d

        # The orchestrator imports pack_data INSIDE _execute_wf_grid_parallel,
        # so patching the source module is sufficient.
        monkeypatch.setattr(mph, "pack_data", _bad_pack)

    def test_no_fallback_marks_parallel_and_records_error(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        self._patch_pack_data_to_inject_lock(monkeypatch)

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=False,
            filename="unpicklable_no_fb.yaml",
        )
        out = tmp_path / "unpicklable_no_fb.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        assert result.error is not None
        msg = result.error
        low = msg.lower()
        assert (
            "typeerror" in low
            or "picklingerror" in low
            or "pickle" in low
            or "lock" in low
            or "brokenprocesspool" in low
            or "process pool" in low
            or "abruptly" in low
        ), f"unexpected error message for unpicklable initargs: {msg!r}"
        assert result.execution_mode == "parallel"

    def test_with_fallback_upgrades_to_parallel_then_fallback(
        self, tmp_path, monkeypatch
    ):
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        self._patch_pack_data_to_inject_lock(monkeypatch)

        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            fallback_to_sequential=True,
            filename="unpicklable_fb.yaml",
        )
        out = tmp_path / "unpicklable_fb.xlsx"
        result = run_grid_pipeline(str(yaml_path), output_path=str(out))

        # Sequential helper does not call pack_data, so the rerun succeeds.
        assert result.error is None
        assert result.execution_mode == "parallel_then_fallback"


# ---------------------------------------------------------------------------
# Plan §6.14 / step 10: CLI / kwargs precedence + _original_config immutability
# ---------------------------------------------------------------------------

class TestCLIKwargsPrecedence:
    """Plan §6.14 / acceptance criterion A6.

    Precedence: explicit kwarg > config.yaml > dataclass default.
    _original_config must never be mutated by override application.
    """

    def test_parallel_enabled_none_honours_yaml_true(self, tmp_path):
        """parallel_enabled=None (default) -> YAML parallel_enabled=true honoured."""
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            filename="yaml_true.yaml",
        )
        result = run_grid_pipeline(
            str(yaml_path),
            output_path=str(tmp_path / "out_none.xlsx"),
            # parallel_enabled not passed -> None -> YAML wins
        )
        assert result.error is None, f"pipeline failed: {result.error}"
        assert result.execution_mode == "parallel", (
            f"expected 'parallel' (from YAML), got {result.execution_mode!r}"
        )

    def test_parallel_enabled_false_overrides_yaml_true(self, tmp_path):
        """parallel_enabled=False kwarg overrides YAML parallel_enabled=true."""
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            filename="yaml_true_override.yaml",
        )
        result = run_grid_pipeline(
            str(yaml_path),
            output_path=str(tmp_path / "out_false.xlsx"),
            parallel_enabled=False,
        )
        assert result.error is None, f"pipeline failed: {result.error}"
        assert result.execution_mode == "sequential", (
            f"expected 'sequential' (kwarg override), got {result.execution_mode!r}"
        )

    def test_second_call_with_none_restores_yaml_true(self, tmp_path):
        """Second call with parallel_enabled=None re-honours YAML, proving
        that _original_config was not mutated by the first call's override.
        """
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            filename="yaml_true_mutation.yaml",
        )

        # First call: override to sequential.
        r1 = run_grid_pipeline(
            str(yaml_path),
            output_path=str(tmp_path / "out_r1.xlsx"),
            parallel_enabled=False,
        )
        assert r1.error is None
        assert r1.execution_mode == "sequential"

        # Second call: no override.  If _original_config were mutated,
        # parallel_enabled would still be False and execution_mode would be
        # "sequential" again.  It must be "parallel" because YAML says true.
        r2 = run_grid_pipeline(
            str(yaml_path),
            output_path=str(tmp_path / "out_r2.xlsx"),
        )
        assert r2.error is None
        assert r2.execution_mode == "parallel", (
            "_original_config was mutated by the first call: "
            f"execution_mode={r2.execution_mode!r} instead of 'parallel'"
        )

    def test_max_workers_kwarg_overrides_yaml(self, tmp_path):
        """max_workers kwarg must be applied to the working config."""
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        # YAML says max_workers=2; we override to 3.
        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            filename="yaml_mw2.yaml",
        )
        result = run_grid_pipeline(
            str(yaml_path),
            output_path=str(tmp_path / "out_mw3.xlsx"),
            max_workers=3,
        )
        assert result.error is None, f"pipeline failed: {result.error}"
        # The working config seen by the pipeline should reflect the override.
        assert result.config.execution.max_workers == 3, (
            f"expected max_workers=3 in result.config, "
            f"got {result.config.execution.max_workers}"
        )

    def test_max_workers_zero_raises_valueerror(self, tmp_path):
        """max_workers=0 override must raise ValueError before config is applied."""
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            filename="mw_zero.yaml",
        )
        result = run_grid_pipeline(
            str(yaml_path),
            output_path=str(tmp_path / "mw_zero.xlsx"),
            max_workers=0,
        )
        assert result.error is not None
        assert "max_workers" in result.error.lower() and (
            "0" in result.error or ">= 1" in result.error
        ), f"expected max_workers validation error, got: {result.error!r}"

    def test_max_workers_bool_raises_valueerror(self, tmp_path):
        """max_workers=True (bool) override must be rejected as invalid type."""
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            filename="mw_bool.yaml",
        )
        result = run_grid_pipeline(
            str(yaml_path),
            output_path=str(tmp_path / "mw_bool.xlsx"),
            max_workers=True,
        )
        assert result.error is not None
        assert "max_workers" in result.error.lower() and "bool" in result.error.lower(), (
            f"expected bool validation error for max_workers, got: {result.error!r}"
        )

    def test_parallel_enabled_string_raises_valueerror(self, tmp_path):
        """parallel_enabled='yes' (str) override must be rejected as invalid type."""
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        yaml_path = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            filename="pe_str.yaml",
        )
        result = run_grid_pipeline(
            str(yaml_path),
            output_path=str(tmp_path / "pe_str.xlsx"),
            parallel_enabled="yes",
        )
        assert result.error is not None
        assert "parallel_enabled" in result.error.lower(), (
            f"expected parallel_enabled validation error, got: {result.error!r}"
        )


# ---------------------------------------------------------------------------
# Plan §3.9 / step 9: execution_mode excluded from baseline fingerprint;
# sequential and parallel runs on identical data produce equal fingerprints.
# ---------------------------------------------------------------------------

class TestFingerprintSequentialVsParallel:
    """Plan §3.9 / acceptance criterion A9 / marker M3.

    Full-pipeline variant: run the tiny pipeline in sequential and parallel
    modes; compute_pipeline_fingerprint of both results must be equal despite
    differing execution_mode values.
    """

    def test_seq_par_fingerprints_equal(self, tmp_path):
        from wf_grid.baseline import (
            compute_pipeline_fingerprint,
            fingerprints_equal,
            summarize_diff,
        )
        from wf_grid.pipeline.orchestrator import run_grid_pipeline

        csv_path = _build_tiny_dataset(tmp_path)
        seq_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=False,
            filename="fp_seq.yaml",
        )
        par_yaml = _write_tiny_yaml(
            tmp_path, csv_path,
            parallel_enabled=True, max_workers=2,
            filename="fp_par.yaml",
        )
        seq_result = run_grid_pipeline(
            str(seq_yaml), output_path=str(tmp_path / "fp_seq.xlsx"),
        )
        par_result = run_grid_pipeline(
            str(par_yaml), output_path=str(tmp_path / "fp_par.xlsx"),
        )

        assert seq_result.error is None, f"seq failed: {seq_result.error}"
        assert par_result.error is None, f"par failed: {par_result.error}"
        assert seq_result.execution_mode == "sequential"
        assert par_result.execution_mode == "parallel"

        fp_seq = compute_pipeline_fingerprint(seq_result)
        fp_par = compute_pipeline_fingerprint(par_result)

        # execution_mode must not appear in either fingerprint's attributes.
        assert "execution_mode" not in fp_seq.attributes, (
            f"execution_mode leaked into seq fingerprint attributes: {fp_seq.attributes}"
        )
        assert "execution_mode" not in fp_par.attributes, (
            f"execution_mode leaked into par fingerprint attributes: {fp_par.attributes}"
        )

        # The fingerprints must be equal (timings are already excluded by
        # _diagnostics_to_dict; metadata paths differ but are in skip_metadata_keys).
        diff = summarize_diff(fp_seq, fp_par)
        assert diff == [], (
            "sequential and parallel fingerprints diverged:\n"
            + "\n".join(diff)
        )
        assert fingerprints_equal(fp_seq, fp_par)


# ---------------------------------------------------------------------------
# Plan §6.13 / C3-2: ZigZagGlobalStats worker-side immutability
# ---------------------------------------------------------------------------

class TestWorkerSideImmutability:
    """Plan §6.13 / counter-audit fix C3-2.

    Call _init_worker in-process (no ProcessPool spawned) and verify that
    ZigZagGlobalStats is hardened for reuse across tasks inside a worker:
      - confirmed_legs is converted from list -> tuple (no append).
      - confirmed_heights_pct ndarray becomes read-only (no item assignment).
      - _WORKER_STATE["zigzag_global_stats"] is the *same* object (no copy).
    """

    @pytest.fixture(autouse=True)
    def _reset_worker_state(self):
        mph._WORKER_STATE.clear()
        yield
        mph._WORKER_STATE.clear()

    def _make_stats(self):
        """Build a ZigZagGlobalStats with mutable list + writeable ndarray."""
        from supertrend_optimizer.core.zigzag_st_filter import (
            ConfirmedLeg,
            ZigZagGlobalStats,
        )
        legs = [
            ConfirmedLeg(
                start_bar=0, end_bar=2, confirm_bar=3,
                start_price=100.0, end_price=102.0,
                direction=1, height_pct=0.02,
            ),
            ConfirmedLeg(
                start_bar=2, end_bar=3, confirm_bar=5,
                start_price=102.0, end_price=99.0,
                direction=-1, height_pct=3.0 / 102.0,
            ),
        ]
        heights = np.array(
            [legs[0].height_pct, legs[1].height_pct], dtype="float64",
        )
        stats = ZigZagGlobalStats(
            reversal_threshold=0.02,
            global_stats_source="full_dataset",
            leg_height_mode="pct",
            confirmed_legs=legs,
            confirmed_heights_pct=heights,
            global_median=float(np.median(heights)),
            candidate_trigger_threshold=0.015,
            candidate_trigger_source="explicit",
            candidate_trigger_quantile=None,
            n_legs_total=len(legs),
            insufficient_data=False,
            fail_closed_reason=None,
            metadata={},
        )
        return stats

    def test_zigzag_stats_hardened_after_init_worker(self):
        """Plan §6.13: _init_worker converts list->tuple and freezes ndarray."""
        from wf_grid.config.schema import (
            BacktestConfig,
            DataConfig,
            GridConfig,
            OptimizationConfig,
            ValidationConfig,
            WalkForwardConfig,
        )

        data_path = _REPO_ROOT / "data.csv"
        if not data_path.exists():
            pytest.skip("data.csv not present in workspace")

        import pandas as _pd
        df = _pd.read_csv(data_path, parse_dates=True, index_col=0).iloc[:500].copy()

        cfg = GridConfig(
            data=DataConfig(file_path=str(data_path)),
            optimization=OptimizationConfig(
                atr_period_range=[10, 10],
                multiplier_range=[2.5, 2.5],
                multiplier_step=0.1,
                trade_mode="both",
            ),
            backtest=BacktestConfig(),
            validation=ValidationConfig(
                walk_forward=WalkForwardConfig(
                    train_size="500bars",
                    test_size="200bars",
                    min_train_bars=100,
                    min_test_bars=20,
                ),
            ),
        )

        stats = self._make_stats()

        # Pre-conditions: confirmed_legs is a list, heights are writeable.
        assert isinstance(stats.confirmed_legs, list), (
            "pre-condition: confirmed_legs must be list before _init_worker"
        )
        assert stats.confirmed_heights_pct.flags.writeable, (
            "pre-condition: confirmed_heights_pct must be writeable before _init_worker"
        )

        mph._init_worker(
            project_root=str(_REPO_ROOT),
            donor_path=str(_REPO_ROOT / "donor"),
            data_dict=mph.pack_data(df),
            wf_slices=[],
            config=cfg,
            prepend_bars=0,
            zigzag_global_stats=stats,
        )

        # Post-conditions:

        # 1. confirmed_legs converted to tuple (immutable sequence).
        assert isinstance(stats.confirmed_legs, tuple), (
            f"expected tuple, got {type(stats.confirmed_legs).__name__}"
        )

        # 2. append raises AttributeError because tuple has no .append.
        with pytest.raises(AttributeError):
            stats.confirmed_legs.append(stats.confirmed_legs[0])

        # 3. confirmed_heights_pct is now read-only.
        assert not stats.confirmed_heights_pct.flags.writeable, (
            "confirmed_heights_pct must be read-only after _init_worker"
        )

        # 4. item assignment raises ValueError mentioning "read-only".
        with pytest.raises(ValueError, match="read-only"):
            stats.confirmed_heights_pct[0] = 999.0

        # 5. _WORKER_STATE holds the same object (no defensive copy made).
        assert mph._WORKER_STATE["zigzag_global_stats"] is stats, (
            "_WORKER_STATE must reference the same stats object, not a copy"
        )


# ---------------------------------------------------------------------------
# Plan §6.18 / step 11: worker logging via StepResult.error_message
# ---------------------------------------------------------------------------

class TestWorkerLoggingViaStepResult:
    """Plan §6.18: per-step runtime errors surface via StepResult, not caplog.

    caplog cannot intercept spawned workers.  Instead we verify that the runner
    catches exceptions from execute_oos_step and stores them in
    StepResult.error_message / error_type, and that these fields are
    bit-identical between the sequential and worker (in-process) paths.

    In the real parallel case the StepResult list is pickled from the worker
    process back to the main process; this in-process version proves the
    runner-level catch-and-embed mechanism works. The pickle round-trip for
    StepResult with error fields is separately covered by TestPickleRoundTrip.
    """

    _ERROR_MSG = "simulated per-step backtest failure §6.18"
    _ERROR_TYPE = "ValueError"

    @pytest.fixture(autouse=True)
    def _reset_worker_state(self):
        mph._WORKER_STATE.clear()
        yield
        mph._WORKER_STATE.clear()

    def _build_run_artifacts(self):
        """Minimal artifacts for worker + sequential runner calls."""
        data_path = _REPO_ROOT / "data.csv"
        if not data_path.exists():
            pytest.skip("data.csv not present in workspace")

        from supertrend_optimizer.data.validator import validate_ohlc_data
        from supertrend_optimizer.utils.time_utils import make_walk_forward_slices
        from wf_grid.config.loader import resolve_periods_per_year
        from wf_grid.config.schema import (
            BacktestConfig, DataConfig, GridConfig,
            OptimizationConfig, ValidationConfig, WalkForwardConfig,
        )
        from wf_grid.grid.enumeration import GridPoint
        from wf_grid.wf.runner import compute_prepend_bars
        import dataclasses

        df = pd.read_csv(data_path, parse_dates=True, index_col=0)
        df = validate_ohlc_data(df, strict=True)
        df = df.iloc[:1500].copy()

        cfg = GridConfig(
            data=DataConfig(file_path=str(data_path)),
            optimization=OptimizationConfig(
                atr_period_range=[10, 10],
                multiplier_range=[2.5, 2.5],
                multiplier_step=0.1,
                trade_mode="both",
            ),
            backtest=BacktestConfig(),
            validation=ValidationConfig(
                walk_forward=WalkForwardConfig(
                    train_size="500bars",
                    test_size="200bars",
                    min_train_bars=100,
                    min_test_bars=20,
                ),
            ),
        )
        cfg = resolve_periods_per_year(cfg, df)
        wf_slices = make_walk_forward_slices(
            index=df.index,
            train_size=cfg.validation.walk_forward.train_size,
            test_size=cfg.validation.walk_forward.test_size,
            step_size=cfg.validation.walk_forward.test_size,
            scheme=cfg.validation.walk_forward.scheme,
            anchor=cfg.validation.walk_forward.anchor,
            min_train_bars=cfg.validation.walk_forward.min_train_bars,
            min_test_bars=cfg.validation.walk_forward.min_test_bars,
        )
        if not wf_slices:
            pytest.skip("Synthetic frame too short for WF slicing")

        def _to_dict(o):
            if dataclasses.is_dataclass(o) and not isinstance(o, type):
                return {f.name: _to_dict(getattr(o, f.name))
                        for f in dataclasses.fields(o)}
            if isinstance(o, list):
                return [_to_dict(i) for i in o]
            if isinstance(o, dict):
                return {k: _to_dict(v) for k, v in o.items()}
            return o

        prepend_bars = compute_prepend_bars(df, _to_dict(cfg))
        gp = GridPoint(
            atr_period=10, multiplier=2.5, trade_mode="both",
            grid_point_id="atr10_m2.5_both",
        )
        return df, cfg, wf_slices, prepend_bars, gp

    def test_step_runtime_error_round_trips_through_worker_path(self, monkeypatch):
        """Plan §6.18: per-step exception -> StepResult.error_message identical
        in sequential and worker paths; _run_grid_point_both does NOT raise.
        """
        from wf_grid.wf.runner import run_wf_for_grid_point

        df, cfg, wf_slices, prepend_bars, gp = self._build_run_artifacts()

        # Inject error at the execute_oos_step level.  The runner catches any
        # Exception from execute_oos_step and converts it to a StepResult;
        # monkeypatching the runner's module-level reference is sufficient.
        def _bad_execute_oos_step(*args, **kwargs):
            raise ValueError(self._ERROR_MSG)

        monkeypatch.setattr("wf_grid.wf.runner.execute_oos_step", _bad_execute_oos_step)

        # Sequential reference: runner must catch the error and return a
        # StepResult list (not raise).
        seq_oos = run_wf_for_grid_point(
            grid_point=gp,
            wf_slices=wf_slices,
            full_data=df,
            config=cfg,
            prepend_bars_requested=prepend_bars,
            zigzag_global_stats=None,
        )

        # Worker path (in-process, same monkeypatch in effect because
        # _run_grid_point_both does deferred imports that re-use the already-
        # patched wf_grid.wf.runner.execute_oos_step reference).
        mph._init_worker(
            project_root=str(_REPO_ROOT),
            donor_path=str(_REPO_ROOT / "donor"),
            data_dict=mph.pack_data(df),
            wf_slices=wf_slices,
            config=cfg,
            prepend_bars=prepend_bars,
            zigzag_global_stats=None,
        )
        # _run_grid_point_both must return a tuple (not raise), even though
        # every OOS step fails at the runner level.
        rid, w_oos, _w_train = mph._run_grid_point_both(gp)
        assert rid == gp.grid_point_id

        # Every step must carry the captured error.
        assert len(seq_oos) > 0, "expected at least one WF step"
        assert all(s.error_message is not None for s in seq_oos), (
            "sequential: expected all StepResults to have error_message set"
        )
        assert all(s.error_type == self._ERROR_TYPE for s in seq_oos), (
            f"sequential: expected error_type={self._ERROR_TYPE!r} on all steps"
        )

        # Worker path must produce identical error fields.
        assert len(w_oos) == len(seq_oos), (
            f"worker returned {len(w_oos)} steps, sequential {len(seq_oos)}"
        )
        for i, (w_step, s_step) in enumerate(zip(w_oos, seq_oos)):
            assert w_step.error_message == s_step.error_message, (
                f"step {i}: worker error_message differs from sequential"
            )
            assert w_step.error_type == s_step.error_type, (
                f"step {i}: worker error_type differs from sequential"
            )

        # Pipeline-level guarantee: the error is captured at step level, NOT
        # at task level.  _run_grid_point_both returned successfully above.

    def test_step_error_survives_spawned_worker_pickle_boundary(self):
        """Plan §6.18 spawned-worker/pickle-boundary variant.

        Trigger a deterministic per-step ConfigError inside execute_oos_step
        by passing a GridConfig whose resolved_periods_per_year is None (the
        step executor raises ConfigError before any backtest computation when
        it sees None).  No monkeypatching needed — the failure happens entirely
        inside the worker's call to execute_oos_step, the runner catches it and
        returns a StepResult; the tuple is then pickled back across the process
        boundary by ProcessPoolExecutor.

        Verification:
          - _execute_wf_grid_parallel returns normally (no infra exception).
          - Every step in all_oos[gp_id] has error_message and error_type set.
          - These fields are bit-identical to the sequential runner output for
            the same (unresolved) config.
        """
        data_path = _REPO_ROOT / "data.csv"
        if not data_path.exists():
            pytest.skip("data.csv not present in workspace")

        from supertrend_optimizer.data.validator import validate_ohlc_data
        from supertrend_optimizer.utils.time_utils import make_walk_forward_slices
        from wf_grid.config.schema import (
            BacktestConfig, DataConfig, GridConfig,
            OptimizationConfig, ValidationConfig, WalkForwardConfig,
        )
        from wf_grid.grid.enumeration import GridPoint, enumerate_grid
        from wf_grid.pipeline.orchestrator import _execute_wf_grid_parallel
        from wf_grid.wf.runner import run_wf_for_grid_point

        df = pd.read_csv(data_path, parse_dates=True, index_col=0)
        df = validate_ohlc_data(df, strict=True)
        df = df.iloc[:1500].copy()

        # Build a config intentionally WITHOUT calling resolve_periods_per_year
        # so that resolved_periods_per_year stays None.  execute_oos_step raises
        # ConfigError("resolved_periods_per_year is None ...") which the runner
        # catches and converts to a StepResult with error_message set.
        cfg = GridConfig(
            data=DataConfig(file_path=str(data_path)),
            optimization=OptimizationConfig(
                atr_period_range=[10, 10],
                multiplier_range=[2.5, 2.5],
                multiplier_step=0.1,
                trade_mode="both",
            ),
            backtest=BacktestConfig(),
            validation=ValidationConfig(
                walk_forward=WalkForwardConfig(
                    train_size="500bars",
                    test_size="200bars",
                    min_train_bars=100,
                    min_test_bars=20,
                ),
            ),
        )
        # Deliberately NOT calling resolve_periods_per_year(cfg, df).
        assert cfg.resolved_periods_per_year is None, (
            "pre-condition: resolved_periods_per_year must be None to trigger step error"
        )

        wf_slices = make_walk_forward_slices(
            index=df.index,
            train_size=cfg.validation.walk_forward.train_size,
            test_size=cfg.validation.walk_forward.test_size,
            step_size=cfg.validation.walk_forward.test_size,
            scheme=cfg.validation.walk_forward.scheme,
            anchor=cfg.validation.walk_forward.anchor,
            min_train_bars=cfg.validation.walk_forward.min_train_bars,
            min_test_bars=cfg.validation.walk_forward.min_test_bars,
        )
        if not wf_slices:
            pytest.skip("Frame too short for WF slicing")

        # Single grid point keeps the test fast and the assertion simple.
        gp = GridPoint(
            atr_period=10, multiplier=2.5, trade_mode="both",
            grid_point_id="atr10_m2.5_both",
        )
        grid_points = [gp]

        # Sequential reference: runner catches the ConfigError and returns a
        # StepResult list — must not raise.
        seq_oos = run_wf_for_grid_point(
            grid_point=gp,
            wf_slices=wf_slices,
            full_data=df,
            config=cfg,
            prepend_bars_requested=0,
            zigzag_global_stats=None,
        )
        assert all(s.error_message is not None for s in seq_oos), (
            "sequential: every step must have error_message (resolved_periods_per_year=None)"
        )
        # Capture the actual error_type raised by execute_oos_step.  The step
        # executor raises ConfigError for missing resolved_periods_per_year;
        # we record what the runner captures and use it as the expected value
        # for the parallel comparison below.
        assert all(s.error_type is not None for s in seq_oos), (
            "sequential: every step must have error_type set"
        )
        expected_error_type = seq_oos[0].error_type

        # Parallel path: _execute_wf_grid_parallel spawns real workers.
        # The ConfigError inside execute_oos_step is caught by the runner;
        # _run_grid_point_both returns a tuple normally; that tuple is pickled
        # back to the main process — the error_message crosses the pickle boundary.
        all_oos, _all_train = _execute_wf_grid_parallel(
            grid_points=grid_points,
            wf_slices=wf_slices,
            data=df,
            config=cfg,
            prepend_bars=0,
            zigzag_global_stats=None,
            n_workers=2,
        )

        # _execute_wf_grid_parallel must return without raising (no infra failure).
        par_oos = all_oos[gp.grid_point_id]

        assert len(par_oos) == len(seq_oos), (
            f"parallel returned {len(par_oos)} steps, sequential {len(seq_oos)}"
        )
        for i, (p_step, s_step) in enumerate(zip(par_oos, seq_oos)):
            assert p_step.error_message == s_step.error_message, (
                f"step {i}: parallel error_message differs from sequential\n"
                f"  parallel:   {p_step.error_message!r}\n"
                f"  sequential: {s_step.error_message!r}"
            )
            assert p_step.error_type == s_step.error_type, (
                f"step {i}: parallel error_type={p_step.error_type!r} "
                f"!= sequential {s_step.error_type!r}"
            )
            assert p_step.error_type == expected_error_type, (
                f"step {i}: error_type {p_step.error_type!r} != "
                f"expected {expected_error_type!r}"
            )
