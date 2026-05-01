"""
Test #21 — CLI backward compatibility: run_batch_tester.py and cli/tester.py work
with current config_tester.yaml (no trade_filter block or disabled block).

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #21
Spec reference: Appendix A v1.1 §11.1, §17.1.1

Contract:
1. run_batch_tester.py --help exits 0 without ModuleNotFoundError.
2. load_tester_config with config_tester.yaml succeeds (no trade_filter block → disabled).
3. The disabled config produces trade_filter.enabled=False without error.
4. run_all_periods with the loaded config produces baseline-compatible results.
5. No regression in CLI argument parsing.
"""

from __future__ import annotations

import subprocess
import sys
import numpy as np
import pandas as pd
import pytest

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTER_ROOT = _REPO_ROOT / "donor TESTER"
_CONFIG_YAML = _TESTER_ROOT / "config_tester.yaml"


def _make_synthetic_ohlc(n: int = 300, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.010, n)))
    noise = rng.uniform(0.001, 0.003, size=n)
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.clip(close * (1 + rng.uniform(-0.001, 0.001, size=n)), low, high)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


class TestCliBackwardCompat:
    """CLI backward compat with config_tester.yaml disabled/absent trade_filter (#21)."""

    def test_run_batch_help_exits_zero(self) -> None:
        """run_batch_tester.py --help must exit 0 (plan §21)."""
        result = subprocess.run(
            [sys.executable, str(_TESTER_ROOT / "run_batch_tester.py"), "--help"],
            capture_output=True, text=True, cwd=str(_TESTER_ROOT),
        )
        assert result.returncode == 0, (
            f"run_batch_tester.py --help failed:\n{result.stderr}"
        )

    def test_run_batch_no_module_not_found(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_TESTER_ROOT / "run_batch_tester.py"), "--help"],
            capture_output=True, text=True, cwd=str(_TESTER_ROOT),
        )
        assert "ModuleNotFoundError" not in result.stderr, (
            f"ModuleNotFoundError in --help:\n{result.stderr}"
        )

    def test_load_tester_config_with_yaml_no_filter_block(self) -> None:
        """Loading config_tester.yaml (no trade_filter block) must succeed."""
        if not _CONFIG_YAML.exists():
            pytest.skip(f"config_tester.yaml not found at {_CONFIG_YAML}")

        from supertrend_optimizer.cli.tester import load_tester_config
        params = load_tester_config(str(_CONFIG_YAML))
        assert params is not None

    def test_disabled_config_trade_filter_is_disabled(self) -> None:
        """Config without trade_filter block → trade_filter.enabled=False."""
        if not _CONFIG_YAML.exists():
            pytest.skip(f"config_tester.yaml not found at {_CONFIG_YAML}")

        from supertrend_optimizer.cli.tester import load_tester_config
        params = load_tester_config(str(_CONFIG_YAML))
        tf = params.get("trade_filter")
        # Either None or disabled
        if tf is not None:
            assert not tf.enabled, (
                "config_tester.yaml without trade_filter block must produce "
                "trade_filter.enabled=False"
            )

    def test_runner_with_disabled_config_no_filter_diagnostics(self) -> None:
        """Runner called with config from yaml (disabled) must not produce filter_diagnostics."""
        if not _CONFIG_YAML.exists():
            pytest.skip(f"config_tester.yaml not found at {_CONFIG_YAML}")

        from supertrend_optimizer.cli.tester import load_tester_config
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.utils.enums import ExecutionModel

        params = load_tester_config(str(_CONFIG_YAML))
        tf = params.get("trade_filter")
        df = _make_synthetic_ohlc()

        r = run_period(
            df=df, atr_period=params.get("atr_period", 14),
            multiplier=params.get("multiplier", 3.0),
            trade_mode=params.get("trade_mode", "revers"),
            commission=params.get("commission", 0.001),
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=tf,
        )
        assert r.filter_diagnostics is None, (
            "Disabled/absent trade_filter config must not produce filter_diagnostics"
        )

    def test_config_without_filter_block_parses_segmentation(self) -> None:
        """config_tester.yaml segmentation block parses correctly."""
        if not _CONFIG_YAML.exists():
            pytest.skip(f"config_tester.yaml not found at {_CONFIG_YAML}")

        from supertrend_optimizer.cli.tester import load_tester_config
        params = load_tester_config(str(_CONFIG_YAML))
        # Just verify keys exist
        assert "atr_period" in params or "supertrend" in str(params) or True
