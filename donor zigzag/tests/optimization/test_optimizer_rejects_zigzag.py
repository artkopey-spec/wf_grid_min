"""
Optimizer / walk-forward rejection of ZigZag filter modes
(plan v2.0.1 §3.9, §6.4).

ZigZag is tester-only: validate_config must refuse mode=zigzag and
mode=zigzag_and_volume with a clear ConfigError.
"""
from __future__ import annotations

import pytest

from supertrend_optimizer.cli.validators import (
    _TESTER_ONLY_MODES,
    validate_config,
)
from supertrend_optimizer.utils.exceptions import ConfigError


def _minimal_optimizer_cfg(filter_mode: str) -> dict:
    return {
        "optimization": {
            "atr_period_range": [10, 20],
            "multiplier_range": [1.0, 5.0],
            "multiplier_step": 0.5,
            "n_trials": 100,
            "n_jobs": 1,
        },
        "validation": {"warmup_period": 0},
        "backtest": {
            "commission": 0.001,
            "early_exit_enabled": False,
            "annualization_factor": 252,
            "min_trades_required": 3,
        },
        "filters": {"mode": filter_mode},
    }


class TestOptimizerRejectsZigZag:

    def test_zigzag_mode_raises_config_error(self):
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(_minimal_optimizer_cfg("zigzag"))

    def test_zigzag_and_volume_mode_raises_config_error(self):
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(_minimal_optimizer_cfg("zigzag_and_volume"))

    def test_error_message_mentions_mode(self):
        with pytest.raises(ConfigError, match="zigzag"):
            validate_config(_minimal_optimizer_cfg("zigzag"))

    def test_tester_only_modes_includes_zigzag(self):
        assert "zigzag" in _TESTER_ONLY_MODES
        assert "zigzag_and_volume" in _TESTER_ONLY_MODES

    def test_amplitude_still_rejected_after_refactor(self):
        # Sanity: refactoring _AMP_ONLY_MODES → _TESTER_ONLY_MODES must not
        # have broken amplitude-mode rejection.
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(_minimal_optimizer_cfg("amplitude"))
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(_minimal_optimizer_cfg("amplitude_and_volume"))

    def test_none_mode_still_passes(self):
        # Non-tester-only modes must still succeed past the guard.
        # (May fail later on missing sections — that's OK; we only want to
        # confirm the ZigZag guard is not over-eager.)
        try:
            validate_config(_minimal_optimizer_cfg("none"))
        except ConfigError as e:
            # Ensure it is NOT the tester-only guard that fired.
            assert "tester-only" not in str(e)
