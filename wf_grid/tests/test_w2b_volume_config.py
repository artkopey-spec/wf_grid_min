from pathlib import Path
from textwrap import dedent

import pytest

from supertrend_optimizer.core.trade_filter_config import (
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    collect_trade_filter_unknown_keys,
    is_volume_enabled,
    resolve_volume_defaults_in_place,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from wf_grid.config.loader import ConfigError, load_grid_config


def _base_filter(volume=None):
    raw = {
        "enabled": True,
        "type": "zigzag_st_mode",
        "zigzag": {
            "enabled": True,
            "reversal_threshold": 0.02,
            "candidate_trigger_threshold": 0.01,
        },
        "lifecycle": {},
    }
    if volume is not None:
        raw["volume"] = volume
    return raw


def _valid_volume(mode: str = "volume_A"):
    return {
        "enabled": True,
        "mode": mode,
        "short_window": 5,
        "baseline_window": 20,
        "threshold_ratio": 1.1,
    }


def _build_and_validate(raw_tf):
    raw = {"trade_filter": raw_tf}
    raw_user_keys = collect_raw_user_keys(raw)
    cfg = build_trade_filter_config_from_raw(raw_tf)
    resolve_zigzag_enabled_in_place(cfg, raw_user_keys)
    resolve_volume_enabled_in_place(cfg, raw_user_keys)
    errors: list[str] = []
    validate_trade_filter(cfg, errors, raw_user_keys)
    return cfg, errors, raw_user_keys


def test_valid_volume_a_config_passes_validation():
    cfg, errors, _keys = _build_and_validate(_base_filter(_valid_volume("volume_A")))

    assert errors == []
    assert is_volume_enabled(cfg) is True
    assert cfg.volume.mode == "volume_A"


def test_valid_volume_b_config_passes_validation_with_explicit_optional_values():
    volume = _valid_volume("volume_B")
    volume.update({
        "regime_low_ratio": 0.7,
        "regime_high_ratio": 1.4,
        "direction_lookback_bars": 4,
    })

    cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert errors == []
    assert is_volume_enabled(cfg) is True
    assert cfg.volume.mode == "volume_B"


def test_absent_volume_block_is_preserved_as_none_and_not_validated():
    cfg, errors, _keys = _build_and_validate(_base_filter())

    assert errors == []
    assert cfg.volume is None
    assert is_volume_enabled(cfg) is False


def test_volume_enabled_false_skips_deep_volume_validation():
    cfg, errors, _keys = _build_and_validate(_base_filter({
        "enabled": False,
        "mode": "not_a_volume_mode",
        "short_window": 0,
    }))

    assert errors == []
    assert is_volume_enabled(cfg) is False


def test_malformed_volume_enabled_is_rejected():
    _cfg, errors, _keys = _build_and_validate(_base_filter({
        "enabled": "true",
    }))

    assert errors == [
        "trade_filter.volume.enabled must be bool (true/false), "
        "got 'str' ('true')"
    ]


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"mode": "bad"}, "trade_filter.volume.mode must be 'volume_A' or 'volume_B'"),
        ({"short_window": 0}, "trade_filter.volume.short_window must be int >= 1"),
        ({"short_window": True}, "trade_filter.volume.short_window must be int >= 1"),
        ({"baseline_window": 4}, "trade_filter.volume.baseline_window must be >="),
        ({"threshold_ratio": 0}, "trade_filter.volume.threshold_ratio must be finite > 0"),
        ({"threshold_ratio": float("inf")}, "trade_filter.volume.threshold_ratio must be finite > 0"),
        ({"regime_low_ratio": 0}, "trade_filter.volume.regime_low_ratio must be finite > 0"),
        (
            {"regime_low_ratio": 1.1, "regime_high_ratio": 1.0},
            "trade_filter.volume.regime_high_ratio must be >",
        ),
        ({"direction_lookback_bars": 0}, "trade_filter.volume.direction_lookback_bars must be int >= 1"),
    ],
)
def test_invalid_volume_fields_are_rejected(patch, expected):
    volume = _valid_volume()
    volume.update(patch)

    _cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert any(expected in error for error in errors)


@pytest.mark.parametrize(
    "missing_key",
    ["mode", "short_window", "baseline_window", "threshold_ratio"],
)
def test_required_volume_fields_are_rejected_when_absent(missing_key):
    volume = _valid_volume()
    volume.pop(missing_key)

    _cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert any(f"trade_filter.volume.{missing_key} is required" in error for error in errors)


def test_unknown_key_under_volume_is_reported_by_strict_schema():
    errors = collect_trade_filter_unknown_keys({
        "enabled": True,
        "volume": {
            "enabled": True,
            "surprise": 1,
        },
    })

    assert errors == ["unknown config key: 'trade_filter.volume.surprise'"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {"enabled": True, "volume": True},
            "trade_filter.volume must be a YAML mapping",
        ),
        (
            {"enabled": True, "zigzag": False},
            "trade_filter.zigzag must be a YAML mapping",
        ),
        (
            {"enabled": True, "triggers": {"candidate_threshold": True}},
            "trade_filter.triggers.candidate_threshold must be a YAML mapping",
        ),
    ],
)
def test_non_mapping_trade_filter_blocks_are_reported_by_strict_schema(raw, expected):
    errors = collect_trade_filter_unknown_keys(raw)

    assert errors == [expected + ", got 'bool'"]


def test_wf_grid_loader_rejects_non_mapping_volume_block(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        dedent(
            """\
            data:
              file_path: data.csv
            validation:
              walk_forward:
                train_size: "90D"
                test_size: "30D"
            trade_filter:
              enabled: true
              volume: true
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="trade_filter.volume must be a YAML mapping"):
        load_grid_config(str(path))


def test_volume_defaults_materialize_only_after_validation():
    cfg, errors, raw_user_keys = _build_and_validate(_base_filter(_valid_volume()))

    assert errors == []
    assert cfg.volume.regime_low_ratio is None
    assert cfg.volume.regime_high_ratio is None
    assert cfg.volume.direction_lookback_bars is None

    resolve_volume_defaults_in_place(cfg, raw_user_keys)

    assert cfg.volume.regime_low_ratio == 0.8
    assert cfg.volume.regime_high_ratio == 1.2
    assert cfg.volume.direction_lookback_bars == 3


def test_wf_grid_loader_materializes_volume_defaults_in_phase5(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        dedent(
            """\
            data:
              file_path: data.csv
            validation:
              walk_forward:
                train_size: "90D"
                test_size: "30D"
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                enabled: true
                reversal_threshold: 0.005
                candidate_trigger_threshold: 0.012
                local_window: 5
              lifecycle:
                freeze_confirmed_legs: 3
                stop_check: confirm_bar_only
                stopping_exit: opposite_st_flip
              volume:
                enabled: true
                mode: volume_A
                short_window: 5
                baseline_window: 20
                threshold_ratio: 1.1
            """
        ),
        encoding="utf-8",
    )

    cfg = load_grid_config(str(path))

    assert cfg.trade_filter.volume.regime_low_ratio == 0.8
    assert cfg.trade_filter.volume.regime_high_ratio == 1.2
    assert cfg.trade_filter.volume.direction_lookback_bars == 3
