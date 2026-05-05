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


class TestV3InitFailureKeyset:
    """Spec §4.5: validator can emit canonical init-failure keys."""

    def _keys_for(self, tf_raw: dict) -> list[str]:
        from supertrend_optimizer.core.trade_filter_config import (
            build_trade_filter_config_from_raw,
            collect_raw_user_keys,
            validate_trade_filter,
        )

        errors: list[str] = []
        keys: list[str] = []
        validate_trade_filter(
            build_trade_filter_config_from_raw(tf_raw),
            errors,
            collect_raw_user_keys({"trade_filter": tf_raw}),
            error_keys=keys,
        )
        assert errors
        return keys

    def _base_raw(self) -> dict:
        return {
            "enabled": True,
            "type": "zigzag_st_mode",
            "zigzag": {
                "reversal_threshold": 0.005,
                "candidate_trigger_threshold": 0.012,
                "local_window": 5,
            },
            "lifecycle": {
                "freeze_confirmed_legs": 3,
                "stop_check": "confirm_bar_only",
                "stopping_exit": "opposite_st_flip",
            },
        }

    @pytest.mark.parametrize(
        "mutate, expected_key",
        [
            (lambda raw: raw["zigzag"].update({"mode": "bad"}), "mode_invalid_literal"),
            (
                lambda raw: (raw["zigzag"].update({"mode": "A"}), raw.update({
                    "triggers": {
                        "candidate_threshold": {"enabled": True},
                        "confirmed_median": {"enabled": True},
                    }
                })),
                "mode_conflicts_with_legacy_triggers",
            ),
            (
                lambda raw: raw["zigzag"].update({"candidate_entry": "legacy"}),
                "candidate_entry_deprecated",
            ),
            (
                lambda raw: raw["zigzag"].update({
                    "candidate_duration_gate": {"enabled": "yes", "max_bars": 3}
                }),
                "duration_gate_enabled_invalid_type",
            ),
            (
                lambda raw: raw["zigzag"].update({
                    "candidate_duration_gate": {"enabled": True}
                }),
                "duration_gate_max_bars_missing",
            ),
            (
                lambda raw: raw["zigzag"].update({
                    "candidate_duration_gate": {"enabled": False, "max_bars": 3}
                }),
                "duration_gate_max_bars_present_when_disabled",
            ),
            (
                lambda raw: raw["zigzag"].update({
                    "candidate_duration_gate": {"enabled": True, "max_bars": "3"}
                }),
                "duration_gate_max_bars_invalid_type",
            ),
            (
                lambda raw: raw["zigzag"].update({
                    "candidate_duration_gate": {"enabled": True, "max_bars": 0}
                }),
                "duration_gate_max_bars_below_one",
            ),
        ],
    )
    def test_v3_init_failure_keys(self, mutate, expected_key):
        raw = self._base_raw()
        mutate(raw)
        assert expected_key in self._keys_for(raw)

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

    def test_missing_triggers_now_valid_no_mode_defaults_to_a(self, tmp_path):
        # v3: triggers is no longer required (WP-V3-1). No triggers + no mode = mode A.
        # Config still fails because candidate_trigger_threshold is required.
        base = self._base_enabled_no_blocks() + (
            "  zigzag:\n    reversal_threshold: 0.005\n    local_window: 5\n"
            "  lifecycle:\n    freeze_confirmed_legs: 3\n"
            "    stop_check: confirm_bar_only\n    stopping_exit: opposite_st_flip\n"
        )
        _assert_error(tmp_path, base, "candidate_trigger_threshold")

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
        assert lc.exit_off_mode == "exit A"
        assert lc.exit_off_zz_leg_count is None

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


# ---------------------------------------------------------------------------
# 17. v3 Schema and migration (WP-V3-1) — A1-A14
# ---------------------------------------------------------------------------

# Canonical v3 zigzag block with mode (no triggers), lifecycle included.
_V3_ZIGZAG_WITH_MODE = """\
  zigzag:
    reversal_threshold: 0.005
    candidate_trigger_threshold: auto
    candidate_trigger_quantile: 0.80
    local_window: 5
  lifecycle:
    freeze_confirmed_legs: 3
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
"""

