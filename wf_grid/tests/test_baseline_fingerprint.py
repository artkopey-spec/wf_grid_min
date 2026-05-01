"""
Fast unit tests for the baseline fingerprint infrastructure (plan WP1).

These tests do NOT run the full pipeline (that lives in
``test_wp1_baseline_capture.py`` and is gated by ``slow`` + skip-when-no-
baseline).  They only pin the contract of the fingerprint helpers, so that
the WP1 regression gate stays trustworthy:

  - Fingerprints are deterministic (same input -> same output).
  - Fingerprints are sensitive to row-level, column-level and dtype
    differences.
  - ``None`` frames round-trip through JSON.
  - ``summarize_diff`` returns informative messages and, by default, ignores
    free-form metadata fields.
  - execution_mode and output_path are excluded from the fingerprint
    (plan §3.9 / WP-PAR / acceptance criterion A9 / marker M3).

Spec reference: Appendix A v1.1 §11, §17.1, §17.2.11, §18.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pytest

from wf_grid.baseline import (
    BASELINE_SCHEMA_VERSION,
    PipelineFingerprint,
    compute_dataframe_fingerprint,
    compute_pipeline_fingerprint,
    fingerprints_equal,
    load_baseline,
    save_baseline,
    summarize_diff,
)
from wf_grid.baseline.fingerprint import _NON_DETERMINISTIC_RESULT_ATTRIBUTES


# ---------------------------------------------------------------------------
# Stand-in for PipelineResult: we don't import the real one to keep the test
# focused on fingerprint behaviour rather than orchestrator coupling.
# ---------------------------------------------------------------------------


@dataclass
class _FakeDiagnostics:
    grid_size: int = 4
    n_wf_steps: int = 2
    step_status_counts: dict = field(default_factory=lambda: {"ok": 6, "no_trades": 2})
    tier_counts: dict = field(default_factory=lambda: {1: 2, 2: 1})
    top5_ranked: list = field(default_factory=lambda: ["gp1", "gp2"])
    timings: dict = field(default_factory=lambda: {"wf_execution": 12.7})  # ignored


@dataclass
class _FakeResult:
    step_oos_long: Optional[pd.DataFrame] = None
    step_train_long: Optional[pd.DataFrame] = None
    trades_oos: Optional[pd.DataFrame] = None
    trades_train: Optional[pd.DataFrame] = None
    aggregated: Optional[pd.DataFrame] = None
    ranked: Optional[pd.DataFrame] = None
    summary_wide: Optional[pd.DataFrame] = None
    bucket_matrix_median: Optional[pd.DataFrame] = None
    # Scalar bucket-matrix attributes mirror PipelineResult.bucket_matrix_status
    # / bucket_matrix_error; the plan (WP1.steps.2 / DoD #2) requires capturing
    # both alongside the DataFrame-valued artefacts.
    bucket_matrix_status: str = "disabled"
    bucket_matrix_error: Optional[str] = None
    diagnostics: Any = None


def _make_step_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "grid_point_id": ["gp1", "gp1", "gp2", "gp2"],
            "wf_step": [1, 2, 1, 2],
            "step_status": ["ok", "ok", "ok", "no_trades"],
            "sum_pnl_pct": [5.0, 7.5, 1.2, 0.0],
            "max_drawdown": [-0.10, -0.20, -0.05, 0.0],
        }
    )


def _make_trade_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": [1, 2],
            "entry_index": [10, 25],
            "exit_index": [15, 30],
            "pnl_pct": [1.5, -0.7],
        }
    )


# ---------------------------------------------------------------------------
# DataFrame fingerprint
# ---------------------------------------------------------------------------


class TestDataFrameFingerprint:
    def test_none_is_marked_absent(self):
        fp = compute_dataframe_fingerprint(None)
        assert fp == {"present": False}

    def test_empty_dataframe_keeps_columns_and_dtypes(self):
        df = pd.DataFrame({"a": pd.Series([], dtype="int64"), "b": pd.Series([], dtype="float64")})
        fp = compute_dataframe_fingerprint(df)
        assert fp["present"] is True
        assert fp["shape"] == [0, 2]
        assert fp["columns"] == ["a", "b"]
        assert fp["dtypes"] == {"a": "int64", "b": "float64"}
        assert "content_hash" in fp

    def test_deterministic_on_same_input(self):
        df = _make_step_df()
        fp1 = compute_dataframe_fingerprint(df)
        fp2 = compute_dataframe_fingerprint(df.copy())
        assert fp1 == fp2

    def test_row_level_change_changes_hash(self):
        df = _make_step_df()
        df2 = df.copy()
        df2.loc[0, "sum_pnl_pct"] = 5.0001
        fp1 = compute_dataframe_fingerprint(df)
        fp2 = compute_dataframe_fingerprint(df2)
        assert fp1["content_hash"] != fp2["content_hash"]
        assert fp1["column_stats"]["sum_pnl_pct"] != fp2["column_stats"]["sum_pnl_pct"]

    def test_added_column_changes_hash(self):
        df = _make_step_df()
        df2 = df.copy()
        df2["new_col"] = np.nan
        fp1 = compute_dataframe_fingerprint(df)
        fp2 = compute_dataframe_fingerprint(df2)
        assert fp1["columns"] != fp2["columns"]
        assert fp1["content_hash"] != fp2["content_hash"]

    def test_dtype_change_changes_hash(self):
        df = _make_step_df()
        df2 = df.copy()
        df2["sum_pnl_pct"] = df2["sum_pnl_pct"].astype("float32")
        fp1 = compute_dataframe_fingerprint(df)
        fp2 = compute_dataframe_fingerprint(df2)
        assert fp1["dtypes"] != fp2["dtypes"]
        assert fp1["content_hash"] != fp2["content_hash"]

    def test_numeric_stats_present_for_numeric_columns(self):
        fp = compute_dataframe_fingerprint(_make_step_df())
        stats = fp["column_stats"]["sum_pnl_pct"]
        assert stats["count"] == 4
        assert stats["sum"] == pytest.approx(5.0 + 7.5 + 1.2 + 0.0)
        assert stats["mean"] == pytest.approx((5.0 + 7.5 + 1.2 + 0.0) / 4)
        assert stats["min"] == 0.0
        assert stats["max"] == 7.5

    def test_nan_columns_yield_none_stats(self):
        df = pd.DataFrame({"a": [np.nan, np.nan]})
        fp = compute_dataframe_fingerprint(df)
        stats = fp["column_stats"]["a"]
        assert stats["n_non_null"] == 0
        assert stats["sum"] is None
        assert stats["mean"] is None

    def test_string_columns_recorded_with_unique_count(self):
        df = pd.DataFrame({"step_status": ["ok", "ok", "no_trades"]})
        fp = compute_dataframe_fingerprint(df)
        assert fp["column_stats"]["step_status"]["n_unique"] == 2


# ---------------------------------------------------------------------------
# Pipeline fingerprint
# ---------------------------------------------------------------------------


class TestPipelineFingerprint:
    def test_includes_all_baseline_frames(self):
        result = _FakeResult(
            step_oos_long=_make_step_df(),
            step_train_long=_make_step_df(),
            trades_oos=_make_trade_df(),
            trades_train=_make_trade_df(),
            aggregated=pd.DataFrame({"grid_point_id": ["gp1"], "sum_pnl_pct_Median": [5.0]}),
            ranked=pd.DataFrame({"grid_rank": [1], "grid_point_id": ["gp1"]}),
            summary_wide=pd.DataFrame({"grid_point_id": ["gp1"], "n_segments": [2]}),
            bucket_matrix_median=pd.DataFrame({"bucket": ["b1"], "median": [5.0]}),
            diagnostics=_FakeDiagnostics(),
        )
        fp = compute_pipeline_fingerprint(result)
        # Plan WP1.steps.2 lists exactly these DataFrame artefacts (WF_Trades /
        # WF_Train_Trades correspond to trades_oos / trades_train in
        # PipelineResult — those are XLSX-export sheet names).
        for key in (
            "step_oos_long",
            "step_train_long",
            "trades_oos",
            "trades_train",
            "aggregated",
            "ranked",
            "summary_wide",
            "bucket_matrix_median",
        ):
            assert key in fp.frames, f"missing baseline frame {key}"
            assert fp.frames[key]["present"] is True

    def test_includes_bucket_matrix_scalar_attributes(self):
        # Plan WP1.steps.2 / DoD #2 requires capturing bucket_matrix_status and
        # bucket_matrix_error.  They are scalar strings on PipelineResult
        # (status="ok"|"disabled"|... + optional error message), so the
        # fingerprint must store them in `attributes`, not `frames`.
        result = _FakeResult(
            bucket_matrix_status="ok",
            bucket_matrix_error=None,
            diagnostics=_FakeDiagnostics(),
        )
        fp = compute_pipeline_fingerprint(result)
        assert fp.attributes == {
            "bucket_matrix_status": "ok",
            "bucket_matrix_error": None,
        }

    def test_attributes_default_for_missing_pipeline_fields(self):
        # When a result object lacks the bucket_matrix_* attributes entirely
        # we still want a deterministic placeholder so that the diff reliably
        # surfaces "field disappeared from pipeline" instead of silently
        # passing.
        @dataclass
        class _BareResult:
            diagnostics: Any = None

        fp = compute_pipeline_fingerprint(_BareResult(diagnostics=_FakeDiagnostics()))
        assert fp.attributes == {
            "bucket_matrix_status": "__missing__",
            "bucket_matrix_error": "__missing__",
        }

    def test_missing_frames_round_trip(self):
        result = _FakeResult(diagnostics=_FakeDiagnostics())
        fp = compute_pipeline_fingerprint(result)
        for key, val in fp.frames.items():
            assert val == {"present": False}, f"{key} should be absent"

    def test_diagnostics_excludes_timings(self):
        result = _FakeResult(diagnostics=_FakeDiagnostics())
        fp = compute_pipeline_fingerprint(result)
        assert fp.diagnostics["present"] is True
        assert "timings" not in fp.diagnostics
        assert fp.diagnostics["grid_size"] == 4
        assert fp.diagnostics["n_wf_steps"] == 2
        assert fp.diagnostics["step_status_counts"] == {"ok": 6, "no_trades": 2}

    def test_diagnostics_keys_normalised_to_strings(self):
        # PipelineDiagnostics.tier_counts is a dict[int, int]; JSON stringifies
        # int keys, so we normalise them up front to keep fingerprints stable
        # across save/load round-trips.
        result = _FakeResult(diagnostics=_FakeDiagnostics())
        fp = compute_pipeline_fingerprint(result)
        assert fp.diagnostics["tier_counts"] == {"1": 2, "2": 1}
        assert all(isinstance(k, str) for k in fp.diagnostics["tier_counts"])

    def test_extra_metadata_attached(self):
        result = _FakeResult(diagnostics=_FakeDiagnostics())
        fp = compute_pipeline_fingerprint(
            result,
            config_path="cfg.yaml",
            data_path="data.csv",
            extra_metadata={"comment": "wp1 baseline"},
        )
        assert fp.metadata["config_path"] == "cfg.yaml"
        assert fp.metadata["data_path"] == "data.csv"
        assert fp.metadata["comment"] == "wp1 baseline"


# ---------------------------------------------------------------------------
# Equality / diff
# ---------------------------------------------------------------------------


class TestFingerprintEquality:
    def _fp(self, **frames) -> PipelineFingerprint:
        return PipelineFingerprint(
            metadata={"config_path": "cfg.yaml", "captured_at": "irrelevant"},
            frames=frames,
            diagnostics={"present": True, "grid_size": 1},
        )

    def test_identical_fingerprints_are_equal(self):
        df = _make_step_df()
        a = self._fp(step_oos_long=compute_dataframe_fingerprint(df))
        b = self._fp(step_oos_long=compute_dataframe_fingerprint(df))
        assert fingerprints_equal(a, b)
        assert summarize_diff(a, b) == []

    def test_metadata_skip_keys_are_ignored_by_default(self):
        df = _make_step_df()
        a = PipelineFingerprint(
            metadata={"config_path": "/old/cfg.yaml", "captured_at": "2026-04-01", "host": "h1"},
            frames={"step_oos_long": compute_dataframe_fingerprint(df)},
        )
        b = PipelineFingerprint(
            metadata={"config_path": "/new/cfg.yaml", "captured_at": "2026-04-27", "host": "h2"},
            frames={"step_oos_long": compute_dataframe_fingerprint(df)},
        )
        assert fingerprints_equal(a, b)

    def test_platform_and_python_metadata_ignored_by_default(self):
        # capture_baseline.py records platform/python in metadata; replays on
        # other machines or Python versions must still pass equality.
        df = _make_step_df()
        a = PipelineFingerprint(
            metadata={"platform": "Windows-11", "python": "3.13.13"},
            frames={"step_oos_long": compute_dataframe_fingerprint(df)},
        )
        b = PipelineFingerprint(
            metadata={},
            frames={"step_oos_long": compute_dataframe_fingerprint(df)},
        )
        assert fingerprints_equal(a, b), summarize_diff(a, b)

    def test_frame_difference_is_reported(self):
        df1 = _make_step_df()
        df2 = df1.copy()
        df2.loc[0, "sum_pnl_pct"] = 99.0
        a = self._fp(step_oos_long=compute_dataframe_fingerprint(df1))
        b = self._fp(step_oos_long=compute_dataframe_fingerprint(df2))
        diff = summarize_diff(a, b)
        assert any("step_oos_long" in d and "content_hash" in d for d in diff)

    def test_attributes_difference_is_reported(self):
        a = PipelineFingerprint(
            attributes={"bucket_matrix_status": "ok", "bucket_matrix_error": None},
        )
        b = PipelineFingerprint(
            attributes={"bucket_matrix_status": "disabled", "bucket_matrix_error": None},
        )
        diff = summarize_diff(a, b)
        assert any("attributes.bucket_matrix_status" in d for d in diff)

    def test_schema_version_mismatch_is_reported(self):
        a = PipelineFingerprint(schema_version=2)
        b = PipelineFingerprint(schema_version=999)
        diff = summarize_diff(a, b)
        assert any("schema_version" in d for d in diff)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        df = _make_step_df()
        original = PipelineFingerprint(
            metadata={"comment": "test"},
            frames={"step_oos_long": compute_dataframe_fingerprint(df)},
            diagnostics={"present": True, "grid_size": 1},
        )
        path = save_baseline(original, tmp_path / "baseline.json")
        assert path.exists()

        loaded = load_baseline(path)
        assert loaded.schema_version == BASELINE_SCHEMA_VERSION
        assert loaded.metadata == original.metadata
        assert loaded.frames == original.frames
        assert loaded.diagnostics == original.diagnostics

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_baseline(tmp_path / "missing.json")

    def test_load_unsupported_schema_version_raises(self, tmp_path: Path):
        path = tmp_path / "baseline.json"
        path.write_text('{"schema_version": 999, "metadata": {}, "frames": {}, "diagnostics": {}}', encoding="utf-8")
        with pytest.raises(ValueError, match="schema_version"):
            load_baseline(path)

    def test_int_keyed_diagnostics_survive_save_load_equality(self, tmp_path: Path):
        # Regression test for the JSON-key-stringification bug discovered on
        # the first WP1 baseline replay: int keys become strings on load,
        # causing a false-positive diff against an in-memory fingerprint.
        result = _FakeResult(diagnostics=_FakeDiagnostics())
        fp = compute_pipeline_fingerprint(result)
        path = save_baseline(fp, tmp_path / "baseline.json")
        loaded = load_baseline(path)
        # Recomputed fingerprint (same as fp by construction) must equal the
        # one that has just round-tripped through JSON.
        assert fingerprints_equal(loaded, fp), summarize_diff(loaded, fp)


# ---------------------------------------------------------------------------
# Plan §3.9 / WP-PAR: execution_mode and output_path exclusion (marker M3,
# acceptance criterion A9).
# ---------------------------------------------------------------------------


@dataclass
class _FakeResultWithRuntimeAttrs(_FakeResult):
    """Extends _FakeResult with runtime-only attributes a real PipelineResult
    carries.  Used to verify the exclusion guard in compute_pipeline_fingerprint.
    """
    execution_mode: str = "sequential"
    output_path: Optional[Any] = None


class TestExecutionModeExclusion:
    """Verify _NON_DETERMINISTIC_RESULT_ATTRIBUTES guards and exclusion logic."""

    def test_non_deterministic_set_contains_expected_names(self):
        """plan §3.9: the frozenset must enumerate both runtime attributes."""
        assert "execution_mode" in _NON_DETERMINISTIC_RESULT_ATTRIBUTES
        assert "output_path" in _NON_DETERMINISTIC_RESULT_ATTRIBUTES

    def test_execution_mode_absent_from_fingerprint_attributes(self):
        """execution_mode must not appear in fp.attributes regardless of value."""
        for mode in ("sequential", "parallel", "parallel_then_fallback"):
            result = _FakeResultWithRuntimeAttrs(
                bucket_matrix_status="ok",
                diagnostics=_FakeDiagnostics(),
                execution_mode=mode,
            )
            fp = compute_pipeline_fingerprint(result)
            assert "execution_mode" not in fp.attributes, (
                f"execution_mode={mode!r} leaked into fp.attributes: {fp.attributes}"
            )

    def test_output_path_absent_from_fingerprint_attributes(self):
        """output_path must not appear in fp.attributes."""
        from pathlib import Path as _Path
        result = _FakeResultWithRuntimeAttrs(
            output_path=_Path("/some/env/specific/path.xlsx"),
            diagnostics=_FakeDiagnostics(),
        )
        fp = compute_pipeline_fingerprint(result)
        assert "output_path" not in fp.attributes, (
            f"output_path leaked into fp.attributes: {fp.attributes}"
        )

    def test_changing_execution_mode_does_not_change_fingerprint(self):
        """plan §3.9: bit-identical frames + different execution_mode -> same
        fingerprint.  This is the primary guarantee of marker M3.
        """
        df = _make_step_df()
        base_result = _FakeResultWithRuntimeAttrs(
            step_oos_long=df,
            bucket_matrix_status="ok",
            diagnostics=_FakeDiagnostics(),
        )

        fp_seq = compute_pipeline_fingerprint(
            _FakeResultWithRuntimeAttrs(
                **{**base_result.__dict__, "execution_mode": "sequential"},
            )
        )
        fp_par = compute_pipeline_fingerprint(
            _FakeResultWithRuntimeAttrs(
                **{**base_result.__dict__, "execution_mode": "parallel"},
            )
        )
        fp_fb = compute_pipeline_fingerprint(
            _FakeResultWithRuntimeAttrs(
                **{**base_result.__dict__, "execution_mode": "parallel_then_fallback"},
            )
        )

        diff_sp = summarize_diff(fp_seq, fp_par)
        diff_sf = summarize_diff(fp_seq, fp_fb)
        assert diff_sp == [], (
            f"execution_mode='sequential' vs 'parallel' produced diff: {diff_sp}"
        )
        assert diff_sf == [], (
            f"execution_mode='sequential' vs 'parallel_then_fallback' produced diff: {diff_sf}"
        )

    def test_changing_output_path_does_not_change_fingerprint(self):
        """plan §3.9: output_path is environment-specific; must not affect fingerprint."""
        from pathlib import Path as _Path
        df = _make_step_df()
        fp_a = compute_pipeline_fingerprint(
            _FakeResultWithRuntimeAttrs(
                step_oos_long=df,
                diagnostics=_FakeDiagnostics(),
                output_path=_Path("/machine_a/run1/result.xlsx"),
            )
        )
        fp_b = compute_pipeline_fingerprint(
            _FakeResultWithRuntimeAttrs(
                step_oos_long=df,
                diagnostics=_FakeDiagnostics(),
                output_path=_Path("/machine_b/run2/result.xlsx"),
            )
        )
        diff = summarize_diff(fp_a, fp_b)
        assert diff == [], f"output_path change produced diff: {diff}"
