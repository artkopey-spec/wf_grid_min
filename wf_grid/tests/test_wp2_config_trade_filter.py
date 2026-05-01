"""
WP2 — Unit tests: trade_filter config schema, loader validation, raw-key presence.

Covers every rule in plan §6.5, the three-case raw-presence test from §6.4.1,
and the explicit non-rule (freeze_confirmed_legs < local_window is valid).

Spec reference: Appendix A v1.1 §11, §11.1, §11.2, §11.3, §15.6, §17.2
Plan reference: §6.3, §6.4, §6.4.1, §6.5
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from wf_grid.config.loader import ConfigError, load_grid_config
from wf_grid.config.schema import (
    GridConfig,
    TradeFilterConfig,
    TradeFilterDiagnosticsConfig,
    TradeFilterLifecycleConfig,
    TradeFilterTriggerToggleConfig,
    TradeFilterTriggersConfig,
    TradeFilterZigZagConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, content: str) -> str:
    """Write YAML to a temp file and return its path."""
    p = tmp_path / "cfg.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


# Minimal valid YAML base (no trade_filter)
_MINIMAL_BASE = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""

# Minimal enabled trade_filter YAML appended to _MINIMAL_BASE
_ENABLED_BLOCK = """\
trade_filter:
  enabled: true
  type: zigzag_st_mode
  zigzag:
    reversal_threshold: 0.005
    candidate_trigger_threshold: 0.012
    local_window: 5
  triggers:
    candidate_threshold:
      enabled: true
    confirmed_median:
      enabled: true
  lifecycle:
    freeze_confirmed_legs: 3
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
"""


def _write_enabled(tmp_path: Path, override: str = "") -> str:
    """Write a config with enabled trade_filter, optionally overriding lines."""
    return _write(tmp_path, _MINIMAL_BASE + _ENABLED_BLOCK + override)


def _write_disabled(tmp_path: Path, extra: str = "") -> str:
    """Write a config with disabled trade_filter."""
    block = "trade_filter:\n  enabled: false\n  type: zigzag_st_mode\n"
    return _write(tmp_path, _MINIMAL_BASE + block + extra)


def _assert_error(tmp_path: Path, yaml: str, fragment: str) -> None:
    """Assert that loading the YAML raises ConfigError containing *fragment*."""
    path = _write(tmp_path, yaml)
    with pytest.raises(ConfigError, match=fragment):
        load_grid_config(path)


def _assert_ok(tmp_path: Path, yaml: str) -> GridConfig:
    """Assert that loading the YAML succeeds and return the config."""
    path = _write(tmp_path, yaml)
    return load_grid_config(path)


# ---------------------------------------------------------------------------
# 1. No trade_filter block → trade_filter is None
# ---------------------------------------------------------------------------

class TestNoTradeFilterBlock:
    def test_absent_block_gives_none(self, tmp_path):
        cfg = _assert_ok(tmp_path, _MINIMAL_BASE)
        assert cfg.trade_filter is None

    def test_absent_block_baseline_compatible(self, tmp_path):
        """Absent trade_filter must not alter any other config field (§14.18)."""
        cfg = _assert_ok(tmp_path, _MINIMAL_BASE)
        assert cfg.trade_filter is None
        assert cfg.data.file_path == "data.csv"


# ---------------------------------------------------------------------------
# 2. Disabled trade_filter (§11.1)
# ---------------------------------------------------------------------------

class TestDisabledTradeFilter:
    def test_disabled_with_type_ok(self, tmp_path):
        cfg = _assert_ok(tmp_path, _MINIMAL_BASE + (
            "trade_filter:\n  enabled: false\n  type: zigzag_st_mode\n"
        ))
        assert cfg.trade_filter is not None
        assert cfg.trade_filter.enabled is False
        assert cfg.trade_filter.type == "zigzag_st_mode"

    def test_disabled_without_type_ok(self, tmp_path):
        """enabled=false without type is valid (§11.1)."""
        cfg = _assert_ok(tmp_path, _MINIMAL_BASE + (
            "trade_filter:\n  enabled: false\n"
        ))
        assert cfg.trade_filter.enabled is False
        assert cfg.trade_filter.type is None

    def test_disabled_wrong_type_reject(self, tmp_path):
        """enabled=false with type != zigzag_st_mode must reject."""
        _assert_error(
            tmp_path,
            _MINIMAL_BASE + "trade_filter:\n  enabled: false\n  type: unknown_filter\n",
            "not supported",
        )

    def test_disabled_unknown_key_reject(self, tmp_path):
        """Strict schema rejects unknown keys even when disabled (§11.1)."""
        _assert_error(
            tmp_path,
            _MINIMAL_BASE + (
                "trade_filter:\n  enabled: false\n  type: zigzag_st_mode\n"
                "  typo_key: 1\n"
            ),
            "unknown config key",
        )


# ---------------------------------------------------------------------------
# 3. enabled key rules
# ---------------------------------------------------------------------------

class TestEnabledKey:
    def test_enabled_absent_reject(self, tmp_path):
        """trade_filter present but enabled absent → required error."""
        _assert_error(
            tmp_path,
            _MINIMAL_BASE + "trade_filter:\n  type: zigzag_st_mode\n",
            "enabled is required",
        )

    def test_enabled_not_bool_string_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            _MINIMAL_BASE + "trade_filter:\n  enabled: 'yes'\n  type: zigzag_st_mode\n",
            "must be bool",
        )

    def test_enabled_not_bool_int_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            _MINIMAL_BASE + "trade_filter:\n  enabled: 1\n  type: zigzag_st_mode\n",
            "must be bool",
        )


# ---------------------------------------------------------------------------
# 4. type rules (enabled=true)
# ---------------------------------------------------------------------------

class TestTypeWhenEnabled:
    def test_type_absent_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            _MINIMAL_BASE + (
                "trade_filter:\n  enabled: true\n"
                "  zigzag:\n    reversal_threshold: 0.005\n    local_window: 5\n"
                "  triggers:\n    candidate_threshold:\n      enabled: true\n"
                "    confirmed_median:\n      enabled: true\n"
                "  lifecycle:\n    freeze_confirmed_legs: 3\n"
                "    stop_check: confirm_bar_only\n    stopping_exit: opposite_st_flip\n"
            ),
            "type is required",
        )

    def test_unsupported_type_reject(self, tmp_path):
        base = _MINIMAL_BASE + _ENABLED_BLOCK.replace(
            "type: zigzag_st_mode", "type: other_filter"
        )
        _assert_error(tmp_path, base, "not supported")

    def test_valid_type_ok(self, tmp_path):
        cfg = load_grid_config(_write(tmp_path, _MINIMAL_BASE + _ENABLED_BLOCK))
        assert cfg.trade_filter.type == "zigzag_st_mode"


# ---------------------------------------------------------------------------
# 5. Required sub-blocks when enabled=true
# ---------------------------------------------------------------------------

class TestRequiredSubBlocksWhenEnabled:
    def _base_enabled_no_blocks(self) -> str:
        return (
            _MINIMAL_BASE
            + "trade_filter:\n  enabled: true\n  type: zigzag_st_mode\n"
        )

    def test_missing_zigzag_reject(self, tmp_path):
        base = self._base_enabled_no_blocks() + (
            "  triggers:\n    candidate_threshold:\n      enabled: true\n"
            "    confirmed_median:\n      enabled: true\n"
            "  lifecycle:\n    freeze_confirmed_legs: 3\n"
            "    stop_check: confirm_bar_only\n    stopping_exit: opposite_st_flip\n"
        )
        _assert_error(tmp_path, base, "zigzag block is required")

    def test_missing_triggers_reject(self, tmp_path):
        base = self._base_enabled_no_blocks() + (
            "  zigzag:\n    reversal_threshold: 0.005\n    local_window: 5\n"
            "  lifecycle:\n    freeze_confirmed_legs: 3\n"
            "    stop_check: confirm_bar_only\n    stopping_exit: opposite_st_flip\n"
        )
        _assert_error(tmp_path, base, "triggers block is required")

    def test_missing_lifecycle_reject(self, tmp_path):
        base = self._base_enabled_no_blocks() + (
            "  zigzag:\n    reversal_threshold: 0.005\n    local_window: 5\n"
            "  triggers:\n    candidate_threshold:\n      enabled: true\n"
            "    confirmed_median:\n      enabled: true\n"
        )
        _assert_error(tmp_path, base, "lifecycle block is required")


# ---------------------------------------------------------------------------
# 6. Trigger rules
# ---------------------------------------------------------------------------

class TestTriggerRules:
    def _build(self, ct_enabled: str, cm_enabled: str) -> str:
        block = _ENABLED_BLOCK.replace(
            "    candidate_threshold:\n      enabled: true\n",
            f"    candidate_threshold:\n      enabled: {ct_enabled}\n",
        ).replace(
            "    confirmed_median:\n      enabled: true\n",
            f"    confirmed_median:\n      enabled: {cm_enabled}\n",
        )
        return _MINIMAL_BASE + block

    def test_both_triggers_disabled_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            self._build("false", "false"),
            "at least one trigger must be enabled",
        )

    def test_only_ct_enabled_ok(self, tmp_path):
        cfg = _assert_ok(tmp_path, self._build("true", "false"))
        assert cfg.trade_filter.triggers.candidate_threshold.enabled is True
        assert cfg.trade_filter.triggers.confirmed_median.enabled is False

    def test_only_cm_enabled_ok(self, tmp_path):
        cfg = _assert_ok(tmp_path, self._build("false", "true"))
        assert cfg.trade_filter.triggers.confirmed_median.enabled is True

    def test_ct_enabled_not_bool_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            self._build('"yes"', "true"),
            "must be bool",
        )

    def test_cm_enabled_not_bool_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            self._build("true", "1"),
            "must be bool",
        )


# ---------------------------------------------------------------------------
# 7. reversal_threshold rules (§11.2, §15.6)
# ---------------------------------------------------------------------------

class TestReversalThreshold:
    def _make(self, rt_value: str) -> str:
        return (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    reversal_threshold: 0.005\n",
                f"    reversal_threshold: {rt_value}\n",
            )
        )

    def test_valid_fraction_ok(self, tmp_path):
        cfg = _assert_ok(tmp_path, self._make("0.005"))
        assert cfg.trade_filter.zigzag.reversal_threshold == pytest.approx(0.005)

    def test_absent_reversal_threshold_reject(self, tmp_path):
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace("    reversal_threshold: 0.005\n", "")
        )
        _assert_error(tmp_path, yaml, "reversal_threshold is required")

    def test_percent_string_reject(self, tmp_path):
        _assert_error(tmp_path, self._make('"0.5%"'), "percent string")

    def test_other_string_reject(self, tmp_path):
        _assert_error(tmp_path, self._make('"half_percent"'), "not a string")

    def test_zero_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("0"), "in \\(0, 1\\)")

    def test_one_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("1"), "in \\(0, 1\\)")

    def test_negative_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("-0.005"), "in \\(0, 1\\)")

    def test_greater_than_one_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("1.5"), "in \\(0, 1\\)")


# ---------------------------------------------------------------------------
# 8. local_window rules
# ---------------------------------------------------------------------------

class TestLocalWindow:
    def _make(self, lw_value: str) -> str:
        return (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    local_window: 5\n",
                f"    local_window: {lw_value}\n",
            )
        )

    def test_valid_int_ok(self, tmp_path):
        cfg = _assert_ok(tmp_path, self._make("3"))
        assert cfg.trade_filter.zigzag.local_window == 3

    def test_zero_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("0"), "local_window must be integer >= 1")

    def test_negative_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("-1"), "local_window must be integer >= 1")

    def test_float_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("5.0"), "local_window must be integer >= 1")


# ---------------------------------------------------------------------------
# 9. candidate_trigger_threshold rules (§11.3)
# ---------------------------------------------------------------------------

class TestCandidateTriggerThreshold:
    def _make(self, ctt: str) -> str:
        return (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                f"    candidate_trigger_threshold: {ctt}\n",
            )
        )

    def test_valid_numeric_ok(self, tmp_path):
        cfg = _assert_ok(tmp_path, self._make("0.020"))
        assert cfg.trade_filter.zigzag.candidate_trigger_threshold == pytest.approx(0.020)

    def test_zero_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("0"), "in \\(0, 1\\)")

    def test_one_reject(self, tmp_path):
        _assert_error(tmp_path, self._make("1"), "in \\(0, 1\\)")

    def test_percent_string_reject(self, tmp_path):
        _assert_error(tmp_path, self._make('"1.2%"'), "percent string")

    def test_unknown_string_reject(self, tmp_path):
        _assert_error(tmp_path, self._make('"high"'), "numeric fraction or 'auto'")

    def test_auto_without_quantile_reject(self, tmp_path):
        """candidate_trigger_threshold=auto requires candidate_trigger_quantile."""
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                "    candidate_trigger_threshold: auto\n",
            )
        )
        _assert_error(tmp_path, yaml, "candidate_trigger_quantile is required")

    def test_auto_with_valid_quantile_ok(self, tmp_path):
        """candidate_trigger_threshold=auto + valid quantile → accept."""
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                "    candidate_trigger_threshold: auto\n"
                "    candidate_trigger_quantile: 0.80\n",
            )
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter.zigzag.candidate_trigger_threshold == "auto"
        assert cfg.trade_filter.zigzag.candidate_trigger_quantile == pytest.approx(0.80)

    def test_absent_ctt_rejects(self, tmp_path):
        """candidate_trigger_threshold absent when enabled → loader error (§11.2 / §11.3)."""
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace("    candidate_trigger_threshold: 0.012\n", "")
        )
        _assert_error(tmp_path, yaml, "candidate_trigger_threshold is required")


# ---------------------------------------------------------------------------
# 10. candidate_trigger_quantile rules (§11.3)
# ---------------------------------------------------------------------------

class TestCandidateTriggerQuantile:
    def test_quantile_out_of_range_zero_reject(self, tmp_path):
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                "    candidate_trigger_threshold: auto\n"
                "    candidate_trigger_quantile: 0.0\n",
            )
        )
        _assert_error(tmp_path, yaml, "in \\(0, 1\\)")

    def test_quantile_out_of_range_one_reject(self, tmp_path):
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                "    candidate_trigger_threshold: auto\n"
                "    candidate_trigger_quantile: 1.0\n",
            )
        )
        _assert_error(tmp_path, yaml, "in \\(0, 1\\)")

    def test_quantile_boundary_ok(self, tmp_path):
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                "    candidate_trigger_threshold: auto\n"
                "    candidate_trigger_quantile: 0.5\n",
            )
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter.zigzag.candidate_trigger_quantile == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 11. Three-case raw YAML presence test (§6.4.1 / plan §6.5 acceptance gate)
#     This is the canonical guard against raw-presence tracking regressions.
# ---------------------------------------------------------------------------

class TestRawYAMLPresenceThreeCases:
    """Spec reference: Appendix A v1.1 §11.3; plan §6.4.1 п.5, §6.5 Note 2."""

    def test_case1_numeric_without_quantile_accept(self, tmp_path):
        """Case 1 (§6.4.1 п.5):
        candidate_trigger_threshold=0.012 with NO candidate_trigger_quantile key
        in YAML → accept.  The raw key is absent; default None must not trigger
        the 'numeric + explicit quantile' reject rule.
        """
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK
            # candidate_trigger_threshold: 0.012 is already in _ENABLED_BLOCK
            # candidate_trigger_quantile is NOT present in YAML
        )
        cfg = _assert_ok(tmp_path, yaml)
        # quantile must remain None (dataclass default, not materialised)
        assert cfg.trade_filter.zigzag.candidate_trigger_quantile is None

    def test_case2_numeric_with_explicit_quantile_reject(self, tmp_path):
        """Case 2 (§6.4.1 п.5):
        candidate_trigger_threshold=0.012 AND candidate_trigger_quantile=0.80
        explicitly in YAML → reject (§11.3).  The raw-key presence flag fires
        even though both dataclass fields carry valid-looking values.
        """
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                "    candidate_trigger_threshold: 0.012\n"
                "    candidate_trigger_quantile: 0.80\n",
            )
        )
        _assert_error(tmp_path, yaml, "must not be specified")

    def test_case3_auto_with_explicit_quantile_accept(self, tmp_path):
        """Case 3 (§6.4.1 п.5):
        candidate_trigger_threshold=auto AND candidate_trigger_quantile=0.80
        explicitly in YAML → accept (auto requires quantile).
        """
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                "    candidate_trigger_threshold: auto\n"
                "    candidate_trigger_quantile: 0.80\n",
            )
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter.zigzag.candidate_trigger_threshold == "auto"
        assert cfg.trade_filter.zigzag.candidate_trigger_quantile == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# 12. Lifecycle rules
# ---------------------------------------------------------------------------

class TestLifecycleRules:
    def _make_lc(self, fcl: str, sc: str, se: str) -> str:
        block = _ENABLED_BLOCK.replace(
            "    freeze_confirmed_legs: 3\n",
            f"    freeze_confirmed_legs: {fcl}\n",
        ).replace(
            "    stop_check: confirm_bar_only\n",
            f"    stop_check: {sc}\n",
        ).replace(
            "    stopping_exit: opposite_st_flip\n",
            f"    stopping_exit: {se}\n",
        )
        return _MINIMAL_BASE + block

    def test_valid_lifecycle_ok(self, tmp_path):
        cfg = _assert_ok(tmp_path, self._make_lc("3", "confirm_bar_only", "opposite_st_flip"))
        assert cfg.trade_filter.lifecycle.freeze_confirmed_legs == 3
        assert cfg.trade_filter.lifecycle.stop_check == "confirm_bar_only"
        assert cfg.trade_filter.lifecycle.stopping_exit == "opposite_st_flip"

    def test_freeze_negative_reject(self, tmp_path):
        _assert_error(tmp_path, self._make_lc("-1", "confirm_bar_only", "opposite_st_flip"),
                      "freeze_confirmed_legs must be integer >= 0")

    def test_freeze_float_reject(self, tmp_path):
        _assert_error(tmp_path, self._make_lc("3.0", "confirm_bar_only", "opposite_st_flip"),
                      "freeze_confirmed_legs must be integer >= 0")

    def test_freeze_zero_ok(self, tmp_path):
        """freeze_confirmed_legs=0 is valid (>= 0)."""
        cfg = _assert_ok(tmp_path, self._make_lc("0", "confirm_bar_only", "opposite_st_flip"))
        assert cfg.trade_filter.lifecycle.freeze_confirmed_legs == 0

    def test_wrong_stop_check_literal_reject(self, tmp_path):
        _assert_error(tmp_path, self._make_lc("3", "every_bar", "opposite_st_flip"),
                      "stop_check must be one of")

    def test_wrong_stopping_exit_literal_reject(self, tmp_path):
        _assert_error(tmp_path, self._make_lc("3", "confirm_bar_only", "immediate"),
                      "stopping_exit must be one of")

    def test_freeze_less_than_local_window_valid(self, tmp_path):
        """freeze_confirmed_legs=2 < local_window=5 is VALID per §3.2 / §17.20.
        Must not produce a warning or a reject.  Explicit non-rule from plan §6.5 Note 1.
        """
        # local_window defaults to 5 in _ENABLED_BLOCK; freeze_confirmed_legs=2 < 5
        cfg = _assert_ok(tmp_path, self._make_lc("2", "confirm_bar_only", "opposite_st_flip"))
        assert cfg.trade_filter.lifecycle.freeze_confirmed_legs == 2
        assert cfg.trade_filter.zigzag.local_window == 5


# ---------------------------------------------------------------------------
# 13. ZigZag extra field rules
# ---------------------------------------------------------------------------

class TestZigZagExtraFields:
    def _make_zz(self, extra_yaml: str) -> str:
        return (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    local_window: 5\n",
                f"    local_window: 5\n{extra_yaml}",
            )
        )

    def test_global_stats_source_wrong_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            self._make_zz("    global_stats_source: per_wf_step\n"),
            "global_stats_source",
        )

    def test_leg_height_mode_wrong_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            self._make_zz("    leg_height_mode: abs\n"),
            "leg_height_mode",
        )

    def test_global_median_wrong_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            self._make_zz("    global_median: 0.008\n"),
            "global_median",
        )

    def test_unknown_key_in_zigzag_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            self._make_zz("    typo: 1\n"),
            "unknown config key",
        )


# ---------------------------------------------------------------------------
# 14. Unknown keys in trade_filter and sub-blocks
# ---------------------------------------------------------------------------

class TestUnknownKeys:
    def test_unknown_top_level_key_reject(self, tmp_path):
        _assert_error(
            tmp_path,
            _MINIMAL_BASE + "trade_filter:\n  enabled: false\n  unknown_key: 1\n",
            "unknown config key",
        )

    def test_unknown_trigger_key_reject(self, tmp_path):
        extra = "    candidate_threshold:\n      enabled: true\n      weight: 2\n"
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_threshold:\n      enabled: true\n",
                extra,
            )
        )
        _assert_error(tmp_path, yaml, "unknown config key")

    def test_unknown_lifecycle_key_reject(self, tmp_path):
        extra = "    extra_param: 99\n"
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    stopping_exit: opposite_st_flip\n",
                "    stopping_exit: opposite_st_flip\n" + extra,
            )
        )
        _assert_error(tmp_path, yaml, "unknown config key")


# ---------------------------------------------------------------------------
# 15. Full happy-path: enabled filter round-trips schema correctly
# ---------------------------------------------------------------------------

class TestEnabledHappyPath:
    def test_full_enabled_config_ok(self, tmp_path):
        path = _write(tmp_path, _MINIMAL_BASE + _ENABLED_BLOCK)
        cfg = load_grid_config(path)

        tf = cfg.trade_filter
        assert tf is not None
        assert tf.enabled is True
        assert tf.type == "zigzag_st_mode"

        zz = tf.zigzag
        assert zz.global_stats_source == "full_dataset"
        assert zz.leg_height_mode == "pct"
        assert zz.reversal_threshold == pytest.approx(0.005)
        assert zz.candidate_trigger_threshold == pytest.approx(0.012)
        assert zz.candidate_trigger_quantile is None   # §6.3 invariant: default None
        assert zz.global_median == "auto"
        assert zz.local_window == 5

        assert tf.triggers.candidate_threshold.enabled is True
        assert tf.triggers.confirmed_median.enabled is True

        lc = tf.lifecycle
        assert lc.freeze_confirmed_legs == 3
        assert lc.stop_check == "confirm_bar_only"
        assert lc.stopping_exit == "opposite_st_flip"

    def test_auto_threshold_with_quantile_ok(self, tmp_path):
        yaml = (
            _MINIMAL_BASE
            + _ENABLED_BLOCK.replace(
                "    candidate_trigger_threshold: 0.012\n",
                "    candidate_trigger_threshold: auto\n"
                "    candidate_trigger_quantile: 0.80\n",
            )
        )
        cfg = _assert_ok(tmp_path, yaml)
        zz = cfg.trade_filter.zigzag
        assert zz.candidate_trigger_threshold == "auto"
        assert zz.candidate_trigger_quantile == pytest.approx(0.80)

    def test_candidate_trigger_quantile_default_is_none(self, tmp_path):
        """§6.3 invariant: candidate_trigger_quantile dataclass default must be None.

        If this default ever changes, raw-key presence tracking breaks (§6.4.1 Note 2).
        """
        from wf_grid.config.schema import TradeFilterZigZagConfig
        assert TradeFilterZigZagConfig().candidate_trigger_quantile is None

    def test_candidate_trigger_threshold_default_is_none(self, tmp_path):
        """§6.3 invariant: candidate_trigger_threshold dataclass default must be None."""
        from wf_grid.config.schema import TradeFilterZigZagConfig
        assert TradeFilterZigZagConfig().candidate_trigger_threshold is None


# ---------------------------------------------------------------------------
# 16. Disabled config = baseline compatible
#     (enabled=false or absent → pipeline unchanged; §14.18, §17.1)
#     Runtime check deferred to test_wp1_baseline_capture.py (slow).
#     Here we verify config-level invariants.
# ---------------------------------------------------------------------------

class TestDisabledBaselineCompatibility:
    def test_disabled_trade_filter_does_not_alter_other_config(self, tmp_path):
        """Disabled block must not change any field outside trade_filter."""
        cfg_no_tf = _assert_ok(tmp_path, _MINIMAL_BASE)
        cfg_disabled = _assert_ok(
            tmp_path,
            _MINIMAL_BASE + "trade_filter:\n  enabled: false\n  type: zigzag_st_mode\n",
        )
        # All non-trade_filter fields must be identical
        import dataclasses
        for f in dataclasses.fields(cfg_no_tf):
            if f.name == "trade_filter":
                continue
            assert getattr(cfg_no_tf, f.name) == getattr(cfg_disabled, f.name), (
                f"Field {f.name!r} differs between no-trade_filter and disabled-trade_filter configs"
            )
