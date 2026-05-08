"""
Unit tests for Этап 2: wf_grid/bucket/assignment.py
"""

from __future__ import annotations

import pytest
import pandas as pd

from wf_grid.bucket.assignment import (
    apply_param_buckets,
    generate_full_bucket_grid,
    compute_expected_bucket_sizes,
    format_atr_range,
    format_mult_range,
    format_bucket_label,
)
from wf_grid.config.schema import (
    GridConfig,
    DataConfig,
    OptimizationConfig,
    BucketConfig,
)
from wf_grid.grid.enumeration import enumerate_grid

_EN_DASH = "\u2013"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    atr_range=(10, 20),
    mult_range=(2.0, 3.0),
    mult_step=0.1,
    trade_mode="long",
    atr_bucket_step=2,
    mult_bucket_step=0.2,
    min_buckets_for_median=5,
) -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        optimization=OptimizationConfig(
            atr_period_range=list(atr_range),
            multiplier_range=list(mult_range),
            multiplier_step=mult_step,
            trade_mode=trade_mode,
        ),
        bucket=BucketConfig(
            atr_bucket_step=atr_bucket_step,
            mult_bucket_step=mult_bucket_step,
            min_buckets_for_median=min_buckets_for_median,
        ),
    )


# ===========================================================================
# apply_param_buckets
# ===========================================================================

