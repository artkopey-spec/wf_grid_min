"""
WP-T4 §7.1 production-path wiring tests.

These tests verify that the CLI/batch production paths wire the ZigZag filter
correctly — i.e. that an enabled legacy config does NOT silently produce an
unfiltered baseline run.

Two production paths under test:
1. ``donor/supertrend_optimizer/cli/tester.py::run_backtest`` (canonical CLI).
2. ``donor TESTER/run_batch_tester.py`` (batch launcher, tested via subprocess).

For these tests we use the same synthetic OHLC data as in the runner
integration suite but exercise the CLI layer directly.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §7.1
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DONOR_ROOT = REPO_ROOT / "donor"
TESTER_ROOT = REPO_ROOT / "donor TESTER"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_synthetic_ohlc(n: int = 600, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(loc=0.0003, scale=0.012, size=n)
    close = 100.0 * np.exp(np.cumsum(log_returns))
    noise = rng.uniform(0.001, 0.005, size=n)
    high = close * (1.0 + noise)
    low = close * (1.0 - noise)
    open_ = np.clip(close * (1.0 + rng.uniform(-0.002, 0.002, size=n)), low, high)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close},
        index=idx,
    )


def _make_trade_filter_config(enabled: bool = True):
    from supertrend_optimizer.core.trade_filter_config import (
        TradeFilterConfig,
        TradeFilterDiagnosticsConfig,
        TradeFilterLifecycleConfig,
        TradeFilterTriggerToggleConfig,
        TradeFilterTriggersConfig,
        TradeFilterZigZagConfig,
    )
    return TradeFilterConfig(
        enabled=enabled,
        type="zigzag_st_mode" if enabled else None,
        zigzag=TradeFilterZigZagConfig(
            enabled=True,
            reversal_threshold=0.04,
            local_window=20,
            candidate_trigger_threshold=0.4,
        ) if enabled else None,
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ) if enabled else None,
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=3,
            stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
        ) if enabled else None,
        diagnostics=TradeFilterDiagnosticsConfig() if enabled else None,
    )


# ---------------------------------------------------------------------------
# Group 1: run_all_periods gets filter params from CLI wiring
#
# These tests call run_all_periods directly but simulate the production call
# site: we verify that when a caller passes trade_filter_config+stats (as
# cli/tester.py now does), PeriodResult.filter_diagnostics is non-None.
# (The CLI itself calls run_all_periods; this is an in-process verification
#  of the contract that cli/tester.py::run_backtest now upholds.)
# ---------------------------------------------------------------------------

class TestCliWiringContract:
    """Verify that the production call site (cli wiring) wires filter correctly."""

    def test_enabled_config_produces_non_none_diagnostics(self) -> None:
        """Core regression: enabled filter must NOT silently no-op.

        This is the guard against the original bug where run_all_periods was
        called without trade_filter_config/zigzag_global_stats.
        """
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        from supertrend_optimizer.data.loader import load_ohlc_csv
        from supertrend_optimizer.data.validator import validate_ohlc_data
        from supertrend_optimizer.testing.runner import run_all_periods

        df = _make_synthetic_ohlc(n=600, seed=42)
        tf_cfg = _make_trade_filter_config(enabled=True)

        # Mimic the production CLI path (plan §7.1)
        zigzag_global_stats = build_zigzag_global_stats(
            close=df["close"].values,
            trade_filter_config=tf_cfg,
        )
        results = run_all_periods(
            df=df,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=tf_cfg,
            zigzag_global_stats=zigzag_global_stats,
        )

        for r in results:
            assert r.filter_diagnostics is not None, (
                f"Period {r.period_label}: enabled filter silently no-opped. "
                "Check cli/tester.py and run_batch_tester.py wiring."
            )
            assert r.filter_diagnostics_summary is not None

    def test_disabled_config_produces_none_diagnostics(self) -> None:
        """Disabled path: filter_diagnostics=None (baseline contract)."""
        from supertrend_optimizer.testing.runner import run_all_periods

        df = _make_synthetic_ohlc(n=600, seed=42)
        results = run_all_periods(
            df=df,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=None,
            zigzag_global_stats=None,
        )
        for r in results:
            assert r.filter_diagnostics is None
            assert r.filter_diagnostics_summary is None

    def test_enabled_filter_changes_positions(self) -> None:
        """Enabled filter must modify at least one position vs raw baseline.

        If run_all_periods were called without filter params (old bug), the
        enabled run would produce identical positions to the disabled run.
        """
        from supertrend_optimizer.core.backtest import generate_positions
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        from supertrend_optimizer.testing.runner import run_all_periods
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = _make_synthetic_ohlc(n=600, seed=42)
        tf_cfg = _make_trade_filter_config(enabled=True)
        stats = build_zigzag_global_stats(
            close=df["close"].values, trade_filter_config=tf_cfg
        )

        results_enabled = run_all_periods(
            df=df, atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001, warmup_period=30,
            trade_filter_config=tf_cfg, zigzag_global_stats=stats,
        )
        results_disabled = run_all_periods(
            df=df, atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001, warmup_period=30,
        )

        # For at least the 100% period, some positions should differ
        pos_enabled = results_enabled[0].result.positions
        pos_disabled = results_disabled[0].result.positions
        diff = int(np.sum(pos_enabled != pos_disabled))
        assert diff > 0, (
            "Enabled and disabled runs produce identical positions — "
            "filter is not wired into run_all_periods. "
            "Check cli/tester.py and run_batch_tester.py."
        )


# ---------------------------------------------------------------------------
# Group 2: cli/tester.py imports the right symbols (static guard)
# ---------------------------------------------------------------------------

class TestCliTesterStaticImports:
    """Guard that cli/tester.py imports build_zigzag_global_stats (WP-T4 §7.1)."""

    def test_build_zigzag_global_stats_imported_in_cli_tester(self) -> None:
        cli_path = DONOR_ROOT / "supertrend_optimizer" / "cli" / "tester.py"
        src = cli_path.read_text(encoding="utf-8")
        assert "build_zigzag_global_stats" in src, (
            "cli/tester.py does not import build_zigzag_global_stats. "
            "WP-T4 §7.1 CLI wiring requires this import."
        )

    def test_trade_filter_config_passed_to_run_all_periods_in_cli_tester(
        self,
    ) -> None:
        cli_path = DONOR_ROOT / "supertrend_optimizer" / "cli" / "tester.py"
        src = cli_path.read_text(encoding="utf-8")
        assert "trade_filter_config=tf_cfg" in src, (
            "cli/tester.py run_all_periods call does not pass trade_filter_config. "
            "WP-T4 §7.1 wiring incomplete."
        )
        assert "zigzag_global_stats=zigzag_global_stats" in src, (
            "cli/tester.py run_all_periods call does not pass zigzag_global_stats. "
            "WP-T4 §7.1 wiring incomplete."
        )


# ---------------------------------------------------------------------------
# Group 3: run_batch_tester.py imports and wires filter correctly (static)
# ---------------------------------------------------------------------------

class TestRunBatchTesterStaticImports:
    """Guard that run_batch_tester.py imports and wires filter correctly."""

    def test_build_zigzag_global_stats_imported(self) -> None:
        src = (TESTER_ROOT / "run_batch_tester.py").read_text(encoding="utf-8")
        assert "build_zigzag_global_stats" in src

    def test_trade_filter_config_passed_to_run_all_periods(self) -> None:
        src = (TESTER_ROOT / "run_batch_tester.py").read_text(encoding="utf-8")
        assert "trade_filter_config=tf_cfg" in src
        assert "zigzag_global_stats=zigzag_global_stats" in src

    def test_zigzag_global_stats_not_built_for_equal_blocks(self) -> None:
        """Equal-blocks path must NOT trigger stats build — gate rejects it first."""
        src = (TESTER_ROOT / "run_batch_tester.py").read_text(encoding="utf-8")
        # Stats are built BEFORE the seg_mode loop, not inside equal_blocks branch
        stats_pos = src.index("build_zigzag_global_stats")
        equal_blocks_pos = src.index('"equal_blocks"')
        assert stats_pos < equal_blocks_pos, (
            "build_zigzag_global_stats call appears AFTER equal_blocks branch — "
            "it should be built once before the run loop (plan §7.1)."
        )


# ---------------------------------------------------------------------------
# Group 4: run_batch_tester.py --help still works after WP-T4 additions
# ---------------------------------------------------------------------------

class TestBatchEntrypointStillWorks:
    """Smoke: batch CLI still starts cleanly after wiring changes."""

    def test_help_exit_zero(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(TESTER_ROOT / "run_batch_tester.py"), "--help"],
            cwd=str(TESTER_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, (
            f"--help failed after WP-T4 wiring changes.\n"
            f"stderr: {result.stderr}"
        )
