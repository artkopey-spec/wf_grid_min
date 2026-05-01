"""
WP-T1 — Tester baseline capture gate test.

This test verifies that:
1. Baseline XLSX files exist with expected SHA-256 hashes (proving they were
   captured from the reference dataset with disabled config against the active
   Mode C runtime — ``donor/supertrend_optimizer/``).
2. A fresh in-memory run reproduces identical key metrics (proves tester is
   stable and deterministic before any Phase 2 wiring).

The tests are neutral to ``filter_diagnostics`` value — they only pin
positions/equity/trades shape and core metrics, which must remain stable across
WP-T3/T4/T9 disabled-path runs. Disabled-path runs MUST produce
``filter_diagnostics is None`` per plan §3.3.3 / Appendix A v1.1 §11.1.

sys.path is set up by conftest.py (no mutation here).

REBASELINE NOTE (WP-T2 / variant 1):
    The first WP-T1 baseline (captured before BLOCKER B-2 was fixed) ran
    against the stale ``donor TESTER/`` stub engine. After the B-2 unblocker
    (donor/__init__.py + sys.path order) put ``donor/`` first in resolution,
    ``donor/`` became the actual runtime — different code path, different
    in-memory metrics. This file pins the post-unblocker (canonical Mode C)
    snapshot. See ``docs/wp_t3_tester_dedup_log.md`` for the audit trail.

Spec reference: Appendix A v1.1 §11.1, §17.1.1, §18
Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §14 WP-T1
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTER_ROOT = REPO_ROOT / "donor TESTER"
BASELINES_DIR = TESTER_ROOT / "tests" / "baselines"

LEGACY_BASELINE = BASELINES_DIR / "result_legacy.baseline.xlsx"
EQ_BLOCKS_BASELINE = BASELINES_DIR / "result_equal_blocks.baseline.xlsx"

LEGACY_SHA256 = "791B153A2063D242F913F8FE9BEBB2198FEED2BA7C2186486468414707B12DC3"
EQ_BLOCKS_SHA256 = "29F5380CC4C71DD2139B7FB61F28A2FC18B054A2ABCC706433DBB4A2F2678006"

DATA_CSV = TESTER_ROOT / "data.csv"
DATA_SHA256 = "6C3C9CA8BD134106DEFF371C00B26E727FE7275213BDAB662F0F10EBDEC18A56"
DATA_ROW_COUNT = 109421
DATA_FIRST_DATETIME = "2025-11-24"
DATA_LAST_DATETIME = "2026-04-16"

# In-memory snapshot of the disabled-path legacy run on reference dataset +
# config. Captured 2026-04-28 against the canonical Mode C runtime
# (donor/supertrend_optimizer/) — i.e. AFTER B-2 unblocker put donor/ first
# in sys.path. Numbers are the post-unblocker golden reference; do NOT compare
# to the original (stub-engine) WP-T1 capture — see docs/wp_t3_tester_dedup_log.md.
EXPECTED_SNAPSHOT = {
    "100%": {"n_bars": 109421, "num_trades": 4636, "sum_pnl_pct": -312.253933},
    "75%":  {"n_bars":  82065, "num_trades": 3591, "sum_pnl_pct": -246.574952},
    "50%":  {"n_bars":  54710, "num_trades": 2406, "sum_pnl_pct": -161.784672},
    "33%":  {"n_bars":  36108, "num_trades": 1594, "sum_pnl_pct": -104.905774},
    "25%":  {"n_bars":  27355, "num_trades": 1236, "sum_pnl_pct":  -85.685258},
}

# Positions / equity snapshot for 100% slice (canonical Mode C runtime).
EXPECTED_100PCT = {
    "positions_shape": (109421,),
    "positions_dtype": np.dtype("int8"),
    "equity_shape": (109421,),
    "trend_shape": (109421,),
    "trades_df_shape": (4636, 13),
    "trades_columns": [
        "trade_id", "direction", "entry_time", "entry_index", "entry_price",
        "exit_time", "exit_index", "exit_price", "bars_held",
        "gross_pnl_pct", "commission_pct", "net_pnl_pct", "supertrend_color",
    ],
    "positions_sum": 55925,
    "equity_last": pytest.approx(0.043687203, rel=1e-6),
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


# ---------------------------------------------------------------------------
# Baseline files existence & hash gates
# ---------------------------------------------------------------------------

class TestBaselineFiles:
    """Verify baseline XLSX files exist with pinned hashes."""

    def test_legacy_baseline_exists(self) -> None:
        assert LEGACY_BASELINE.exists(), f"Legacy baseline missing: {LEGACY_BASELINE}"

    def test_equal_blocks_baseline_exists(self) -> None:
        assert EQ_BLOCKS_BASELINE.exists(), f"Equal-blocks baseline missing: {EQ_BLOCKS_BASELINE}"

    def test_legacy_baseline_sha256(self) -> None:
        actual = _sha256_file(LEGACY_BASELINE)
        assert actual == LEGACY_SHA256, (
            f"Legacy baseline SHA-256 mismatch.\n"
            f"Expected: {LEGACY_SHA256}\n"
            f"Actual:   {actual}\n"
            "If intentional regeneration: update README.md hash and this constant."
        )

    def test_equal_blocks_baseline_sha256(self) -> None:
        actual = _sha256_file(EQ_BLOCKS_BASELINE)
        assert actual == EQ_BLOCKS_SHA256, (
            f"Equal-blocks baseline SHA-256 mismatch.\n"
            f"Expected: {EQ_BLOCKS_SHA256}\n"
            f"Actual:   {actual}\n"
            "If intentional regeneration: update README.md hash and this constant."
        )

    def test_readme_exists(self) -> None:
        assert (BASELINES_DIR / "README.md").exists(), "baselines/README.md missing"


# ---------------------------------------------------------------------------
# Reference dataset integrity
# ---------------------------------------------------------------------------

class TestReferenceDataset:
    """Verify the reference data.csv matches pinned metadata."""

    def test_data_csv_exists(self) -> None:
        assert DATA_CSV.exists(), f"Reference data.csv not found: {DATA_CSV}"

    def test_data_csv_sha256(self) -> None:
        actual = _sha256_file(DATA_CSV)
        assert actual == DATA_SHA256, (
            f"Reference data.csv SHA-256 mismatch.\n"
            f"Expected: {DATA_SHA256}\n"
            f"Actual:   {actual}"
        )

    def test_data_csv_row_count(self) -> None:
        df = pd.read_csv(DATA_CSV, usecols=[0])
        assert len(df) == DATA_ROW_COUNT, (
            f"Expected {DATA_ROW_COUNT} data rows, got {len(df)}"
        )

    def test_data_csv_datetime_span(self) -> None:
        df = pd.read_csv(DATA_CSV, usecols=["time"], nrows=1)
        assert DATA_FIRST_DATETIME in str(df["time"].iloc[0]), \
            f"Expected first row to contain {DATA_FIRST_DATETIME}"
        df_tail = pd.read_csv(DATA_CSV, usecols=["time"])
        assert DATA_LAST_DATETIME in str(df_tail["time"].iloc[-1]), \
            f"Expected last row to contain {DATA_LAST_DATETIME}"


# ---------------------------------------------------------------------------
# In-memory snapshot: determinism gate
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def legacy_results():
    """Run legacy path on reference dataset and return PeriodResult list.

    sys.path is set up by donor TESTER/tests/conftest.py.
    """
    from supertrend_optimizer.cli.tester import load_tester_config, merge_cli_and_config
    from supertrend_optimizer.data.loader import load_ohlc_csv
    from supertrend_optimizer.data.validator import validate_ohlc_data
    from supertrend_optimizer.testing.runner import run_all_periods
    from supertrend_optimizer.utils.enums import ExecutionModel, MarketType
    from supertrend_optimizer.utils.warmup import calculate_warmup_tester
    from supertrend_optimizer.data.timeframe import resolve_periods_per_year_from_config

    cfg = load_tester_config(str(TESTER_ROOT / "config_tester.yaml"))
    df = load_ohlc_csv(str(DATA_CSV))
    df = validate_ohlc_data(df)

    cli_stub = argparse.Namespace(
        atr=None, mult=None, mode=None, execution_model=None,
        market=None, annualization_basis=None, periods_per_year=None,
    )
    base_params = merge_cli_and_config(cli_stub, cfg)
    warmup = calculate_warmup_tester(
        n=len(df), atr_period=base_params["atr_period"], warmup_period_auto=True
    )
    market_enum = MarketType(base_params["market"])
    periods_per_year = resolve_periods_per_year_from_config(
        config_value=base_params["annualization_factor"],
        index=df.index,
        explicit_basis=base_params["annualization_basis"],
        market=market_enum,
    )

    return run_all_periods(
        df=df,
        atr_period=base_params["atr_period"],
        multiplier=base_params["multiplier"],
        trade_mode=base_params["trade_mode"],
        commission=base_params["commission"],
        warmup_period=warmup,
        periods_per_year=periods_per_year,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        auto_warmup=base_params["warmup_period_auto"],
        min_trades_required=base_params["min_trades_required"],
    )


class TestInMemorySnapshot:
    """Pin key in-memory metrics for all 5 legacy periods (pre-Phase-2)."""

    @pytest.mark.parametrize("label", list(EXPECTED_SNAPSHOT.keys()))
    def test_n_bars(self, legacy_results, label: str) -> None:
        r = next(r for r in legacy_results if r.period_label == label)
        assert r.n_bars == EXPECTED_SNAPSHOT[label]["n_bars"]

    @pytest.mark.parametrize("label", list(EXPECTED_SNAPSHOT.keys()))
    def test_num_trades(self, legacy_results, label: str) -> None:
        r = next(r for r in legacy_results if r.period_label == label)
        assert r.metrics["num_trades"] == EXPECTED_SNAPSHOT[label]["num_trades"]

    @pytest.mark.parametrize("label", list(EXPECTED_SNAPSHOT.keys()))
    def test_sum_pnl_pct(self, legacy_results, label: str) -> None:
        r = next(r for r in legacy_results if r.period_label == label)
        assert r.metrics["sum_pnl_pct"] == pytest.approx(
            EXPECTED_SNAPSHOT[label]["sum_pnl_pct"], rel=1e-4
        )

    def test_positions_shape_100pct(self, legacy_results) -> None:
        r = next(r for r in legacy_results if r.period_label == "100%")
        assert r.result.positions.shape == EXPECTED_100PCT["positions_shape"]

    def test_positions_dtype_100pct(self, legacy_results) -> None:
        r = next(r for r in legacy_results if r.period_label == "100%")
        assert r.result.positions.dtype == EXPECTED_100PCT["positions_dtype"]

    def test_equity_shape_100pct(self, legacy_results) -> None:
        r = next(r for r in legacy_results if r.period_label == "100%")
        assert r.result.equity_curve.shape == EXPECTED_100PCT["equity_shape"]

    def test_trades_df_shape_100pct(self, legacy_results) -> None:
        r = next(r for r in legacy_results if r.period_label == "100%")
        assert r.result.trades_df.shape == EXPECTED_100PCT["trades_df_shape"]

    def test_trades_df_columns_100pct(self, legacy_results) -> None:
        r = next(r for r in legacy_results if r.period_label == "100%")
        assert list(r.result.trades_df.columns) == EXPECTED_100PCT["trades_columns"]

    def test_positions_sum_100pct(self, legacy_results) -> None:
        r = next(r for r in legacy_results if r.period_label == "100%")
        assert r.result.positions.sum() == EXPECTED_100PCT["positions_sum"]

    def test_equity_last_100pct(self, legacy_results) -> None:
        r = next(r for r in legacy_results if r.period_label == "100%")
        assert r.result.equity_curve[-1] == EXPECTED_100PCT["equity_last"]

    def test_result_has_required_attributes(self, legacy_results) -> None:
        """Sanity gate: BacktestResult exposes the attributes tester depends on.

        Neutral to Phase 2: does NOT pin presence/absence of `filter_diagnostics`
        (which will be added in WP-T3/T4 as `Optional` and remain `None` on
        disabled-path per plan §3.3.3).
        """
        r = next(r for r in legacy_results if r.period_label == "100%")
        for attr in ("positions", "equity_curve", "trend", "trades_df", "metrics"):
            assert hasattr(r.result, attr), f"BacktestResult missing required attribute: {attr}"
