"""
Unit tests for A2: grid enumeration + canonical identity.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from wf_grid.config.loader import load_grid_config
from wf_grid.grid.enumeration import GridPoint, _canonical_multiplier, enumerate_grid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def _make_config(tmp_path, atr_range=(5, 7), mult_range=(1.5, 2.5), mult_step=0.5,
                 trade_mode="both"):
    yaml_text = f"""\
data:
  file_path: data.csv
optimization:
  atr_period_range: [{atr_range[0]}, {atr_range[1]}]
  multiplier_range: [{mult_range[0]}, {mult_range[1]}]
  multiplier_step: {mult_step}
  trade_mode: {trade_mode}
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""
    path = _write_yaml(tmp_path, yaml_text)
    return load_grid_config(path)


# ---------------------------------------------------------------------------
# Canonical multiplier unit tests
# ---------------------------------------------------------------------------

class TestCanonicalMultiplier:
    def test_exact_step_boundary(self):
        # 1.5 / 0.5 = 3 ticks → 3 * 0.5 = 1.5
        assert _canonical_multiplier(1.5, 0.5) == pytest.approx(1.5)

    def test_accumulated_float_stabilised(self):
        # Naive float accumulation: 1.5 + 0.1 + ... 20 times may drift.
        # Tick approach: each value is derived independently from its tick.
        step = 0.1
        # Simulate naive accumulation
        naive = 1.5
        for _ in range(20):
            naive += step
        # Canonical approach
        canonical = _canonical_multiplier(naive, step)
        # The canonical value must land exactly on a tick boundary
        tick = round(naive / step)
        assert canonical == pytest.approx(tick * step, abs=1e-10)

    def test_multiplier_35_with_step_01(self):
        # tick = round(3.5 / 0.1) = 35; canonical = 35 * 0.1 = 3.5
        result = _canonical_multiplier(3.5, 0.1)
        assert result == pytest.approx(3.5, abs=1e-10)

    def test_small_step(self):
        # 2.00 / 0.05 = 40 ticks → 40 * 0.05 = 2.0
        assert _canonical_multiplier(2.0, 0.05) == pytest.approx(2.0)

    def test_canonical_is_deterministic(self):
        # Same inputs → same output, every call
        v1 = _canonical_multiplier(3.3, 0.1)
        v2 = _canonical_multiplier(3.3, 0.1)
        assert v1 == v2


# ---------------------------------------------------------------------------
# Grid size
# ---------------------------------------------------------------------------

class TestGridSize:
    def test_size_matches_formula(self, tmp_path):
        # atr: 5..7 = 3, mult: 1.5, 2.0, 2.5 = 3, mode: 1 → 9
        cfg = _make_config(tmp_path, atr_range=(5, 7), mult_range=(1.5, 2.5), mult_step=0.5)
        grid = enumerate_grid(cfg)
        assert len(grid) == 3 * 3 * 1  # |atr| * |mult| * |modes|

    def test_size_single_atr_single_mult(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.5)
        grid = enumerate_grid(cfg)
        assert len(grid) == 1

    def test_size_with_revers_mode(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 6), mult_range=(1.5, 2.0), mult_step=0.5,
                           trade_mode="revers")
        grid = enumerate_grid(cfg)
        assert len(grid) == 2 * 2 * 1   # atr 5,6 × mult 1.5,2.0 × revers

    def test_full_default_range_size(self, tmp_path):
        # Default: atr 5..55 = 51, mult 1.5..5.5 step 0.1 = 41 ticks, mode 1
        cfg = _make_config(tmp_path, atr_range=(5, 55), mult_range=(1.5, 5.5), mult_step=0.1)
        grid = enumerate_grid(cfg)
        expected = 51 * 41 * 1
        assert len(grid) == expected


# ---------------------------------------------------------------------------
# grid_point_id format and uniqueness
# ---------------------------------------------------------------------------

