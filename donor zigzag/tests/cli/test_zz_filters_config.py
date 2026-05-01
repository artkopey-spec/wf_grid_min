"""
Config-level tests for ZigZag filter validation
(plan v2.0.1 §2, §3.5.1–§3.5.3, §6.4).
"""
from __future__ import annotations

import warnings

import pytest

from supertrend_optimizer.cli.tester import (
    _validate_filters_config,
    _validate_zigzag_section,
)
from supertrend_optimizer.utils.exceptions import ConfigError


# ---------------------------------------------------------------------------
# _validate_zigzag_section — individual fields
# ---------------------------------------------------------------------------


class TestZigZagSectionDefaults:

    def test_empty_dict_uses_defaults(self):
        cfg = _validate_zigzag_section({})
        assert cfg["reversal_threshold"] == 0.005
        assert cfg["min_legs_global"] == 50
        assert cfg["q_strong"] == 0.80
        assert cfg["k_local"] == 5
        assert cfg["entry_side"] == "counter_trend"
        assert cfg["arm_timeout_bars_since_extreme"] == 24
        assert cfg["arm_timeout_bars_hard"] == 78

    def test_none_uses_defaults(self):
        cfg = _validate_zigzag_section(None)
        assert cfg["reversal_threshold"] == 0.005

    def test_non_dict_raises(self):
        with pytest.raises(ConfigError, match="must be a mapping"):
            _validate_zigzag_section(["not", "a", "dict"])


class TestReversalThreshold:

    @pytest.mark.parametrize("val", [0.0001, 0.005, 0.05, 0.1])
    def test_valid_bounds(self, val):
        cfg = _validate_zigzag_section({"reversal_threshold": val})
        assert cfg["reversal_threshold"] == pytest.approx(val)

    @pytest.mark.parametrize("val", [0.00001, 0.0, -0.01, 0.11, 1.0])
    def test_out_of_range_raises(self, val):
        with pytest.raises(ConfigError, match="reversal_threshold"):
            _validate_zigzag_section({"reversal_threshold": val})

    @pytest.mark.parametrize("val", ["0.005", True, None, [0.005]])
    def test_wrong_type_raises(self, val):
        with pytest.raises(ConfigError, match="reversal_threshold"):
            _validate_zigzag_section({"reversal_threshold": val})


class TestMinLegsGlobal:

    @pytest.mark.parametrize("val", [10, 50, 500])
    def test_valid_bounds(self, val):
        cfg = _validate_zigzag_section({"min_legs_global": val})
        assert cfg["min_legs_global"] == val

    @pytest.mark.parametrize("val", [9, 501, -5, 0])
    def test_out_of_range_raises(self, val):
        with pytest.raises(ConfigError, match="min_legs_global"):
            _validate_zigzag_section({"min_legs_global": val})

    @pytest.mark.parametrize("val", [50.0, "50", True, None])
    def test_wrong_type_raises(self, val):
        with pytest.raises(ConfigError, match="min_legs_global"):
            _validate_zigzag_section({"min_legs_global": val})


class TestQStrong:

    @pytest.mark.parametrize("val", [0.01, 0.5, 0.80, 0.99])
    def test_valid_bounds(self, val):
        cfg = _validate_zigzag_section({"q_strong": val})
        assert cfg["q_strong"] == pytest.approx(val)

    @pytest.mark.parametrize("val", [0.0, 1.0, -0.1, 1.1])
    def test_out_of_range_raises(self, val):
        with pytest.raises(ConfigError, match="q_strong"):
            _validate_zigzag_section({"q_strong": val})


class TestKLocal:

    @pytest.mark.parametrize("val", [2, 5, 50])
    def test_valid_bounds(self, val):
        cfg = _validate_zigzag_section({"k_local": val})
        assert cfg["k_local"] == val

    @pytest.mark.parametrize("val", [1, 51, 0, -3])
    def test_out_of_range_raises(self, val):
        with pytest.raises(ConfigError, match="k_local"):
            _validate_zigzag_section({"k_local": val})


