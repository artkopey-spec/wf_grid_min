"""
Parity-check: 3.0 BucketMatrix_Median vs donor formula reference.

Instead of calling donor code directly (which has missing dependencies),
this module implements a standalone reference computation of the donor
formulas and verifies 3.0 produces identical numerical results.

Known intentional differences between 3.0 and donor are documented:
    1. Step naming: donor Step0..N-1 (0-based) vs 3.0 Step1..N (1-based)
    2. win_steps labels: donor "WF0,WF1" vs 3.0 "Step1,Step2"
    3. bucket_key: same string format in both — ``"{ab}_{mt}"`` (underscore), e.g. ``"10_10"``
    4. Sorting: 3.0 sorts by stability_score DESC; donor unsorted
    5. Sentinel cleaning: 3.0 cleans INVALID_METRIC_VALUE → NaN; donor skips None
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest

from wf_grid.bucket.median_matrix_builder import build_median_bucket_matrix
from wf_grid.config.schema import (
    BucketConfig,
    DataConfig,
    GridConfig,
    OptimizationConfig,
)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _make_config(
    atr_range=(10, 14),
    mult_range=(2.0, 2.4),
    mult_step=0.2,
    atr_bucket_step=2,
    mult_bucket_step=0.2,
    min_buckets_for_median=1,
) -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        optimization=OptimizationConfig(
            atr_period_range=list(atr_range),
            multiplier_range=list(mult_range),
            multiplier_step=mult_step,
        ),
        bucket=BucketConfig(
            atr_bucket_step=atr_bucket_step,
            mult_bucket_step=mult_bucket_step,
            min_buckets_for_median=min_buckets_for_median,
        ),
    )


# ---------------------------------------------------------------------------
# Shared test data: 6 params × 2 steps
# ---------------------------------------------------------------------------

_ENTRIES = [
    # Step 1 (wf_step=1 in 3.0)
    [
        {"atr_period": 10, "multiplier": 2.0, "sum_pnl_pct": 5.0},
        {"atr_period": 10, "multiplier": 2.2, "sum_pnl_pct": 3.0},
        {"atr_period": 12, "multiplier": 2.0, "sum_pnl_pct": 8.0},
        {"atr_period": 12, "multiplier": 2.4, "sum_pnl_pct": 2.0},
        {"atr_period": 14, "multiplier": 2.0, "sum_pnl_pct": -1.0},
        {"atr_period": 14, "multiplier": 2.2, "sum_pnl_pct": 4.0},
    ],
    # Step 2 (wf_step=2 in 3.0)
    [
        {"atr_period": 10, "multiplier": 2.0, "sum_pnl_pct": 6.0},
        {"atr_period": 10, "multiplier": 2.2, "sum_pnl_pct": 1.0},
        {"atr_period": 12, "multiplier": 2.0, "sum_pnl_pct": 7.0},
        {"atr_period": 12, "multiplier": 2.4, "sum_pnl_pct": -3.0},
        {"atr_period": 14, "multiplier": 2.0, "sum_pnl_pct": 2.0},
        {"atr_period": 14, "multiplier": 2.2, "sum_pnl_pct": 5.0},
    ],
]


def _make_step_oos_long() -> pd.DataFrame:
    rows = []
    for step_idx, step_entries in enumerate(_ENTRIES):
        for e in step_entries:
            rows.append({
                "grid_point_id": f"atr{e['atr_period']}_m{e['multiplier']:.2f}_both",
                "atr_period": e["atr_period"],
                "multiplier": e["multiplier"],
                "trade_mode": "both",
                "wf_step": step_idx + 1,
                "step_status": "ok",
                "sum_pnl_pct": e["sum_pnl_pct"],
                "max_drawdown": -0.10,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Standalone reference implementation of donor formulas
# ---------------------------------------------------------------------------

def _compute_reference(
    entries: list[list[dict]],
    atr_range=(10, 14),
    mult_range=(2.0, 2.4),
    mult_step=0.2,
    atr_bucket_step=2,
    mult_bucket_step=0.2,
    min_buckets_for_median=1,
) -> pd.DataFrame:
    """Reference implementation: donor formulas, 0-based steps, no imports from donor."""
    n_steps = len(entries)

    # Grid generation (donor formula)
    atr_buckets_set = set()
    for atr in range(int(atr_range[0]), int(atr_range[1]) + 1):
        atr_buckets_set.add(int(round(atr / atr_bucket_step) * atr_bucket_step))
    atr_buckets = sorted(atr_buckets_set)

    mult_ticks_set = set()
    n_mult_steps = int(round((mult_range[1] - mult_range[0]) / mult_step)) + 1
    for i in range(n_mult_steps):
        mv = round(mult_range[0] + i * mult_step, 10)
        if mv > mult_range[1] + 1e-9:
            break
        mult_ticks_set.add(int(round(mv / mult_bucket_step)))
    mult_ticks = sorted(mult_ticks_set)

    all_keys = [(ab, mt) for ab in atr_buckets for mt in mult_ticks]

    # Bucket size from full grid of (atr, mult) points
    bucket_size_map: Dict[Tuple[int, int], int] = {}
    for atr in range(int(atr_range[0]), int(atr_range[1]) + 1):
        for i in range(n_mult_steps):
            mv = round(mult_range[0] + i * mult_step, 10)
            if mv > mult_range[1] + 1e-9:
                break
            ab = int(round(atr / atr_bucket_step) * atr_bucket_step)
            mt = int(round(mv / mult_bucket_step))
            bk = (ab, mt)
            bucket_size_map[bk] = bucket_size_map.get(bk, 0) + 1

    # Accumulate
    step_bucket_vals: List[Dict[Tuple[int, int], List[float]]] = [
        {} for _ in range(n_steps)
    ]
    param_bucket_step_vals: Dict[
        Tuple[int, int],
        Dict[Tuple[int, float], Dict[int, List[float]]]
    ] = {}

    for step_idx, step_entries in enumerate(entries):
        for e in step_entries:
            atr_p = int(e["atr_period"])
            mult_v = float(e["multiplier"])
            pnl = float(e["sum_pnl_pct"])
            ab = int(round(atr_p / atr_bucket_step) * atr_bucket_step)
            mt = int(round(mult_v / mult_bucket_step))
            bk = (ab, mt)
            step_bucket_vals[step_idx].setdefault(bk, []).append(pnl)
            param_key = (atr_p, round(mult_v, 6))
            if bk not in param_bucket_step_vals:
                param_bucket_step_vals[bk] = {}
            if param_key not in param_bucket_step_vals[bk]:
                param_bucket_step_vals[bk][param_key] = {}
            param_bucket_step_vals[bk][param_key].setdefault(step_idx, []).append(pnl)

    # Build rows with medians
    rows = []
    for (ab, mt) in all_keys:
        row = {
            "atr_bucket": ab,
            "mult_bucket_ticks": mt,
            "bucket_size": bucket_size_map.get((ab, mt), 0),
        }
        for s_idx in range(n_steps):
            vals = step_bucket_vals[s_idx].get((ab, mt), [])
            row[f"Step{s_idx}"] = float(np.median(vals)) if vals else float("nan")
        rows.append(row)

    bdf = pd.DataFrame(rows)
    step_cols = [f"Step{i}" for i in range(n_steps)]
    total_steps = n_steps
    n_rows = len(bdf)

    # wins_count, top3_count
    wins_count = [0] * n_rows
    top3_count_list = [0] * n_rows
    for s_idx, col in enumerate(step_cols):
        step_vals = pd.to_numeric(bdf[col], errors="coerce").dropna()
        if step_vals.empty:
            continue
        winner_idx = int(step_vals.idxmax())
        ranks = step_vals.rank(ascending=False, method="min")
        top3_idxs = set(ranks[ranks <= 3].index)
        for row_i in range(n_rows):
            v = bdf.at[row_i, col]
            if pd.isna(v):
                continue
            if row_i == winner_idx:
                wins_count[row_i] += 1
            if row_i in top3_idxs:
                top3_count_list[row_i] += 1

    bdf["wins_count"] = wins_count
    bdf["top3_count"] = top3_count_list

    # mean_oos_pnl
    step_data = bdf[step_cols].apply(pd.to_numeric, errors="coerce")
    bdf["mean_oos_pnl"] = step_data.mean(axis=1, skipna=True)

    # std_bucket (population std, ddof=0, on raw values)
    std_vals = []
    for row_i in range(n_rows):
        ab = int(bdf.at[row_i, "atr_bucket"])
        mt = int(bdf.at[row_i, "mult_bucket_ticks"])
        bk = (ab, mt)
        all_v = []
        for s_idx in range(n_steps):
            all_v.extend(step_bucket_vals[s_idx].get(bk, []))
        arr = np.array(all_v, dtype=float)
        arr = arr[~np.isnan(arr)]
        std_vals.append(float(np.std(arr, ddof=0)) if len(arr) > 0 else float("nan"))
    bdf["std_bucket"] = std_vals

    # pct_params_positive_pnl
    pct_pos = []
    for row_i in range(n_rows):
        ab = int(bdf.at[row_i, "atr_bucket"])
        mt = int(bdf.at[row_i, "mult_bucket_ticks"])
        bk = (ab, mt)
        param_map = param_bucket_step_vals.get(bk, {})
        valid_c = 0
        pos_c = 0
        for _pk, spm in param_map.items():
            obs = []
            for sp in spm.values():
                obs.extend(sp)
            arr = np.array(obs, dtype=float)
            arr = arr[~np.isnan(arr)]
            if len(arr) == 0:
                continue
            valid_c += 1
            if float(np.mean(arr)) > 0.0:
                pos_c += 1
        pct_pos.append(pos_c / valid_c if valid_c > 0 else float("nan"))
    bdf["pct_params_positive_pnl"] = pct_pos

    # above_median_count, eligible_median_steps
    above_med = [0] * n_rows
    elig_steps = [0] * n_rows
    for col in step_cols:
        col_vals = pd.to_numeric(bdf[col], errors="coerce")
        non_nan = col_vals.dropna()
        if non_nan.empty or len(non_nan) < min_buckets_for_median:
            continue
        step_median = float(non_nan.median())
        for row_i in range(n_rows):
            v = col_vals.iloc[row_i]
            if not pd.isna(v):
                elig_steps[row_i] += 1
                if float(v) >= step_median:
                    above_med[row_i] += 1

    bdf["above_median_count"] = above_med
    bdf["above_median_ratio"] = [c / total_steps if total_steps > 0 else 0.0 for c in above_med]

    presence = []
    for row_i in range(n_rows):
        c = sum(1 for col in step_cols if not pd.isna(bdf.at[row_i, col]))
        presence.append(c)
    bdf["presence_count"] = presence

    bdf["above_median_ratio_present"] = [
        float(c / p) if p > 0 else 0.0 for c, p in zip(above_med, presence)
    ]
    bdf["eligible_median_steps_count"] = elig_steps
    bdf["above_median_ratio_eligible"] = [
        float(c / e) if e > 0 else 0.0 for c, e in zip(above_med, elig_steps)
    ]

    # stability_score
    stability = []
    for row_i in range(n_rows):
        if total_steps > 0:
            pr = presence[row_i] / total_steps
            amr = above_med[row_i] / total_steps
            stability.append(round(0.6 * pr + 0.4 * amr, 6))
        else:
            stability.append(0.0)
    bdf["bucket_stability_score"] = stability

    # zone_dominance_score
    zd = []
    for row_i in range(n_rows):
        if total_steps > 0:
            pr = presence[row_i] / total_steps
            t3r = top3_count_list[row_i] / total_steps
            wr = wins_count[row_i] / total_steps
            zd.append(round(0.4 * pr + 0.3 * t3r + 0.3 * wr, 6))
        else:
            zd.append(0.0)
    bdf["zone_dominance_score"] = zd

    return bdf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFormulaParity:
    """Verify 3.0 builder produces identical numbers to reference formulas."""

    @pytest.fixture
    def builds(self):
        config = _make_config()
        step_oos = _make_step_oos_long()
        df_30 = build_median_bucket_matrix(step_oos, config)
        df_ref = _compute_reference(_ENTRIES)
        return df_30, df_ref

    def test_same_number_of_rows(self, builds):
        df_30, df_ref = builds
        assert len(df_30) == len(df_ref)

    def test_same_bucket_keys(self, builds):
        df_30, df_ref = builds
        keys_30 = set(zip(df_30["atr_bucket"], df_30["mult_bucket_ticks"]))
        keys_ref = set(zip(df_ref["atr_bucket"], df_ref["mult_bucket_ticks"]))
        assert keys_30 == keys_ref

    def _get_ref_row(self, df_ref, ab, mt):
        return df_ref[(df_ref["atr_bucket"] == ab) & (df_ref["mult_bucket_ticks"] == mt)].iloc[0]

    def test_bucket_size(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert int(row["bucket_size"]) == int(ref["bucket_size"])

    def test_step_medians(self, builds):
        """3.0 Step1=ref Step0, 3.0 Step2=ref Step1."""
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            for s in range(1, 3):
                v_30 = row[f"Step{s}"]
                v_ref = ref[f"Step{s - 1}"]
                if pd.isna(v_30) and pd.isna(v_ref):
                    continue
                assert float(v_30) == pytest.approx(float(v_ref), abs=1e-9), \
                    f"Step{s} mismatch at ({ab},{mt})"

    def test_mean_oos_pnl(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            v_30, v_ref = row["mean_oos_pnl"], ref["mean_oos_pnl"]
            if pd.isna(v_30) and pd.isna(v_ref):
                continue
            assert float(v_30) == pytest.approx(float(v_ref), abs=1e-9)

    def test_std_bucket(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            v_30, v_ref = row["std_bucket"], ref["std_bucket"]
            if pd.isna(v_30) and pd.isna(v_ref):
                continue
            assert float(v_30) == pytest.approx(float(v_ref), abs=1e-9)

    def test_pct_params_positive_pnl(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            v_30, v_ref = row["pct_params_positive_pnl"], ref["pct_params_positive_pnl"]
            if pd.isna(v_30) and pd.isna(v_ref):
                continue
            assert float(v_30) == pytest.approx(float(v_ref), abs=1e-9)

    def test_wins_count(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert int(row["wins_count"]) == int(ref["wins_count"])

    def test_top3_count(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert int(row["top3_count"]) == int(ref["top3_count"])

    def test_above_median_count(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert int(row["above_median_count"]) == int(ref["above_median_count"])

    def test_above_median_ratio(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert float(row["above_median_ratio"]) == pytest.approx(
                float(ref["above_median_ratio"]), abs=1e-9
            )

    def test_presence_count(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert int(row["presence_count"]) == int(ref["presence_count"])

    def test_above_median_ratio_present(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert float(row["above_median_ratio_present"]) == pytest.approx(
                float(ref["above_median_ratio_present"]), abs=1e-9
            )

    def test_eligible_median_steps_count(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert int(row["eligible_median_steps_count"]) == int(ref["eligible_median_steps_count"])

    def test_above_median_ratio_eligible(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert float(row["above_median_ratio_eligible"]) == pytest.approx(
                float(ref["above_median_ratio_eligible"]), abs=1e-9
            )

    def test_stability_score(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert float(row["bucket_stability_score"]) == pytest.approx(
                float(ref["bucket_stability_score"]), abs=1e-6
            )

    def test_zone_dominance_score(self, builds):
        df_30, df_ref = builds
        for _, row in df_30.iterrows():
            ab, mt = int(row["atr_bucket"]), int(row["mult_bucket_ticks"])
            ref = self._get_ref_row(df_ref, ab, mt)
            assert float(row["zone_dominance_score"]) == pytest.approx(
                float(ref["zone_dominance_score"]), abs=1e-6
            )


class TestKnownDifferences:
    """Document and verify intentional 3.0-vs-donor differences."""

    @pytest.fixture
    def df_30(self):
        config = _make_config()
        step_oos = _make_step_oos_long()
        return build_median_bucket_matrix(step_oos, config)

    def test_step_naming_1_based(self, df_30):
        assert "Step1" in df_30.columns
        assert "Step2" in df_30.columns
        assert "Step0" not in df_30.columns

    def test_bucket_key_underscore_format(self, df_30):
        """3.0 uses 'ab_mt' format (same as donor)."""
        k = str(df_30.iloc[0]["bucket_key"])
        assert "_" in k
        parts = k.split("_")
        assert len(parts) == 2
        int(parts[0])
        int(parts[1])

    def test_sorted_by_stability_desc(self, df_30):
        scores = df_30["bucket_stability_score"].tolist()
        assert scores == sorted(scores, reverse=True)

    def test_win_steps_1_based(self, df_30):
        for ws in df_30["win_steps"]:
            if ws:
                for part in str(ws).split(","):
                    assert part.startswith("Step"), f"Expected Step prefix, got {part}"

    def test_presence_steps_1_based(self, df_30):
        for ps in df_30["bucket_presence_steps"]:
            if ps:
                for part in str(ps).split(","):
                    assert part.startswith("Step"), f"Expected Step prefix, got {part}"

    def test_no_drift_with_atr_period_step_2(self):
        """Invariant sum(sizes) == len(enumerate_grid) // n_modes holds with step=2."""
        from wf_grid.bucket.assignment import compute_expected_bucket_sizes
        from wf_grid.grid.enumeration import enumerate_grid
        from wf_grid.config.schema import (
            GridConfig, DataConfig, OptimizationConfig, BucketConfig,
        )
        cfg = GridConfig(
            data=DataConfig(file_path="dummy.csv"),
            optimization=OptimizationConfig(
                atr_period_range=[10, 20],
                multiplier_range=[2.0, 3.0],
                multiplier_step=0.5,
                atr_period_step=2,
            ),
            bucket=BucketConfig(
                atr_bucket_step=4,
                mult_bucket_step=0.5,
                min_buckets_for_median=1,
            ),
        )
        sizes = compute_expected_bucket_sizes(cfg)
        grid = enumerate_grid(cfg)
        n_modes = len({p.trade_mode for p in grid})
        expected_per_mode = len(grid) // max(n_modes, 1)
        assert sum(sizes.values()) == expected_per_mode

    def test_all_expected_columns_present(self, df_30):
        expected = {
            "bucket_param", "bucket_key", "atr_bucket", "mult_bucket_ticks",
            "bucket_size", "Step1", "Step2",
            "bucket_presence_steps", "mean_oos_pnl", "std_bucket",
            "pct_params_positive_pnl", "wins_count", "win_steps", "top3_count",
            "above_median_count", "above_median_ratio",
            "presence_count", "above_median_ratio_present",
            "eligible_median_steps_count", "above_median_ratio_eligible",
            "bucket_stability_score", "zone_dominance_score",
        }
        assert expected.issubset(set(df_30.columns))
