"""
Test #1 — Import smoke: all filter-wired modules importable after Phase 2 wiring.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #1
Spec reference: Appendix A v1.1 §17

Verifies that adding filter wiring did not break any import path for the
Mode-C tester.  Every module that was touched in WP-T2 … WP-T7 must be
importable without error.
"""

from __future__ import annotations
import pytest


class TestFilterWiredModulesImport:
    """All Phase-2-touched modules must import without error (#1)."""

    def test_trade_filter_config_importable(self) -> None:
        from supertrend_optimizer.core import trade_filter_config  # noqa: F401

    def test_trade_filter_config_exports(self) -> None:
        from supertrend_optimizer.core.trade_filter_config import (
            TradeFilterConfig,
            TradeFilterZigZagConfig,
            TradeFilterTriggersConfig,
            TradeFilterLifecycleConfig,
            TradeFilterDiagnosticsConfig,
            TradeFilterTriggerToggleConfig,
            validate_trade_filter,
        )
        assert TradeFilterConfig is not None

    def test_runner_importable(self) -> None:
        from supertrend_optimizer.testing.runner import (
            run_period, run_all_periods, run_equal_blocks, PeriodResult,
        )
        assert run_period is not None
        assert run_all_periods is not None

    def test_period_result_has_filter_fields(self) -> None:
        from supertrend_optimizer.testing.runner import PeriodResult
        import dataclasses
        fields = {f.name for f in dataclasses.fields(PeriodResult)}
        assert "filter_diagnostics" in fields
        assert "filter_diagnostics_summary" in fields

    def test_signal_events_importable(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events
        import inspect
        sig = inspect.signature(build_signal_events)
        assert "filter_diagnostics" in sig.parameters

    def test_excel_tester_importable(self) -> None:
        from supertrend_optimizer.io.excel_tester import (
            export_tester_results,
            export_equal_blocks_results,
            _two_step_trade_trigger_link,
        )
        import inspect
        sig = inspect.signature(export_tester_results)
        assert "trade_filter_config" in sig.parameters
        assert "df" in sig.parameters
        assert "config_yaml_snapshot" in sig.parameters
        assert "run_metadata" in sig.parameters
        assert "export_diagnostics" in sig.parameters
        assert "export_signals" in sig.parameters
        assert "export_false_start" in sig.parameters
        assert "export_cycle" in sig.parameters
        assert "export_trades" in sig.parameters
        sig_eq = inspect.signature(export_equal_blocks_results)
        assert "config_yaml_snapshot" in sig_eq.parameters
        assert "run_metadata" in sig_eq.parameters

    def test_build_zigzag_global_stats_importable(self) -> None:
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        assert callable(build_zigzag_global_stats)

    def test_run_batch_tester_importable_as_module(self) -> None:
        """run_batch_tester.py must be importable (its bootstrap code should not crash)."""
        from pathlib import Path
        import subprocess, sys
        script = Path(__file__).resolve().parents[1] / "run_batch_tester.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, (
            f"run_batch_tester.py --help failed:\n{result.stderr}"
        )
        assert "ModuleNotFoundError" not in result.stderr