class TestEntrySide:

    def test_counter_trend_ok(self):
        cfg = _validate_zigzag_section({"entry_side": "counter_trend"})
        assert cfg["entry_side"] == "counter_trend"

    @pytest.mark.parametrize("val", ["pullback", "trend", "long_only", ""])
    def test_other_values_raise(self, val):
        with pytest.raises(ConfigError, match="entry_side"):
            _validate_zigzag_section({"entry_side": val})

    def test_non_string_raises(self):
        with pytest.raises(ConfigError, match="entry_side"):
            _validate_zigzag_section({"entry_side": 1})


class TestArmTimeouts:

    def test_valid(self):
        cfg = _validate_zigzag_section({
            "arm_timeout_bars_since_extreme": 24,
            "arm_timeout_bars_hard": 78,
        })
        assert cfg["arm_timeout_bars_since_extreme"] == 24
        assert cfg["arm_timeout_bars_hard"] == 78

    def test_hard_less_than_since_extreme_raises(self):
        # arm_timeout_bars_hard < arm_timeout_bars_since_extreme → ConfigError
        with pytest.raises(ConfigError, match="arm_timeout_bars_hard.*must be >="):
            _validate_zigzag_section({
                "arm_timeout_bars_since_extreme": 100,
                "arm_timeout_bars_hard": 50,
            })

    def test_hard_equals_since_extreme_ok(self):
        cfg = _validate_zigzag_section({
            "arm_timeout_bars_since_extreme": 24,
            "arm_timeout_bars_hard": 24,
        })
        assert cfg["arm_timeout_bars_hard"] == 24

    @pytest.mark.parametrize("field", ["arm_timeout_bars_since_extreme",
                                         "arm_timeout_bars_hard"])
    @pytest.mark.parametrize("val", [0, -1, 10001])
    def test_out_of_range(self, field, val):
        base = {"arm_timeout_bars_since_extreme": 24, "arm_timeout_bars_hard": 78}
        base[field] = val
        with pytest.raises(ConfigError, match=field):
            _validate_zigzag_section(base)


# ---------------------------------------------------------------------------
# _validate_filters_config — full-config integration for zigzag mode
# ---------------------------------------------------------------------------


class TestFiltersConfigZigZagMode:

    def test_zigzag_mode_with_zz_section(self):
        raw = {
            "mode": "zigzag",
            "zigzag": {"reversal_threshold": 0.003, "min_legs_global": 100},
        }
        cfg = _validate_filters_config(raw)
        assert cfg["mode"] == "zigzag"
        assert cfg["zigzag"]["reversal_threshold"] == 0.003
        assert cfg["zigzag"]["min_legs_global"] == 100
        # defaults fill the rest
        assert cfg["zigzag"]["k_local"] == 5

    def test_zigzag_mode_no_section_uses_defaults(self):
        raw = {"mode": "zigzag"}
        cfg = _validate_filters_config(raw)
        assert cfg["zigzag"]["reversal_threshold"] == 0.005

    def test_zigzag_and_volume_requires_volume_ma_column(self):
        raw = {
            "mode": "zigzag_and_volume",
            "zigzag": {},
            "volume": {"volume_ma_column": "   "},   # whitespace → invalid
        }
        with pytest.raises(ConfigError, match="volume_ma_column is required"):
            _validate_filters_config(raw)

    def test_zigzag_and_volume_ok_with_volume_ma_column(self):
        raw = {
            "mode": "zigzag_and_volume",
            "zigzag": {"reversal_threshold": 0.005},
            "volume": {"volume_ma_column": "Volume MA", "min_ratio": 2.0},
        }
        cfg = _validate_filters_config(raw)
        assert cfg["mode"] == "zigzag_and_volume"
        assert cfg["volume"]["volume_ma_column"] == "Volume MA"

    def test_non_zz_mode_uses_default_zigzag_section(self):
        # Any non-zz mode still has `zigzag` key with defaults.
        raw = {"mode": "none"}
        cfg = _validate_filters_config(raw)
        assert "zigzag" in cfg
        assert cfg["zigzag"]["reversal_threshold"] == 0.005