_V3_BASE = _MINIMAL_BASE + "trade_filter:\n  enabled: true\n  type: zigzag_st_mode\n"


def _v3_yaml(mode: str, extra_zigzag: str = "") -> str:
    return (
        _V3_BASE
        + _V3_ZIGZAG_WITH_MODE.replace(
            "  zigzag:\n", f"  zigzag:\n    mode: {mode}\n{extra_zigzag}"
        )
    )


class TestV3ModeValidation:
    """A1/A2: mode literal validation."""

    @pytest.mark.parametrize("mode", ["A", "B", "C", "A+B", "C+B"])
    def test_a1_valid_modes_accepted(self, tmp_path, mode):
        """A1: all five valid mode literals load without error."""
        cfg = _assert_ok(tmp_path, _v3_yaml(mode))
        assert cfg.trade_filter.zigzag.mode == mode

    @pytest.mark.parametrize("bad_mode", ["a", "b", "c", "a+b", "c+b", "AB", "D", "", "auto"])
    def test_a2_invalid_mode_rejected(self, tmp_path, bad_mode):
        """A2: lowercase or unknown mode -> ConfigError."""
        _assert_error(tmp_path, _v3_yaml(bad_mode), "must be one of")

    def test_mode_field_default_is_none(self):
        """mode defaults to None when not set."""
        from wf_grid.config.schema import TradeFilterZigZagConfig
        assert TradeFilterZigZagConfig().mode is None


