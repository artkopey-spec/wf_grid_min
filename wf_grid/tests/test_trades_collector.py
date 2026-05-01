"""
Unit tests for A6: trades_collector — collect_oos_trades, collect_train_trades.

Covers:
- Schema: all required columns in correct order.
- Identity columns: grid_point_id, wf_step, step_status on every row.
- Invariant E: no prepend leakage (entry_index >= 0) in OOS layer.
- Rebase correctness: rebased indices from executor are preserved.
- Inclusion policy: trades collected for all step_status values (raw diagnostics).
- Empty / None trades_df: no rows added, correct empty schema returned.
- Multiple grid points + multiple steps: concat and identity correct.
- train trades: train_start_idx / train_end_idx columns present.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import pytest

from wf_grid.collect.trades_collector import (
    TradesCollectionError,
    _OOS_TRADE_COLUMNS,
    _TRAIN_TRADE_COLUMNS,
    collect_oos_trades,
    collect_train_trades,
)
from wf_grid.config.loader import load_grid_config
from wf_grid.config.schema import GridConfig
from wf_grid.status.status_model import StepStatus
from wf_grid.wf.step_executor import StepResult


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def _config(tmp_path, min_meaningful_bars: int = 5) -> GridConfig:
    yaml_text = f"""\
data:
  file_path: data.csv
  periods_per_year: 252
validation:
  walk_forward:
    train_size: "200bars"
    test_size: "50bars"
status:
  min_meaningful_bars: {min_meaningful_bars}