class TestGridPointId:
    def test_id_format(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(10, 10), mult_range=(2.5, 2.5), mult_step=0.5,
                           trade_mode="both")
        grid = enumerate_grid(cfg)
        assert len(grid) == 1
        assert grid[0].grid_point_id == "atr10_m2.50_both"

    def test_id_format_revers(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 5), mult_range=(1.5, 1.5), mult_step=0.5,
                           trade_mode="revers")
        grid = enumerate_grid(cfg)
        assert grid[0].grid_point_id == "atr5_m1.50_revers"

    def test_id_format_two_decimals(self, tmp_path):
        # multiplier_step=0.1 → multiplier formatted as X.XX
        cfg = _make_config(tmp_path, atr_range=(7, 7), mult_range=(3.3, 3.3), mult_step=0.1,
                           trade_mode="long")
        grid = enumerate_grid(cfg)
        # canonical: tick = round(3.3/0.1)=33, 33*0.1=3.3 → "3.30"
        assert grid[0].grid_point_id == "atr7_m3.30_long"

    def test_all_ids_unique(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 7), mult_range=(1.5, 2.5), mult_step=0.5)
        grid = enumerate_grid(cfg)
        ids = [p.grid_point_id for p in grid]
        assert len(ids) == len(set(ids))

    def test_all_ids_unique_full_default(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 55), mult_range=(1.5, 5.5), mult_step=0.1)
        grid = enumerate_grid(cfg)
        ids = [p.grid_point_id for p in grid]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_config_same_order(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 7), mult_range=(1.5, 2.0), mult_step=0.5)
        grid1 = enumerate_grid(cfg)
        grid2 = enumerate_grid(cfg)
        assert [p.grid_point_id for p in grid1] == [p.grid_point_id for p in grid2]

    def test_order_atr_then_mult(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 6), mult_range=(1.5, 2.0), mult_step=0.5)
        grid = enumerate_grid(cfg)
        ids = [p.grid_point_id for p in grid]
        # Expected: (5,1.5), (5,2.0), (6,1.5), (6,2.0)
        assert ids == [
            "atr5_m1.50_both",
            "atr5_m2.00_both",
            "atr6_m1.50_both",
            "atr6_m2.00_both",
        ]


# ---------------------------------------------------------------------------
# Float drift: canonical multiplier used in GridPoint.multiplier
# ---------------------------------------------------------------------------

class TestFloatDrift:
    def test_multiplier_field_matches_id(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 5), mult_range=(1.5, 5.5), mult_step=0.1)
        grid = enumerate_grid(cfg)
        for point in grid:
            # The multiplier in the dataclass must match what's in grid_point_id
            expected_id_fragment = f"m{point.multiplier:.2f}"
            assert expected_id_fragment in point.grid_point_id

    def test_no_drift_across_41_ticks(self, tmp_path):
        # 41 ticks from 1.5 to 5.5 step 0.1 — naive accumulation would drift
        cfg = _make_config(tmp_path, atr_range=(5, 5), mult_range=(1.5, 5.5), mult_step=0.1)
        grid = enumerate_grid(cfg)
        multipliers = [p.multiplier for p in grid]

        # Each multiplier must be expressible as tick * 0.1 with no residual
        step = 0.1
        for m in multipliers:
            tick = round(m / step)
            assert m == pytest.approx(tick * step, abs=1e-10), (
                f"Multiplier {m} has float drift; tick={tick}, expected={tick*step}"
            )

    def test_canonical_mult_equals_id_parsing(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 5), mult_range=(1.5, 5.5), mult_step=0.1)
        grid = enumerate_grid(cfg)
        for point in grid:
            # Parse multiplier from id and compare with stored multiplier
            id_parts = point.grid_point_id.split("_")
            mult_from_id = float(id_parts[1][1:])   # strip leading "m"
            assert point.multiplier == pytest.approx(mult_from_id, abs=1e-9)


# ---------------------------------------------------------------------------
# GridPoint dataclass properties
# ---------------------------------------------------------------------------

class TestGridPointDataclass:
    def test_frozen(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 5), mult_range=(2.0, 2.0), mult_step=0.5)
        grid = enumerate_grid(cfg)
        point = grid[0]
        with pytest.raises((AttributeError, TypeError)):
            point.atr_period = 999  # type: ignore[misc]

    def test_fields_correct_types(self, tmp_path):
        cfg = _make_config(tmp_path, atr_range=(5, 5), mult_range=(2.0, 2.0), mult_step=0.5)
        grid = enumerate_grid(cfg)
        p = grid[0]
        assert isinstance(p.atr_period, int)
        assert isinstance(p.multiplier, float)
        assert isinstance(p.trade_mode, str)
        assert isinstance(p.grid_point_id, str)
