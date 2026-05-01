"""
Tests for warmup period utilities, focused on resolved warmup values.

No backtest execution; all tests operate at the config/calculation layer.
"""

import pytest

from supertrend_optimizer.utils.warmup import (
    apply_auto_warmup_to_config,
    calculate_warmup,
    clamp,
)
from supertrend_optimizer.utils.constants import (
    MIN_AUTO_WARMUP,
    MAX_AUTO_WARMUP,
    DEFAULT_ATR_PERIOD_MAX,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(warmup_period: int = 0,
              warmup_period_auto: bool = False,
              atr_period_max: int = 20) -> dict:
    return {
        "validation": {
            "warmup_period": warmup_period,
            "warmup_period_auto": warmup_period_auto,
        },
        "optimization": {
            "atr_period_range": [5, atr_period_max],
        },
    }


# ---------------------------------------------------------------------------
# apply_auto_warmup_to_config — Variant A (auto=True)
# ---------------------------------------------------------------------------

class TestApplyAutoWarmupVariantA:
    """Variant A: warmup_period_auto=True, warmup_period=0."""

    def test_variant_a_10_pct_clamp_lower(self):
        """n=500 → 10%=50 → clamped to MIN_AUTO_WARMUP=100."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=True, atr_period_max=20)
        result = apply_auto_warmup_to_config(cfg, n=500)
        # auto = clamp(50, 100, 400) = 100; base = max(0, 20) = 20
        # warmup = max(20, 100) = 100
        assert result["validation"]["warmup_period"] == MIN_AUTO_WARMUP

    def test_variant_a_10_pct_in_range(self):
        """n=2000 → 10%=200 → within [100, 400]; atr_max=20 → warmup=200."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=True, atr_period_max=20)
        result = apply_auto_warmup_to_config(cfg, n=2000)
        expected = 200  # clamp(200, 100, 400)=200; max(20,200)=200
        assert result["validation"]["warmup_period"] == expected

    def test_variant_a_10_pct_clamp_upper(self):
        """n=5000 → 10%=500 → clamped to MAX_AUTO_WARMUP=400."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=True, atr_period_max=20)
        result = apply_auto_warmup_to_config(cfg, n=5000)
        assert result["validation"]["warmup_period"] == MAX_AUTO_WARMUP

    def test_variant_a_atr_max_wins_over_clamp(self):
        """atr_period_max=450 > MAX_AUTO_WARMUP=400 → warmup=450."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=True, atr_period_max=450)
        result = apply_auto_warmup_to_config(cfg, n=500)
        # auto = clamp(50, 100, 400) = 100; auto = max(100, 450) = 450
        # base = max(0, 450) = 450; warmup = max(450, 450) = 450
        assert result["validation"]["warmup_period"] == 450

    def test_variant_a_explicit_warmup_ignored_when_auto_larger(self):
        """explicit warmup_period=50 but auto=200 → result=200."""
        cfg = _make_cfg(warmup_period=50, warmup_period_auto=True, atr_period_max=10)
        result = apply_auto_warmup_to_config(cfg, n=2000)
        assert result["validation"]["warmup_period"] == 200

    def test_variant_a_explicit_warmup_wins_when_larger(self):
        """explicit warmup_period=350 > auto=200 (n=2000) → base=max(350,10)=350."""
        cfg = _make_cfg(warmup_period=350, warmup_period_auto=True, atr_period_max=10)
        result = apply_auto_warmup_to_config(cfg, n=2000)
        # auto = 200; base = max(350, 10) = 350; warmup = max(350, 200) = 350
        assert result["validation"]["warmup_period"] == 350

    def test_variant_a_does_not_mutate_original(self):
        """Original cfg must remain untouched after the call."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=True, atr_period_max=20)
        original_warmup = cfg["validation"]["warmup_period"]
        _ = apply_auto_warmup_to_config(cfg, n=2000)
        assert cfg["validation"]["warmup_period"] == original_warmup

    def test_variant_a_result_is_different_dict(self):
        """Returned config must be a separate object from the input."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=True)
        result = apply_auto_warmup_to_config(cfg, n=1000)
        assert result is not cfg
        assert result["validation"] is not cfg["validation"]


# ---------------------------------------------------------------------------
# apply_auto_warmup_to_config — auto disabled
# ---------------------------------------------------------------------------

class TestApplyAutoWarmupDisabled:
    """warmup_period_auto=False: warmup = max(warmup_period, atr_period_max)."""

    def test_auto_false_uses_atr_max_when_larger(self):
        """atr_period_max=30 > warmup_period=0 → result=30."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=False, atr_period_max=30)
        result = apply_auto_warmup_to_config(cfg, n=2000)
        assert result["validation"]["warmup_period"] == 30

    def test_auto_false_uses_explicit_warmup_when_larger(self):
        """explicit warmup_period=100 > atr_max=30 → result=100."""
        cfg = _make_cfg(warmup_period=100, warmup_period_auto=False, atr_period_max=30)
        result = apply_auto_warmup_to_config(cfg, n=2000)
        assert result["validation"]["warmup_period"] == 100

    def test_auto_false_ignores_n(self):
        """Data size has no effect when auto is disabled."""
        cfg = _make_cfg(warmup_period=50, warmup_period_auto=False, atr_period_max=20)
        small = apply_auto_warmup_to_config(cfg, n=100)
        large = apply_auto_warmup_to_config(cfg, n=100_000)
        assert small["validation"]["warmup_period"] == large["validation"]["warmup_period"]

    def test_auto_false_does_not_mutate_original(self):
        cfg = _make_cfg(warmup_period=50, warmup_period_auto=False, atr_period_max=20)
        _ = apply_auto_warmup_to_config(cfg, n=2000)
        assert cfg["validation"]["warmup_period"] == 50

    def test_auto_key_missing_treated_as_false(self):
        """When warmup_period_auto key is absent, behaves like False."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=False, atr_period_max=15)
        del cfg["validation"]["warmup_period_auto"]
        result = apply_auto_warmup_to_config(cfg, n=3000)
        assert result["validation"]["warmup_period"] == 15  # max(0, 15)


# ---------------------------------------------------------------------------
# Sanity: CLI path == direct-call path
# ---------------------------------------------------------------------------

class TestCliDirectCallEquivalence:
    """Both paths must produce identical resolved warmup."""

    def test_auto_true_same_result(self):
        """After apply_auto_warmup_to_config, val_cfg['warmup_period'] equals
        what the CLI would have stored in cfg_eff."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=True, atr_period_max=20)
        n = 3000
        # CLI path: calculate_warmup → warmup_period
        cli_warmup = calculate_warmup(n, cfg)
        # Direct path: apply_auto_warmup_to_config
        direct_cfg = apply_auto_warmup_to_config(cfg, n)
        assert direct_cfg["validation"]["warmup_period"] == cli_warmup

    def test_auto_false_same_result(self):
        cfg = _make_cfg(warmup_period=80, warmup_period_auto=False, atr_period_max=25)
        n = 1500
        cli_warmup = calculate_warmup(n, cfg)
        direct_cfg = apply_auto_warmup_to_config(cfg, n)
        assert direct_cfg["validation"]["warmup_period"] == cli_warmup

    def test_idempotent_double_call(self):
        """Calling apply_auto_warmup_to_config twice gives the same result."""
        cfg = _make_cfg(warmup_period=0, warmup_period_auto=True, atr_period_max=20)
        n = 2000
        once = apply_auto_warmup_to_config(cfg, n)
        twice = apply_auto_warmup_to_config(once, n)
        assert once["validation"]["warmup_period"] == twice["validation"]["warmup_period"]


# ---------------------------------------------------------------------------
# clamp helper
# ---------------------------------------------------------------------------

class TestClamp:
    def test_below_min(self):
        assert clamp(50, 100, 400) == 100

    def test_above_max(self):
        assert clamp(500, 100, 400) == 400

    def test_within_range(self):
        assert clamp(200, 100, 400) == 200

    def test_at_min(self):
        assert clamp(100, 100, 400) == 100

    def test_at_max(self):
        assert clamp(400, 100, 400) == 400


# ---------------------------------------------------------------------------
# Sparse / partial configs — regression guard for KeyError fixes
# ---------------------------------------------------------------------------

class TestSparseConfigFallbacks:
    """
    calculate_warmup and apply_auto_warmup_to_config must not raise KeyError
    when called with sparse (partial) configs.  The fallback for a missing
    atr_period_range is DEFAULT_ATR_PERIOD_MAX (currently 55).
    """

    def test_empty_config_no_error(self):
        """Completely empty config must not raise."""
        result = calculate_warmup(n=1000, cfg={})
        assert result == DEFAULT_ATR_PERIOD_MAX  # max(0, 55) = 55

    def test_optimization_key_missing(self):
        """Config with no 'optimization' section uses DEFAULT_ATR_PERIOD_MAX."""
        cfg = {"validation": {"warmup_period": 0}}
        result = calculate_warmup(n=1000, cfg=cfg)
        assert result == DEFAULT_ATR_PERIOD_MAX

    def test_atr_period_range_missing(self):
        """'optimization' present but without atr_period_range uses DEFAULT_ATR_PERIOD_MAX."""
        cfg = {"optimization": {"objective_metric": "sortino"}}
        result = calculate_warmup(n=1000, cfg=cfg)
        assert result == DEFAULT_ATR_PERIOD_MAX

    def test_validation_key_missing(self):
        """Config with no 'validation' section uses warmup_period=0 as base."""
        cfg = {"optimization": {"atr_period_range": [5, 30]}}
        result = calculate_warmup(n=1000, cfg=cfg)
        assert result == 30  # max(0, 30) = 30

    def test_apply_auto_warmup_empty_config_no_error(self):
        """apply_auto_warmup_to_config with {} must not raise and sets warmup_period."""
        result = apply_auto_warmup_to_config({}, n=1000)
        assert "validation" in result
        assert result["validation"]["warmup_period"] == DEFAULT_ATR_PERIOD_MAX

    def test_apply_auto_warmup_no_validation_key(self):
        """Config without 'validation' key — returned config must have validation.warmup_period."""
        cfg = {"optimization": {"objective_metric": "sortino"}}
        result = apply_auto_warmup_to_config(cfg, n=500)
        assert result["validation"]["warmup_period"] == DEFAULT_ATR_PERIOD_MAX

    def test_apply_auto_warmup_preserves_other_validation_keys(self):
        """apply_auto_warmup_to_config must not drop existing validation sub-keys."""
        cfg = {
            "validation": {
                "warmup_period": 0,
                "walk_forward": {"enabled": True},
            },
            "optimization": {"atr_period_range": [5, 20]},
        }
        result = apply_auto_warmup_to_config(cfg, n=1000)
        assert "walk_forward" in result["validation"]
        assert result["validation"]["walk_forward"]["enabled"] is True

    def test_default_atr_period_max_is_55(self):
        """Guard: DEFAULT_ATR_PERIOD_MAX must equal the unified project default (55)."""
        assert DEFAULT_ATR_PERIOD_MAX == 55

    def test_sparse_config_warmup_is_only_default_atr(self):
        """
        Sparse config (no atr_period_range, no warmup_period, auto=False) yields
        max(0, DEFAULT_ATR_PERIOD_MAX) = DEFAULT_ATR_PERIOD_MAX.
        No silent over/under-estimation.
        """
        cfg = {"optimization": {}, "validation": {}}
        result = calculate_warmup(n=9999, cfg=cfg)
        assert result == DEFAULT_ATR_PERIOD_MAX