class TestFilterSectionConflicts:

    def test_mode_volume_forbids_zigzag_section(self):
        raw = {
            "mode": "volume",
            "zigzag": {"reversal_threshold": 0.005},
            "volume": {"volume_ma_column": "Volume MA"},
        }
        with pytest.raises(ConfigError, match="filters.zigzag.*forbidden.*volume"):
            _validate_filters_config(raw)

    def test_mode_zigzag_with_amplitude_section_warns(self):
        raw = {
            "mode": "zigzag",
            "zigzag": {},
            "amplitude": {"n": 20},
        }
        with pytest.warns(UserWarning, match="amplitude section is present"):
            cfg = _validate_filters_config(raw)
        assert cfg["mode"] == "zigzag"

    def test_mode_amplitude_with_zigzag_section_warns(self):
        raw = {
            "mode": "amplitude",
            "amplitude": {"n": 20},
            "zigzag": {"reversal_threshold": 0.005},
        }
        with pytest.warns(UserWarning, match="zigzag section is present"):
            cfg = _validate_filters_config(raw)
        assert cfg["mode"] == "amplitude"

    def test_none_mode_allows_both_sections(self):
        raw = {
            "mode": "none",
            "amplitude": {"n": 20},
            "zigzag": {"reversal_threshold": 0.005},
        }
        # Neither error nor warning expected for mode=none
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            cfg = _validate_filters_config(raw)
        assert cfg["mode"] == "none"


# ---------------------------------------------------------------------------
# RFC v3.1 Phase 2 — config schema (§6.3, §6.4, §6.5, §6.6)
# ---------------------------------------------------------------------------


class TestStructuralResetMinSpan:
    """TP-30c: structural_reset_min_span validator (RFC v3.1 §6.6)."""

    def test_default_is_3(self):
        cfg = _validate_zigzag_section({})
        assert cfg["structural_reset_min_span"] == 3

    def test_valid_boundary_1(self):
        cfg = _validate_zigzag_section({"structural_reset_min_span": 1})
        assert cfg["structural_reset_min_span"] == 1

    def test_valid_boundary_1000(self):
        cfg = _validate_zigzag_section({"structural_reset_min_span": 1000})
        assert cfg["structural_reset_min_span"] == 1000

    def test_zero_fails(self):
        with pytest.raises(ConfigError, match="structural_reset_min_span"):
            _validate_zigzag_section({"structural_reset_min_span": 0})

    def test_1001_fails(self):
        with pytest.raises(ConfigError, match="structural_reset_min_span"):
            _validate_zigzag_section({"structural_reset_min_span": 1001})

    def test_negative_fails(self):
        with pytest.raises(ConfigError, match="structural_reset_min_span"):
            _validate_zigzag_section({"structural_reset_min_span": -1})

    def test_float_fails(self):
        with pytest.raises(ConfigError, match="structural_reset_min_span"):
            _validate_zigzag_section({"structural_reset_min_span": 3.0})

    def test_bool_fails(self):
        with pytest.raises(ConfigError, match="structural_reset_min_span"):
            _validate_zigzag_section({"structural_reset_min_span": True})