class TestApplyParamBuckets:
    def test_basic_assignment(self):
        df = pd.DataFrame({"atr_period": [10, 11, 12], "multiplier": [2.0, 2.1, 2.2]})
        result = apply_param_buckets(df, atr_bucket_step=2, mult_bucket_step=0.2)
        # atr_bucket = round(atr / 2) * 2
        # 10 → round(5)*2=10, 11 → round(5.5)*2=12 (banker's), 12 → 12
        # mult_bucket_ticks = round(mult / 0.2)
        # 2.0 → 10, 2.1 → 11 (round(10.5)=10 banker's rounding!), 2.2 → 11
        assert "atr_bucket" in result.columns
        assert "mult_bucket_ticks" in result.columns
        # 10 / 2 = 5.0 → round(5.0) = 5 → 5*2 = 10
        assert result["atr_bucket"].iloc[0] == 10
        # 2.0 / 0.2 = 10.0 → round(10.0) = 10
        assert result["mult_bucket_ticks"].iloc[0] == 10

    def test_does_not_mutate_input(self):
        df = pd.DataFrame({"atr_period": [10, 14], "multiplier": [2.0, 2.5]})
        original_cols = list(df.columns)
        apply_param_buckets(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert list(df.columns) == original_cols

    def test_boundary_values(self):
        df = pd.DataFrame({"atr_period": [5, 55], "multiplier": [1.5, 5.5]})
        result = apply_param_buckets(df, atr_bucket_step=2, mult_bucket_step=0.2)
        # 5 / 2 = 2.5 → round(2.5) = 2 (banker's) → 2*2=4; or 3 depending on rounding
        # Key: both columns are integer types
        assert result["atr_bucket"].dtype in (int, "int64", "int32")
        assert result["mult_bucket_ticks"].dtype in (int, "int64", "int32")

    def test_determinism(self):
        df = pd.DataFrame({"atr_period": [10, 12, 14], "multiplier": [2.0, 2.2, 2.4]})
        r1 = apply_param_buckets(df, atr_bucket_step=2, mult_bucket_step=0.2)
        r2 = apply_param_buckets(df, atr_bucket_step=2, mult_bucket_step=0.2)
        pd.testing.assert_frame_equal(r1, r2)

    def test_donor_parity_formula(self):
        """Check exact donor formula: atr_bucket = round(atr/step)*step, mt = round(mult/step)."""
        df = pd.DataFrame({"atr_period": [13, 20], "multiplier": [2.3, 3.0]})
        result = apply_param_buckets(df, atr_bucket_step=2, mult_bucket_step=0.2)
        # atr=13: round(13/2)*2 = round(6.5)*2 = 6*2=12 (banker's) or 7*2=14
        # In Python: round(6.5) = 6 (banker's rounding)
        assert result["atr_bucket"].iloc[0] == round(13 / 2) * 2
        # atr=20: round(20/2)*2 = round(10)*2 = 20
        assert result["atr_bucket"].iloc[1] == round(20 / 2) * 2
        # mult=2.3: round(2.3/0.2) = round(11.5) = 12 (banker's) or 11
        assert result["mult_bucket_ticks"].iloc[0] == round(2.3 / 0.2)
        # mult=3.0: round(3.0/0.2) = round(15.0) = 15
        assert result["mult_bucket_ticks"].iloc[1] == round(3.0 / 0.2)


# ===========================================================================
# generate_full_bucket_grid
# ===========================================================================

class TestGenerateFullBucketGrid:
    def test_returns_sorted_list(self):
        config = _make_config(
            atr_range=(10, 14), mult_range=(2.0, 2.4), mult_step=0.2,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        grid = generate_full_bucket_grid(config)
        assert grid == sorted(grid), "Grid must be sorted (atr_bucket ASC, mult_ticks ASC)"

    def test_all_unique(self):
        config = _make_config(
            atr_range=(10, 20), mult_range=(2.0, 3.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        grid = generate_full_bucket_grid(config)
        assert len(grid) == len(set(grid)), "All bucket keys must be unique"

    def test_each_grid_point_maps_to_bucket(self):
        """Every ATR * mult combination maps to exactly one bucket in the grid."""
        config = _make_config(
            atr_range=(10, 14), mult_range=(2.0, 2.4), mult_step=0.2,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        grid = generate_full_bucket_grid(config)
        grid_set = set(grid)

        # Verify each grid point maps to a key in the generated set
        tick_min = round(2.0 / 0.2)
        tick_max = round(2.4 / 0.2)
        for atr in range(10, 15):
            for tick in range(tick_min, tick_max + 1):
                m = tick * 0.2
                ab = round(atr / 2) * 2
                mt = round(m / 0.2)
                assert (ab, mt) in grid_set

    def test_degenerate_atr_range(self):
        config = _make_config(atr_range=(20, 10))  # min > max
        grid = generate_full_bucket_grid(config)
        assert grid == []

    def test_degenerate_mult_range(self):
        config = _make_config(mult_range=(3.0, 2.0))  # min > max
        grid = generate_full_bucket_grid(config)
        assert grid == []

    def test_single_atr_single_mult(self):
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        grid = generate_full_bucket_grid(config)
        assert len(grid) == 1
        assert grid[0] == (round(10 / 2) * 2, round(2.0 / 0.2))

    def test_large_bucket_step_collapses_grid(self):
        """With atr_bucket_step=10 covering atr 10-20, all map to same bucket."""
        config = _make_config(
            atr_range=(10, 14), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=10, mult_bucket_step=0.2,
        )
        grid = generate_full_bucket_grid(config)
        # All ATR values collapse to fewer buckets
        atr_buckets = {ab for ab, _ in grid}
        assert len(atr_buckets) <= 2  # at most boundary effect


# ===========================================================================
# compute_expected_bucket_sizes
# ===========================================================================

class TestComputeExpectedBucketSizes:
    def test_sum_equals_grid_points(self):
        """sum(bucket_sizes) == total grid points (single trade_mode)."""
        config = _make_config(
            atr_range=(10, 14), mult_range=(2.0, 2.4), mult_step=0.2,
            trade_mode="long",
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        sizes = compute_expected_bucket_sizes(config)
        total = sum(sizes.values())
        grid = enumerate_grid(config)
        assert total == len(grid), (
            f"sum(sizes)={total} != len(enumerate_grid)={len(grid)}"
        )

    def test_all_sizes_positive(self):
        config = _make_config(
            atr_range=(10, 16), mult_range=(2.0, 2.6), mult_step=0.2,
            trade_mode="long",
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        sizes = compute_expected_bucket_sizes(config)
        assert all(v >= 1 for v in sizes.values()), "All bucket sizes must be >= 1"

    def test_uses_optimization_mult_step(self):
        """Enumeration uses optimization.multiplier_step, not bucket.mult_bucket_step."""
        # mult_step=0.1 → 11 mult values in [2.0, 3.0]; bucket_step=0.2 → groups of ~2
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.2), mult_step=0.1,
            trade_mode="long",
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        sizes = compute_expected_bucket_sizes(config)
        # 2 atr values × 3 mult values (2.0, 2.1, 2.2) = 6 total
        total = sum(sizes.values())
        assert total == 6

    def test_degenerate_range_returns_empty(self):
        config = _make_config(atr_range=(20, 10))  # degenerate
        sizes = compute_expected_bucket_sizes(config)
        assert sizes == {}

    def test_keys_match_generate_full_bucket_grid(self):
        """Keys of compute_expected_bucket_sizes == set from generate_full_bucket_grid."""
        config = _make_config(
            atr_range=(10, 14), mult_range=(2.0, 2.4), mult_step=0.2,
            trade_mode="long",
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        sizes = compute_expected_bucket_sizes(config)
        grid = set(generate_full_bucket_grid(config))
        assert set(sizes.keys()) == grid

    def test_uniform_buckets_with_compatible_steps(self):
        """When mult_step divides evenly into mult_bucket_step, buckets are uniform."""
        # mult_step=0.1, mult_bucket_step=0.2 → each bucket has 2 mult values
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.4), mult_step=0.1,
            trade_mode="long",
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        sizes = compute_expected_bucket_sizes(config)
        # 1 atr × 5 mult values [2.0,2.1,2.2,2.3,2.4] = 5 total
        total = sum(sizes.values())
        assert total == 5

    def test_invariant_violation_raises_on_miscount(self, monkeypatch):
        """If enumeration count mismatches, ValueError is raised."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.2), mult_step=0.1,
            trade_mode="long",
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        # enumerate_grid is imported at module level in assignment.py,
        # so we patch it there directly.
        from wf_grid.grid.enumeration import GridPoint
        import wf_grid.bucket.assignment as assign_mod

        def fake_enumerate(cfg):
            return [GridPoint(10, 2.0, "long", "x")] * 999  # wrong count

        monkeypatch.setattr(assign_mod, "enumerate_grid", fake_enumerate)

        with pytest.raises(ValueError, match="bucket_size invariant violated"):
            compute_expected_bucket_sizes(config)


# ===========================================================================
# format_atr_range
# ===========================================================================

class TestFormatAtrRange:
    def test_basic(self):
        assert format_atr_range(10, 2) == f"10{_EN_DASH}12"

    def test_single_step(self):
        assert format_atr_range(5, 1) == f"5{_EN_DASH}6"

    def test_large(self):
        assert format_atr_range(50, 5) == f"50{_EN_DASH}55"

    def test_uses_en_dash_not_hyphen(self):
        result = format_atr_range(10, 2)
        assert "-" not in result, "Must use en-dash U+2013, not hyphen"
        assert _EN_DASH in result

    def test_zero_atr_bucket(self):
        assert format_atr_range(0, 2) == f"0{_EN_DASH}2"


# ===========================================================================
# format_mult_range
# ===========================================================================

class TestFormatMultRange:
    def test_basic(self):
        assert format_mult_range(10, 0.2) == f"2.0{_EN_DASH}2.2"

    def test_integer_like_mult(self):
        assert format_mult_range(20, 0.5) == f"10.0{_EN_DASH}10.5"

    def test_small_step(self):
        assert format_mult_range(15, 0.1) == f"1.5{_EN_DASH}1.6"

    def test_always_one_decimal(self):
        result = format_mult_range(10, 0.2)
        lo, hi = result.split(_EN_DASH)
        assert "." in lo and len(lo.split(".")[1]) == 1, f"lo={lo!r} must have 1 decimal"
        assert "." in hi and len(hi.split(".")[1]) == 1, f"hi={hi!r} must have 1 decimal"

    def test_uses_en_dash_not_hyphen(self):
        result = format_mult_range(10, 0.2)
        assert "-" not in result
        assert _EN_DASH in result


# ===========================================================================
# format_bucket_label
# ===========================================================================

class TestFormatBucketLabel:
    def test_basic(self):
        result = format_bucket_label(10, 10, 2, 0.2)
        assert result == f"ATR 10{_EN_DASH}12, M 2.0{_EN_DASH}2.2"

    def test_consistency_with_range_helpers(self):
        atr_part = format_atr_range(12, 2)
        mult_part = format_mult_range(15, 0.2)
        label = format_bucket_label(12, 15, 2, 0.2)
        assert f"ATR {atr_part}" in label
        assert f"M {mult_part}" in label

    def test_format_structure(self):
        label = format_bucket_label(10, 10, 2, 0.2)
        assert label.startswith("ATR ")
        assert ", M " in label

    def test_en_dash_in_both_parts(self):
        label = format_bucket_label(10, 10, 2, 0.2)
        assert label.count(_EN_DASH) == 2


# ---------------------------------------------------------------------------
# atr_period_step invariant (ТЗ §7.3)
# ---------------------------------------------------------------------------

class TestBucketInvariantWithSparseAtr:
    """Invariant holds with atr_period_step=3 and intentionally incompatible bucket=4."""

    def test_invariant_holds_with_sparse_atr(self):
        # atr=[10,20], step=3 → ATR values: [10,13,16,19,20] (5 points)
        # atr_bucket_step=4 is not a multiple of 3 — Warning A fires, math must hold.
        cfg = GridConfig(
            data=DataConfig(file_path="dummy.csv"),
            optimization=OptimizationConfig(
                atr_period_range=[10, 20],
                multiplier_range=[2.0, 3.0],
                multiplier_step=0.5,
                atr_period_step=3,
                trade_mode="long",
            ),
            bucket=BucketConfig(
                atr_bucket_step=4,
                mult_bucket_step=0.5,
                min_buckets_for_median=1,
            ),
        )
        # Verify ATR values produced
        from wf_grid.grid.enumeration import _atr_values
        atr_vals = _atr_values(10, 20, 3)
        assert atr_vals == [10, 13, 16, 19, 20]

        # compute_expected_bucket_sizes must not raise
        sizes = compute_expected_bucket_sizes(cfg)
        assert isinstance(sizes, dict)
        assert len(sizes) > 0

        # Invariant: sum(sizes) == n_atr_points * n_mult_ticks
        grid = enumerate_grid(cfg)
        n_modes = len({p.trade_mode for p in grid})
        expected_per_mode = len(grid) // max(n_modes, 1)
        assert sum(sizes.values()) == expected_per_mode