"""
    path = _write_yaml(tmp_path, yaml_text)
    cfg = load_grid_config(path)
    cfg.resolved_periods_per_year = 252.0
    return cfg


def _make_trades_df(n: int = 3, entry_start: int = 0) -> pd.DataFrame:
    """Build a minimal trades_df matching donor schema."""
    return pd.DataFrame({
        "trade_id": list(range(1, n + 1)),
        "direction": ["LONG"] * n,
        "entry_time": pd.date_range("2020-01-01", periods=n, freq="5D"),
        "entry_index": [entry_start + i * 5 for i in range(n)],
        "entry_price": [100.0 + i for i in range(n)],
        "exit_time": pd.date_range("2020-01-05", periods=n, freq="5D"),
        "exit_index": [entry_start + i * 5 + 4 for i in range(n)],
        "exit_price": [101.0 + i for i in range(n)],
        "bars_held": [4] * n,
        "gross_pnl_pct": [1.0] * n,
        "commission_pct": [0.02] * n,
        "net_pnl_pct": [0.98] * n,
    })


def _make_step_result(
    gp_id: str,
    wf_step: int,
    trades_df: Optional[pd.DataFrame] = None,
    effective_oos_bars: int = 50,
    error_message: Optional[str] = None,
    error_type: Optional[str] = None,
    num_trades: int = 3,
) -> StepResult:
    metrics = {
        "num_trades": num_trades,
        "sum_pnl_pct": 2.5,
        "sharpe": 1.2,
        "sortino": 1.5,
        "max_drawdown": -0.1,
        "cagr": 0.15,
        "win_rate": 60.0,
        "profit_factor": 1.8,
        "avg_trade": 0.5,
    }
    return StepResult(
        grid_point_id=gp_id,
        wf_step=wf_step,
        test_start_idx=200 + wf_step * 50,
        test_end_idx=250 + wf_step * 50,
        metrics=metrics,
        oos_trades_df=trades_df if trades_df is not None else _make_trades_df(),
        prepend_bars_requested=55,
        prepend_bars_applied=55,
        used_prepend=True,
        used_legacy_oos_path=False,
        used_defensive_fallback=False,
        oos_boundary_index=55,
        warmup_used=0,
        warmup_effective=0,
        effective_oos_bars=effective_oos_bars,
        early_exit=False,
        error_message=error_message,
        error_type=error_type,
    )


def _grid_results(n_gp: int = 2, n_steps: int = 2) -> Dict[str, List[StepResult]]:
    return {
        f"atr{5+i}_m2.00_both": [
            _make_step_result(f"atr{5+i}_m2.00_both", step)
            for step in range(n_steps)
        ]
        for i in range(n_gp)
    }


# ===========================================================================
# Schema
# ===========================================================================

class TestOosTradesSchema:
    def test_all_columns_present(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(1, 1)
        df = collect_oos_trades(gr, cfg)
        for col in _OOS_TRADE_COLUMNS:
            assert col in df.columns, f"Missing: {col}"

    def test_column_order(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(1, 1)
        df = collect_oos_trades(gr, cfg)
        assert list(df.columns) == _OOS_TRADE_COLUMNS

    def test_empty_returns_correct_schema(self, tmp_path):
        cfg = _config(tmp_path)
        df = collect_oos_trades({}, cfg)
        assert len(df) == 0
        assert list(df.columns) == _OOS_TRADE_COLUMNS


class TestTrainTradesSchema:
    def test_all_train_columns_present(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(1, 1)
        df = collect_train_trades(gr, cfg)
        for col in _TRAIN_TRADE_COLUMNS:
            assert col in df.columns

    def test_train_column_order(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(1, 1)
        df = collect_train_trades(gr, cfg)
        assert list(df.columns) == _TRAIN_TRADE_COLUMNS

    def test_train_has_train_window_columns(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(1, 1)
        df = collect_train_trades(gr, cfg)
        assert "train_start_idx" in df.columns
        assert "train_end_idx" in df.columns
        assert "test_start_idx" not in df.columns


# ===========================================================================
# Identity columns on every row
# ===========================================================================

class TestIdentityColumns:
    def test_grid_point_id_on_every_row(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(2, 2)
        df = collect_oos_trades(gr, cfg)
        assert df["grid_point_id"].notna().all()

    def test_wf_step_on_every_row(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(2, 2)
        df = collect_oos_trades(gr, cfg)
        assert df["wf_step"].notna().all()

    def test_step_status_on_every_row(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(2, 2)
        df = collect_oos_trades(gr, cfg)
        assert df["step_status"].notna().all()

    def test_gp_id_values_correct(self, tmp_path):
        cfg = _config(tmp_path)
        gp_id = "atr5_m2.00_both"
        gr = {gp_id: [_make_step_result(gp_id, 0)]}
        df = collect_oos_trades(gr, cfg)
        assert (df["grid_point_id"] == gp_id).all()

    def test_wf_step_values_correct(self, tmp_path):
        cfg = _config(tmp_path)
        gp_id = "atr5_m2.00_both"
        gr = {gp_id: [_make_step_result(gp_id, 7)]}
        df = collect_oos_trades(gr, cfg)
        assert (df["wf_step"] == 7).all()


# ===========================================================================
# Invariant E — no prepend leakage
# ===========================================================================

class TestInvariantE:
    def test_rebased_indices_all_nonnegative(self, tmp_path):
        cfg = _config(tmp_path)
        # entry_index starts at 0 (already rebased by executor)
        trades = _make_trades_df(n=3, entry_start=0)
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0, trades)]}
        df = collect_oos_trades(gr, cfg)
        assert (df["entry_index"] >= 0).all()

    def test_negative_entry_index_raises_invariant_e(self, tmp_path):
        cfg = _config(tmp_path)
        trades = _make_trades_df(n=2, entry_start=-5)  # entry_start=-5 → negative indices
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0, trades)]}
        with pytest.raises(TradesCollectionError, match="Invariant E"):
            collect_oos_trades(gr, cfg)

    def test_rebase_values_preserved(self, tmp_path):
        cfg = _config(tmp_path)
        trades = _make_trades_df(n=2, entry_start=5)
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0, trades)]}
        df = collect_oos_trades(gr, cfg)
        assert list(df["entry_index"]) == [5, 10]


# ===========================================================================
# Inclusion policy — all step_status values
# ===========================================================================

class TestInclusionPolicy:
    def test_ok_step_trades_included(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=5)
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0,
                                                      effective_oos_bars=50)]}
        df = collect_oos_trades(gr, cfg)
        assert len(df) == 3
        assert df.iloc[0]["step_status"] == StepStatus.OK.value

    def test_no_trades_step_status_in_rows(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=5)
        # num_trades=0 → status=no_trades, but trades_df still has rows (raw diagnostics)
        trades = _make_trades_df(n=2)
        sr = _make_step_result("atr5_m2.00_both", 0, trades,
                                effective_oos_bars=50, num_trades=0)
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_trades(gr, cfg)
        # Trades are still present (raw diagnostics contract)
        assert len(df) == 2
        assert (df["step_status"] == StepStatus.NO_TRADES.value).all()

    def test_insufficient_bars_step_trades_included(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=100)
        trades = _make_trades_df(n=1)
        sr = _make_step_result("atr5_m2.00_both", 0, trades, effective_oos_bars=10)
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_trades(gr, cfg)
        assert len(df) == 1
        assert df.iloc[0]["step_status"] == StepStatus.INSUFFICIENT_BARS.value

    def test_runtime_error_step_trades_included(self, tmp_path):
        cfg = _config(tmp_path)
        trades = _make_trades_df(n=1)
        sr = _make_step_result("atr5_m2.00_both", 0, trades,
                               error_message="boom", error_type="ValueError")
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_trades(gr, cfg)
        assert len(df) == 1
        assert df.iloc[0]["step_status"] == StepStatus.RUNTIME_ERROR.value


# ===========================================================================
# Empty / None trades_df
# ===========================================================================

class TestEmptyTrades:
    def test_none_trades_df_no_rows(self, tmp_path):
        cfg = _config(tmp_path)
        sr = _make_step_result("atr5_m2.00_both", 0, trades_df=None)
        sr.oos_trades_df = None
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_trades(gr, cfg)
        assert len(df) == 0

    def test_empty_trades_df_no_rows(self, tmp_path):
        cfg = _config(tmp_path)
        empty = pd.DataFrame(columns=_make_trades_df().columns)
        sr = _make_step_result("atr5_m2.00_both", 0, trades_df=empty)
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_trades(gr, cfg)
        assert len(df) == 0
        assert list(df.columns) == _OOS_TRADE_COLUMNS

    def test_all_empty_returns_empty_schema(self, tmp_path):
        cfg = _config(tmp_path)
        sr0 = _make_step_result("atr5_m2.00_both", 0)
        sr0.oos_trades_df = None
        sr1 = _make_step_result("atr5_m2.00_both", 1)
        sr1.oos_trades_df = None
        gr = {"atr5_m2.00_both": [sr0, sr1]}
        df = collect_oos_trades(gr, cfg)
        assert len(df) == 0
        assert list(df.columns) == _OOS_TRADE_COLUMNS


# ===========================================================================
# Multiple grid points + multiple steps
# ===========================================================================

class TestMultipleGpAndSteps:
    def test_total_rows_across_gp_and_steps(self, tmp_path):
        cfg = _config(tmp_path)
        # 2 gp × 3 steps × 3 trades = 18 rows
        gr = {
            f"atr{5+i}_m2.00_both": [
                _make_step_result(f"atr{5+i}_m2.00_both", step)
                for step in range(3)
            ]
            for i in range(2)
        }
        df = collect_oos_trades(gr, cfg)
        assert len(df) == 2 * 3 * 3

    def test_correct_gp_id_per_row(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(2, 1)
        df = collect_oos_trades(gr, cfg)
        gp_ids = sorted(df["grid_point_id"].unique())
        expected = sorted(gr.keys())
        assert gp_ids == expected

    def test_correct_wf_step_per_row(self, tmp_path):
        cfg = _config(tmp_path)
        gp = "atr5_m2.00_both"
        gr = {gp: [_make_step_result(gp, i) for i in range(3)]}
        df = collect_oos_trades(gr, cfg)
        # Each step contributes 3 trades
        for step in range(3):
            subset = df[df["wf_step"] == step]
            assert len(subset) == 3
            assert (subset["grid_point_id"] == gp).all()


# ===========================================================================
# net_pnl_pct values preserved
# ===========================================================================

class TestTradeValues:
    def test_net_pnl_pct_preserved(self, tmp_path):
        cfg = _config(tmp_path)
        trades = _make_trades_df(n=3)
        trades["net_pnl_pct"] = [1.0, -0.5, 2.2]
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0, trades)]}
        df = collect_oos_trades(gr, cfg)
        assert list(df["net_pnl_pct"]) == pytest.approx([1.0, -0.5, 2.2])

    def test_window_indices_stored(self, tmp_path):
        cfg = _config(tmp_path)
        sr = _make_step_result("atr5_m2.00_both", 0)
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_trades(gr, cfg)
        assert (df["test_start_idx"] == sr.test_start_idx).all()
        assert (df["test_end_idx"] == sr.test_end_idx).all()