class TestReadinessDefaults:
    """TP-21 (partial): when no readiness block, defaults apply (RFC v3.1 §6.4 r.1, §6.5)."""

    def test_no_readiness_block_gives_default_structure(self):
        cfg = _validate_zigzag_section({"reversal_threshold": 0.005})
        r = cfg["readiness"]
        assert r["contour_a"]["enabled"] is True
        assert r["contour_a"]["p80_quantile"] == pytest.approx(0.80)
        assert r["contour_b"]["enabled"] is False
        assert r["contour_b"]["local_k"] == 5
        assert r["contour_b"]["open_ratio"] == pytest.approx(1.5)
        assert r["contour_b"]["close_ratio"] == pytest.approx(1.0)

    def test_no_readiness_and_no_q_strong_no_warning(self):
        # No q_strong explicitly set → no DeprecationWarning.
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            cfg = _validate_zigzag_section({})
        assert cfg["readiness"]["contour_a"]["p80_quantile"] == pytest.approx(0.80)

    def test_migration_q_strong_used_as_p80_quantile(self):
        # TP-21: q_strong present, no readiness → p80=q_strong, deprecation warn.
        with pytest.warns(DeprecationWarning, match="q_strong is deprecated"):
            cfg = _validate_zigzag_section({"q_strong": 0.75})
        assert cfg["readiness"]["contour_a"]["p80_quantile"] == pytest.approx(0.75)

    def test_migration_filters_config_no_readiness(self):
        # TP-21 full: _validate_filters_config without readiness block.
        raw = {"mode": "zigzag", "zigzag": {"reversal_threshold": 0.005}}
        cfg = _validate_filters_config(raw)
        r = cfg["zigzag"]["readiness"]
        assert r["contour_a"]["enabled"] is True
        assert r["contour_a"]["p80_quantile"] == pytest.approx(0.80)
        assert r["contour_b"]["enabled"] is False


class TestMigrationAmbiguity:
    """TP-22: q_strong + p80_quantile conflict → ConfigError (RFC v3.1 §6.4 r.3)."""

    def test_both_set_and_differ_fails(self):
        raw = {
            "q_strong": 0.80,
            "readiness": {
                "contour_a": {"p80_quantile": 0.75},
            },
        }
        with pytest.raises(ConfigError, match="q_strong.*p80_quantile|p80_quantile.*q_strong"):
            _validate_zigzag_section(raw)

    def test_both_set_and_same_ok(self):
        # Same value → no conflict; p80_quantile explicit so no DeprecationWarning
        # (DeprecationWarning only fires when p80_quantile is ABSENT, §6.4 r.2).
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            cfg = _validate_zigzag_section({
                "q_strong": 0.80,
                "readiness": {
                    "contour_a": {"p80_quantile": 0.80},
                },
            })
        assert cfg["readiness"]["contour_a"]["p80_quantile"] == pytest.approx(0.80)

    def test_p80_quantile_only_no_conflict(self):
        # p80_quantile set, no q_strong → ok, no warning.
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            cfg = _validate_zigzag_section({
                "readiness": {
                    "contour_a": {"p80_quantile": 0.70},
                },
            })
        assert cfg["readiness"]["contour_a"]["p80_quantile"] == pytest.approx(0.70)


class TestStrictSchemaUnknownKeys:
    """TP-30a: unknown keys at all levels → ConfigError (RFC v3.1 §6.4 r.7)."""

    def test_unknown_key_at_zigzag_level(self):
        with pytest.raises(ConfigError, match="unknown key"):
            _validate_zigzag_section({"reversal_threshold": 0.005, "foo_bar": 123})

    def test_unknown_key_at_readiness_level(self):
        with pytest.raises(ConfigError, match="unknown key"):
            _validate_zigzag_section({
                "readiness": {"contour_a": {}, "some_extra_key": True},
            })

    def test_unknown_key_at_contour_a_level(self):
        with pytest.raises(ConfigError, match="unknown key"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_a": {"enabled": True, "min_legs_global": 100},
                },
            })

    def test_unknown_key_at_contour_b_level(self):
        with pytest.raises(ConfigError, match="unknown key"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_b": {"enabled": False, "grace_bars": 3},
                },
            })