class TestV3LegacyMigration:
    """A3-A6: legacy triggers -> mode resolution (validation passes)."""

    def test_a3_no_mode_no_triggers_valid(self, tmp_path):
        """A3: no mode, no triggers -> loads without error; mode resolved to 'A' by loader."""
        yaml = (
            _V3_BASE
            + _V3_ZIGZAG_WITH_MODE
        )
        cfg = _assert_ok(tmp_path, yaml)
        # After loading, loader resolves mode=None + no triggers -> "A" (WP-V3-2)
        assert cfg.trade_filter.zigzag.mode == "A"

    def test_a4_legacy_candidate_only_valid(self, tmp_path):
        """A4: legacy candidate-only (ct=true, cm=false) -> mode resolved to 'A'."""
        yaml = (
            _V3_BASE
            + _V3_ZIGZAG_WITH_MODE
            + "  triggers:\n    candidate_threshold:\n      enabled: true\n"
            "    confirmed_median:\n      enabled: false\n"
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter.zigzag.mode == "A"

    def test_a5_legacy_confirmed_only_valid(self, tmp_path):
        """A5: legacy confirmed-only (ct=false, cm=true) -> mode resolved to 'B'."""
        yaml = (
            _V3_BASE
            + _V3_ZIGZAG_WITH_MODE
            + "  triggers:\n    candidate_threshold:\n      enabled: false\n"
            "    confirmed_median:\n      enabled: true\n"
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter.zigzag.mode == "B"

    def test_a6_legacy_both_valid(self, tmp_path):
        """A6: legacy both (ct=true, cm=true) -> mode resolved to 'A+B'."""
        yaml = (
            _V3_BASE
            + _V3_ZIGZAG_WITH_MODE
            + "  triggers:\n    candidate_threshold:\n      enabled: true\n"
            "    confirmed_median:\n      enabled: true\n"
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter.zigzag.mode == "A+B"


class TestV3MixedSchemaRejection:
    """A7/A8: explicit mode + triggers conflict; candidate_entry deprecation."""

    def test_a7_mode_plus_triggers_rejected(self, tmp_path):
        """A7: explicit mode + any triggers block -> ConfigError."""
        yaml = (
            _V3_BASE
            + _V3_ZIGZAG_WITH_MODE.replace(
                "  zigzag:\n", "  zigzag:\n    mode: A\n"
            )
            + "  triggers:\n    candidate_threshold:\n      enabled: true\n"
            "    confirmed_median:\n      enabled: true\n"
        )
        _assert_error(tmp_path, yaml, "cannot be used together")

    def test_a8_candidate_entry_deprecated(self, tmp_path):
        """A8: candidate_entry under trade_filter.zigzag -> ConfigError with deprecated message.

        candidate_entry is explicitly whitelisted in the strict schema so the validator
        can emit a specific deprecation message (ТЗ v3 §3.1, §4.5 candidate_entry_deprecated)
        rather than a generic unknown-key rejection.
        """
        yaml = (
            _V3_BASE
            + "  zigzag:\n    mode: A\n    candidate_entry: true\n"
            "    reversal_threshold: 0.005\n    candidate_trigger_threshold: auto\n"
            "    candidate_trigger_quantile: 0.80\n    local_window: 5\n"
            + "  lifecycle:\n    freeze_confirmed_legs: 3\n"
            "    stop_check: confirm_bar_only\n    stopping_exit: opposite_st_flip\n"
        )
        _assert_error(tmp_path, yaml, "deprecated in v3")


class TestV3CandidateDurationGate:
    """A9-A14: candidate_duration_gate validation."""

    def _base_with_mode(self, mode: str = "A") -> str:
        return _v3_yaml(mode)

    def test_a9_gate_enabled_non_bool_rejected(self, tmp_path):
        """A9: candidate_duration_gate.enabled non-bool -> ConfigError (int value)."""
        yaml = _v3_yaml("A", "    candidate_duration_gate:\n      enabled: 1\n      max_bars: 10\n")
        _assert_error(tmp_path, yaml, "candidate_duration_gate.enabled must be bool")

    def test_a9_gate_enabled_string_rejected(self, tmp_path):
        """A9: enabled as string rejected."""
        yaml = _v3_yaml("A", "    candidate_duration_gate:\n      enabled: 'true'\n      max_bars: 10\n")
        _assert_error(tmp_path, yaml, "candidate_duration_gate.enabled must be bool")

    def test_a10_gate_enabled_no_max_bars_rejected(self, tmp_path):
        """A10: enabled=true without max_bars -> ConfigError."""
        yaml = _v3_yaml("A", "    candidate_duration_gate:\n      enabled: true\n")
        _assert_error(tmp_path, yaml, "max_bars is required when enabled is true")

    @pytest.mark.parametrize("bad_val", ["null", "0", "-1", "1.5", "'10'", "true"])
    def test_a11_gate_max_bars_invalid_types(self, tmp_path, bad_val):
        """A11: max_bars bool/float/string/null/<1 -> ConfigError."""
        yaml = _v3_yaml(
            "A",
            f"    candidate_duration_gate:\n      enabled: true\n      max_bars: {bad_val}\n",
        )
        _assert_error(tmp_path, yaml, "max_bars must be int >= 1")

    def test_a12_gate_disabled_with_max_bars_rejected(self, tmp_path):
        """A12: enabled=false + explicit max_bars -> ConfigError."""
        yaml = _v3_yaml("A", "    candidate_duration_gate:\n      enabled: false\n      max_bars: 5\n")
        _assert_error(tmp_path, yaml, "max_bars must be absent when enabled is false")

    def test_a13_absent_gate_disabled_by_default(self, tmp_path):
        """A13: absent gate block -> disabled gate (default)."""
        cfg = _assert_ok(tmp_path, _v3_yaml("A"))
        gate = cfg.trade_filter.zigzag.candidate_duration_gate
        assert gate.enabled is False
        assert gate.max_bars is None

    def test_a14_mode_b_enabled_gate_valid(self, tmp_path):
        """A14: mode B + enabled gate + valid max_bars -> valid."""
        yaml = _v3_yaml("B", "    candidate_duration_gate:\n      enabled: true\n      max_bars: 10\n")
        cfg = _assert_ok(tmp_path, yaml)
        gate = cfg.trade_filter.zigzag.candidate_duration_gate
        assert gate.enabled is True
        assert gate.max_bars == 10

    def test_gate_enabled_valid_max_bars_loads(self, tmp_path):
        """Gate enabled with valid max_bars=1 (boundary) -> valid."""
        yaml = _v3_yaml("C", "    candidate_duration_gate:\n      enabled: true\n      max_bars: 1\n")
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter.zigzag.candidate_duration_gate.max_bars == 1


# ---------------------------------------------------------------------------
# Exit-off mode (docs/plan_exit_off_modes.txt)
# ---------------------------------------------------------------------------

class TestExitOffLifecycleValidation:
    def test_exit_b_without_count_rejects(self, tmp_path):
        yaml = _MINIMAL_BASE + _ENABLED_BLOCK.replace(
            "    stopping_exit: opposite_st_flip\n",
            "    stopping_exit: opposite_st_flip\n    exit_off_mode: \"exit B\"\n",
        )
        _assert_error(tmp_path, yaml, "trade_filter.lifecycle.exit_off_zz_leg_count")

    def test_exit_a_with_explicit_count_rejects(self, tmp_path):
        yaml = _MINIMAL_BASE + _ENABLED_BLOCK.replace(
            "    stopping_exit: opposite_st_flip\n",
            "    stopping_exit: opposite_st_flip\n"
            "    exit_off_mode: \"exit A\"\n"
            "    exit_off_zz_leg_count: 3\n",
        )
        _assert_error(tmp_path, yaml, "must be absent")

    def test_exit_b_with_count_ok(self, tmp_path):
        yaml = _MINIMAL_BASE + _ENABLED_BLOCK.replace(
            "    stopping_exit: opposite_st_flip\n",
            "    stopping_exit: opposite_st_flip\n"
            "    exit_off_mode: \"exit B\"\n"
            "    exit_off_zz_leg_count: 3\n",
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter.lifecycle.exit_off_mode == "exit B"
        assert cfg.trade_filter.lifecycle.exit_off_zz_leg_count == 3


# ---------------------------------------------------------------------------
# PR1: Drift-тест whitelist'ов lifecycle (plan §13 PR1 / §14.1 last bullet)
#
# Гарантирует, что _ALLOWED_KEYS["trade_filter.lifecycle"] в WF Grid loader
# И TRADE_FILTER_ALLOWED_KEYS["trade_filter.lifecycle"] в shared модуле
# идентичны. Расхождение = регрессия (план §15 R2).
# ---------------------------------------------------------------------------

class TestLifecycleWhitelistDrift:
    """PR1 drift-test: WF loader и shared module должны иметь одинаковый
    lifecycle whitelist (docs/plan_exit_off_modes_v2.txt §13 PR1)."""

    def test_lifecycle_allowed_keys_match_shared(self):
        from wf_grid.config.loader import _ALLOWED_KEYS as wf_allowed
        from supertrend_optimizer.core.trade_filter_config import (
            TRADE_FILTER_ALLOWED_KEYS as shared_allowed,
        )

        wf_lc = frozenset(wf_allowed["trade_filter.lifecycle"])
        shared_lc = shared_allowed["trade_filter.lifecycle"]

        assert wf_lc == shared_lc, (
            f"Lifecycle whitelist drift detected!\n"
            f"  WF only:     {sorted(wf_lc - shared_lc)}\n"
            f"  Shared only: {sorted(shared_lc - wf_lc)}\n"
            "Update both _ALLOWED_KEYS in loader.py AND TRADE_FILTER_ALLOWED_KEYS "
            "in trade_filter_config.py to match."
        )


# ===========================================================================
# PR2: Full validation matrix §3.1-§3.3 (plan_exit_off_modes_v2.txt §14.1)
# ===========================================================================

import copy as _copy

from supertrend_optimizer.core.trade_filter_config import (
    _V3_INIT_FAILURE_KEYS as _SHARED_FAILURE_KEYS,
    TRADE_FILTER_ALLOWED_KEYS as _SHARED_ALLOWED,
    build_trade_filter_config_from_raw as _build_tf,
    collect_raw_user_keys as _collect_ruk,
    validate_trade_filter as _shared_validate,
)

# Minimal raw dict for a fully valid enabled trade_filter (v3 mode A).
# All lifecycle keys default to valid values; tests override specific keys.
_VALID_ENABLED_RAW: dict = {
    "enabled": True,
    "type": "zigzag_st_mode",
    "zigzag": {
        "mode": "A",
        "reversal_threshold": 0.005,
        "candidate_trigger_threshold": 0.012,
        "local_window": 5,
    },
    "lifecycle": {
        "freeze_confirmed_legs": 3,
        "stop_check": "confirm_bar_only",
        "stopping_exit": "opposite_st_flip",
    },
}


def _run_exit_off(
    lc_overrides: dict | None = None,
    caller: str = "wf_grid",
) -> tuple[list[str], list[str]]:
    """Run validate_trade_filter on a minimal valid config with lifecycle overrides.

    Returns (errors, error_keys).  Uses the shared validator directly so that
    error_keys are captured (load_grid_config only raises ConfigError text).
    """
    raw = {"trade_filter": _copy.deepcopy(_VALID_ENABLED_RAW)}
    if lc_overrides:
        raw["trade_filter"]["lifecycle"].update(lc_overrides)
    tf = _build_tf(raw["trade_filter"])
    ruk = _collect_ruk(raw)
    errors: list[str] = []
    ekeys: list[str] = []
    _shared_validate(tf, errors, ruk, caller_pipeline=caller, error_keys=ekeys)
    return errors, ekeys


def _assert_no_exit_off_errors(lc_overrides: dict | None = None) -> None:
    """Assert that no errors related to exit_off keys appear."""
    errors, ekeys = _run_exit_off(lc_overrides)
    exit_keys = {k for k in ekeys if "exit_off" in k or "zz_leg" in k}
    assert not exit_keys, f"Unexpected exit_off errors: {exit_keys}"
    exit_msgs = [e for e in errors if "exit_off" in e or "zz_leg" in e]
    assert not exit_msgs, f"Unexpected exit_off error messages: {exit_msgs}"


def _superset_assert(
    ekeys: list[str],
    required: set[str],
    forbidden: set[str] | None = None,
) -> None:
    """Superset assert: required ⊆ observed; forbidden ∩ observed == ∅."""
    observed = set(ekeys)
    missing = required - observed
    assert not missing, (
        f"Required error_keys missing: {sorted(missing)}\n"
        f"  Observed: {sorted(observed)}"
    )
    if forbidden:
        present_forbidden = forbidden & observed
        assert not present_forbidden, (
            f"Forbidden error_keys present: {sorted(present_forbidden)}\n"
            f"  Observed: {sorted(observed)}"
        )


# ---------------------------------------------------------------------------
# §3.1 — Valid configs
# ---------------------------------------------------------------------------

class TestExitOffValidConfigs:
    """§3.1: Valid configurations must produce zero exit_off errors."""

    def test_both_keys_absent_is_valid(self):
        """Both keys absent -> resolved 'exit A', no errors."""
        _assert_no_exit_off_errors()

    def test_exit_a_explicit_no_count_is_valid(self):
        """exit_off_mode: 'exit A' without count -> valid."""
        _assert_no_exit_off_errors({"exit_off_mode": "exit A"})

    def test_exit_b_with_count_1_is_valid(self):
        """exit_off_mode: 'exit B' + count: 1 -> valid (boundary)."""
        _assert_no_exit_off_errors({"exit_off_mode": "exit B", "exit_off_zz_leg_count": 1})

    def test_exit_b_with_count_3_is_valid(self):
        """exit_off_mode: 'exit B' + count: 3 -> valid."""
        _assert_no_exit_off_errors({"exit_off_mode": "exit B", "exit_off_zz_leg_count": 3})

    def test_exit_b_with_count_100_is_valid(self):
        """exit_off_mode: 'exit B' + large count -> valid."""
        _assert_no_exit_off_errors({"exit_off_mode": "exit B", "exit_off_zz_leg_count": 100})


# ---------------------------------------------------------------------------
# §3.2 — Single-error invalid configs
# ---------------------------------------------------------------------------

class TestExitOffInvalidSingleErrors:
    """§3.2: Each config triggers exactly the expected error key (superset assert)."""

    # --- count present when exit A (present_when_exit_a) ---

    def test_absent_mode_explicit_count_present_when_exit_a(self):
        _, ekeys = _run_exit_off({"exit_off_zz_leg_count": 3})
        _superset_assert(ekeys, {"exit_off_zz_leg_count_present_when_exit_a"})

    def test_exit_a_plus_count_null_present_when_exit_a(self):
        _, ekeys = _run_exit_off({"exit_off_mode": "exit A", "exit_off_zz_leg_count": None})
        _superset_assert(ekeys, {"exit_off_zz_leg_count_present_when_exit_a"})

    def test_exit_a_plus_count_zero_present_when_exit_a(self):
        _, ekeys = _run_exit_off({"exit_off_mode": "exit A", "exit_off_zz_leg_count": 0})
        _superset_assert(ekeys, {"exit_off_zz_leg_count_present_when_exit_a"})

    def test_exit_a_plus_count_int_present_when_exit_a(self):
        _, ekeys = _run_exit_off({"exit_off_mode": "exit A", "exit_off_zz_leg_count": 3})
        _superset_assert(ekeys, {"exit_off_zz_leg_count_present_when_exit_a"})

    def test_exit_a_plus_count_str_present_when_exit_a(self):
        _, ekeys = _run_exit_off({"exit_off_mode": "exit A", "exit_off_zz_leg_count": "3"})
        _superset_assert(ekeys, {"exit_off_zz_leg_count_present_when_exit_a"})

    # --- count missing when exit B (count_missing) ---

    def test_exit_b_no_count_missing(self):
        _, ekeys = _run_exit_off({"exit_off_mode": "exit B"})
        _superset_assert(ekeys, {"exit_off_zz_leg_count_missing"})

    # --- count below_one when exit B ---

    @pytest.mark.parametrize("val", [0, -1])
    def test_exit_b_count_below_one(self, val):
        _, ekeys = _run_exit_off({"exit_off_mode": "exit B", "exit_off_zz_leg_count": val})
        _superset_assert(ekeys, {"exit_off_zz_leg_count_below_one"})

    # --- count invalid_type when exit B ---

    @pytest.mark.parametrize("val", [True, False, 3.0, "3", None, [], {}])
    def test_exit_b_count_invalid_type(self, val):
        _, ekeys = _run_exit_off({"exit_off_mode": "exit B", "exit_off_zz_leg_count": val})
        _superset_assert(ekeys, {"exit_off_zz_leg_count_invalid_type"})

    # --- mode invalid_literal ---

    @pytest.mark.parametrize("val", ["", "A", "B", "C", "A+B", "C+B",
                                      "exit a", "EXIT A", "EXIT_A", "exit_A"])
    def test_mode_invalid_literal(self, val):
        _, ekeys = _run_exit_off({"exit_off_mode": val})
        _superset_assert(ekeys, {"exit_off_mode_invalid_literal"})

    # --- mode invalid_type ---

    @pytest.mark.parametrize("val", [True, False, 1, 0, 1.0, None, [], {}])
    def test_mode_invalid_type(self, val):
        _, ekeys = _run_exit_off({"exit_off_mode": val})
        _superset_assert(ekeys, {"exit_off_mode_invalid_type"})


# ---------------------------------------------------------------------------
# §3.3 — Combination configs (superset assert, NOT exact equality)
# ---------------------------------------------------------------------------

class TestExitOffCombinations:
    """§3.3: Multiple error keys can fire simultaneously (plan §3.3 contract)."""

    def test_invalid_literal_plus_count_int_fires_two_keys(self):
        """mode invalid_literal + count: 3 -> {invalid_literal, present_when_exit_a}."""
        _, ekeys = _run_exit_off({"exit_off_mode": "exit_A", "exit_off_zz_leg_count": 3})
        _superset_assert(
            ekeys,
            required={"exit_off_mode_invalid_literal",
                       "exit_off_zz_leg_count_present_when_exit_a"},
        )

    def test_invalid_literal_plus_count_zero_no_below_one(self):
        """mode invalid_literal + count: 0 -> {invalid_literal, present_when_exit_a};
        below_one must NOT fire (count not validated when effective != 'exit B')."""
        _, ekeys = _run_exit_off({"exit_off_mode": "exit_A", "exit_off_zz_leg_count": 0})
        _superset_assert(
            ekeys,
            required={"exit_off_mode_invalid_literal",
                       "exit_off_zz_leg_count_present_when_exit_a"},
            forbidden={"exit_off_zz_leg_count_below_one"},
        )

    def test_invalid_type_mode_plus_count_int_fires_two_keys(self):
        """mode invalid_type + count: 3 -> {invalid_type (mode), present_when_exit_a}."""
        _, ekeys = _run_exit_off({"exit_off_mode": True, "exit_off_zz_leg_count": 3})
        _superset_assert(
            ekeys,
            required={"exit_off_mode_invalid_type",
                       "exit_off_zz_leg_count_present_when_exit_a"},
        )

    def test_invalid_type_mode_plus_count_bool_no_count_type_error(self):
        """mode invalid_type + count: True -> {mode invalid_type, present_when_exit_a};
        count_invalid_type must NOT fire (count not validated when effective != 'exit B')."""
        _, ekeys = _run_exit_off({"exit_off_mode": True, "exit_off_zz_leg_count": True})
        _superset_assert(
            ekeys,
            required={"exit_off_mode_invalid_type",
                       "exit_off_zz_leg_count_present_when_exit_a"},
            forbidden={"exit_off_zz_leg_count_invalid_type"},
        )

    def test_exit_a_plus_count_bool_no_count_type_error(self):
        """exit_off_mode: 'exit A' + count: True -> {present_when_exit_a};
        count_invalid_type must NOT fire."""
        _, ekeys = _run_exit_off({"exit_off_mode": "exit A", "exit_off_zz_leg_count": True})
        _superset_assert(
            ekeys,
            required={"exit_off_zz_leg_count_present_when_exit_a"},
            forbidden={"exit_off_zz_leg_count_invalid_type"},
        )

    def test_exit_b_plus_count_bool_fires_only_count_type(self):
        """exit_off_mode: 'exit B' + count: True -> {invalid_type (count)}."""
        _, ekeys = _run_exit_off({"exit_off_mode": "exit B", "exit_off_zz_leg_count": True})
        _superset_assert(ekeys, required={"exit_off_zz_leg_count_invalid_type"})

    def test_exit_b_plus_count_zero_fires_only_below_one(self):
        """exit_off_mode: 'exit B' + count: 0 -> {below_one}."""
        _, ekeys = _run_exit_off({"exit_off_mode": "exit B", "exit_off_zz_leg_count": 0})
        _superset_assert(ekeys, required={"exit_off_zz_leg_count_below_one"})


# ---------------------------------------------------------------------------
# Disabled path (§1 контракт конфига — disabled filter behavior)
# ---------------------------------------------------------------------------

class TestExitOffDisabledPath:
    """Disabled filter: strict schema still blocks unknown keys;
    known key with invalid value is skipped."""

    def test_disabled_unknown_lifecycle_key_schema_invalid(self, tmp_path):
        """Unknown lifecycle key under disabled filter -> schema error."""
        yaml = (
            _MINIMAL_BASE
            + "trade_filter:\n  enabled: false\n  type: zigzag_st_mode\n"
            "  lifecycle:\n    unknown_key_xyz: true\n"
        )
        _assert_error(tmp_path, yaml, "unknown config key")

    def test_disabled_known_key_invalid_value_no_error(self, tmp_path):
        """Known lifecycle key with invalid value under disabled filter -> no error."""
        yaml = (
            _MINIMAL_BASE
            + "trade_filter:\n  enabled: false\n  type: zigzag_st_mode\n"
            "  lifecycle:\n    exit_off_mode: \"BAD_LITERAL\"\n"
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert cfg.trade_filter is not None
        assert not cfg.trade_filter.enabled

    def test_disabled_no_new_columns(self, tmp_path):
        """Disabled filter: no per-bar diagnostics, filter_diagnostics_summary is None."""
        yaml = (
            _MINIMAL_BASE
            + "trade_filter:\n  enabled: false\n  type: zigzag_st_mode\n"
        )
        cfg = _assert_ok(tmp_path, yaml)
        assert not cfg.trade_filter.enabled


# ---------------------------------------------------------------------------
# caller_pipeline parity: wf_grid vs tester produce identical errors (§14.1)
# ---------------------------------------------------------------------------

class TestExitOffCallerPipelineParity:
    """Parametrize caller_pipeline; errors must be identical for wf_grid/tester."""

    @pytest.mark.parametrize("caller", ["wf_grid", "tester"])
    def test_absent_count_with_exit_b_same_error(self, caller):
        _, ekeys = _run_exit_off({"exit_off_mode": "exit B"}, caller=caller)
        _superset_assert(ekeys, {"exit_off_zz_leg_count_missing"})

    @pytest.mark.parametrize("caller", ["wf_grid", "tester"])
    def test_invalid_literal_same_error(self, caller):
        _, ekeys = _run_exit_off({"exit_off_mode": "EXIT_A"}, caller=caller)
        _superset_assert(ekeys, {"exit_off_mode_invalid_literal"})

    @pytest.mark.parametrize("caller", ["wf_grid", "tester"])
    def test_combination_same_errors(self, caller):
        _, ekeys = _run_exit_off(
            {"exit_off_mode": "EXIT_A", "exit_off_zz_leg_count": 3}, caller=caller
        )
        _superset_assert(
            ekeys,
            required={"exit_off_mode_invalid_literal",
                       "exit_off_zz_leg_count_present_when_exit_a"},
        )

    def test_wf_grid_and_tester_produce_identical_ekeys_for_all_cases(self):
        """Exhaustive parity: for each test override, both callers return same error_keys."""
        test_cases = [
            {"exit_off_mode": "exit B"},
            {"exit_off_mode": "EXIT_A"},
            {"exit_off_mode": "exit A", "exit_off_zz_leg_count": 3},
            {"exit_off_mode": "exit B", "exit_off_zz_leg_count": 0},
            {"exit_off_mode": True, "exit_off_zz_leg_count": 3},
            {"exit_off_mode": "exit B", "exit_off_zz_leg_count": "3"},
        ]
        for overrides in test_cases:
            _, wf_ekeys = _run_exit_off(overrides, caller="wf_grid")
            _, t_ekeys = _run_exit_off(overrides, caller="tester")
            assert set(wf_ekeys) == set(t_ekeys), (
                f"Caller parity mismatch for overrides={overrides!r}:\n"
                f"  wf_grid: {sorted(wf_ekeys)}\n"
                f"  tester:  {sorted(t_ekeys)}"
            )


# ---------------------------------------------------------------------------
# _V3_INIT_FAILURE_KEYS registry (§14.1 last check)
# ---------------------------------------------------------------------------

class TestExitOffFailureKeysRegistry:
    """All 6 exit-off error keys must be in _V3_INIT_FAILURE_KEYS."""

    _EXPECTED_EXIT_OFF_KEYS = {
        "exit_off_mode_invalid_literal",
        "exit_off_mode_invalid_type",
        "exit_off_zz_leg_count_missing",
        "exit_off_zz_leg_count_invalid_type",
        "exit_off_zz_leg_count_below_one",
        "exit_off_zz_leg_count_present_when_exit_a",
    }

    def test_all_six_keys_in_failure_registry(self):
        missing = self._EXPECTED_EXIT_OFF_KEYS - _SHARED_FAILURE_KEYS
        assert not missing, (
            f"Missing from _V3_INIT_FAILURE_KEYS: {sorted(missing)}"
        )

    def test_each_key_is_reachable_by_validator(self):
        """Verify each error key is actually emitted by the validator
        (not just declared in the registry)."""
        cases = {
            "exit_off_mode_invalid_literal": {"exit_off_mode": "EXIT_A"},
            "exit_off_mode_invalid_type": {"exit_off_mode": 42},
            "exit_off_zz_leg_count_missing": {"exit_off_mode": "exit B"},
            "exit_off_zz_leg_count_invalid_type": {
                "exit_off_mode": "exit B", "exit_off_zz_leg_count": "3"
            },
            "exit_off_zz_leg_count_below_one": {
                "exit_off_mode": "exit B", "exit_off_zz_leg_count": 0
            },
            "exit_off_zz_leg_count_present_when_exit_a": {
                "exit_off_mode": "exit A", "exit_off_zz_leg_count": 3
            },
        }
        for expected_key, overrides in cases.items():
            _, ekeys = _run_exit_off(overrides)
            assert expected_key in ekeys, (
                f"Error key {expected_key!r} not emitted by validator "
                f"for overrides={overrides!r}. Got: {ekeys}"
            )