class TestReadinessContourBValidation:
    """TP-30b, TP-30d: contour_b field validation (RFC v3.1 §6.6)."""

    def test_close_ratio_negative_fails(self):
        # TP-30b
        with pytest.raises(ConfigError, match="close_ratio"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_b": {"enabled": True, "close_ratio": -0.5},
                },
            })

    def test_open_ratio_zero_allowed(self):
        # TP-30d boundary: open_ratio=0.0 is valid (needed for §6.7 legacy_parity)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cfg = _validate_zigzag_section({
                "readiness": {
                    "contour_b": {
                        "enabled": True,
                        "open_ratio": 0.0,
                        "close_ratio": 0.0,
                    },
                },
            })
        assert cfg["readiness"]["contour_b"]["open_ratio"] == pytest.approx(0.0)

    def test_open_ratio_negative_fails(self):
        # TP-30d: open_ratio < 0 → fail
        with pytest.raises(ConfigError, match="open_ratio"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_b": {"enabled": True, "open_ratio": -0.1},
                },
            })

    def test_open_ratio_less_than_close_ratio_fails(self):
        # open_ratio < close_ratio → fail (§6.6)
        with pytest.raises(ConfigError, match="open_ratio.*close_ratio|close_ratio"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_b": {
                        "enabled": True,
                        "open_ratio": 0.5,
                        "close_ratio": 1.0,
                    },
                },
            })

    def test_local_k_zero_fails(self):
        with pytest.raises(ConfigError, match="local_k"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_b": {"enabled": True, "local_k": 0},
                },
            })

    def test_local_k_one_ok(self):
        cfg = _validate_zigzag_section({
            "readiness": {
                "contour_b": {"enabled": True, "local_k": 1, "open_ratio": 1.5},
            },
        })
        assert cfg["readiness"]["contour_b"]["local_k"] == 1


class TestReadinessContourAValidation:
    """Contour A field validation (RFC v3.1 §6.6)."""

    def test_p80_quantile_zero_fails(self):
        with pytest.raises(ConfigError, match="p80_quantile"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_a": {"p80_quantile": 0.0},
                },
            })

    def test_p80_quantile_one_fails(self):
        with pytest.raises(ConfigError, match="p80_quantile"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_a": {"p80_quantile": 1.0},
                },
            })

    def test_p80_quantile_valid(self):
        cfg = _validate_zigzag_section({
            "readiness": {
                "contour_a": {"p80_quantile": 0.70},
            },
        })
        assert cfg["readiness"]["contour_a"]["p80_quantile"] == pytest.approx(0.70)

    def test_enabled_false_ok(self):
        cfg = _validate_zigzag_section({
            "readiness": {
                "contour_a": {"enabled": False},
            },
        })
        assert cfg["readiness"]["contour_a"]["enabled"] is False


class TestBothContoursDisabledWarn:
    """A=off, B=off → UserWarning in tester context (RFC v3.1 §6.6)."""

    def test_both_disabled_warns(self):
        with pytest.warns(UserWarning, match="both contour_a and contour_b are disabled"):
            _validate_zigzag_section({
                "readiness": {
                    "contour_a": {"enabled": False},
                    "contour_b": {"enabled": False},
                },
            })

    def test_only_a_disabled_no_warn(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            cfg = _validate_zigzag_section({
                "readiness": {
                    "contour_a": {"enabled": False},
                    "contour_b": {"enabled": True, "open_ratio": 1.5},
                },
            })
        assert cfg["readiness"]["contour_a"]["enabled"] is False


class TestLegacyParityPreset:
    """TP-24 (partial): legacy_parity config preset (RFC v3.1 §6.7)."""

    def test_legacy_parity_preset_valid(self):
        # A=off, B=on, open_ratio=0.0, close_ratio=0.0 → valid (§6.7).
        # No UserWarning expected: only A=off AND B=off triggers warning.
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            cfg = _validate_zigzag_section({
                "readiness": {
                    "contour_a": {"enabled": False},
                    "contour_b": {
                        "enabled": True,
                        "local_k": 5,
                        "open_ratio": 0.0,
                        "close_ratio": 0.0,
                    },
                },
            })
        assert cfg["readiness"]["contour_a"]["enabled"] is False
        assert cfg["readiness"]["contour_b"]["enabled"] is True
        assert cfg["readiness"]["contour_b"]["open_ratio"] == pytest.approx(0.0)
        assert cfg["readiness"]["contour_b"]["close_ratio"] == pytest.approx(0.0)
