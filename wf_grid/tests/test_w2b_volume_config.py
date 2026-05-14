from pathlib import Path
from textwrap import dedent

import pytest

from supertrend_optimizer.core.trade_filter_config import (
    _V3_INIT_FAILURE_KEYS,
    TradeFilterBaselineSessionConfig,
    TradeFilterVolumeConfig,
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    collect_trade_filter_unknown_keys,
    is_volume_enabled,
    resolve_volume_defaults_in_place,
    resolve_volume_enabled_in_place,
    resolve_volume_baseline_session_in_place,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.cli.tester import load_tester_config
from wf_grid.config.loader import ConfigError, load_grid_config


def test_volume_config_defaults_include_aggregation_and_baseline_session():
    first = TradeFilterVolumeConfig()
    second = TradeFilterVolumeConfig()

    assert first.aggregation == "median"
    assert first.daily_reset is False
    assert first.cycle_direction_gate is False
    assert first.exit_hysteresis_ratio is None
    assert first.exit_freeze_bars is None
    assert isinstance(first.baseline_session, TradeFilterBaselineSessionConfig)
    assert first.baseline_session.enabled is False
    assert first.baseline_session.window is None
    assert first.baseline_session is not second.baseline_session


def test_build_trade_filter_config_materializes_volume_aggregation_and_baseline_session():
    volume = _valid_volume()
    volume.update({
        "aggregation": "mean",
        "daily_reset": True,
        "cycle_direction_gate": True,
        "exit_hysteresis_ratio": 1.8,
        "exit_freeze_bars": 10,
        "baseline_session": {
            "enabled": True,
            "window": "09:00-19:00",
        },
    })

    cfg = build_trade_filter_config_from_raw(_base_filter(volume))

    assert cfg.volume.aggregation == "mean"
    assert cfg.volume.daily_reset is True
    assert cfg.volume.cycle_direction_gate is True
    assert cfg.volume.exit_hysteresis_ratio == 1.8
    assert cfg.volume.exit_freeze_bars == 10
    assert cfg.volume.baseline_session.enabled is True
    assert cfg.volume.baseline_session.window == "09:00-19:00"


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
    assert cfg.volume.aggregation == "median"


def test_valid_standalone_volume_daily_reset_passes_without_zigzag_payload():
    raw = {
        "enabled": True,
        "zigzag": {"enabled": False},
        "volume": {
            **_valid_volume("volume_A"),
            "daily_reset": True,
        },
    }

    cfg, errors, _keys = _build_and_validate(raw)

    assert errors == []
    assert cfg.volume.daily_reset is True


def test_valid_standalone_volume_cycle_direction_gate_passes_without_zigzag_payload():
    raw = {
        "enabled": True,
        "zigzag": {"enabled": False},
        "volume": {
            **_valid_volume("volume_A"),
            "cycle_direction_gate": True,
        },
    }

    cfg, errors, _keys = _build_and_validate(raw)

    assert errors == []
    assert cfg.volume.cycle_direction_gate is True


def test_valid_volume_b_config_passes_validation_with_explicit_optional_values():
    volume = _valid_volume("volume_B")
    volume.update({
        "exit_hysteresis_ratio": 1.0,
        "exit_freeze_bars": 10,
        "regime_low_ratio": 0.7,
        "regime_high_ratio": 1.4,
        "direction_lookback_bars": 4,
    })

    cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert errors == []
    assert is_volume_enabled(cfg) is True
    assert cfg.volume.mode == "volume_B"
    assert cfg.volume.exit_hysteresis_ratio == 1.0
    assert cfg.volume.exit_freeze_bars == 10


@pytest.mark.parametrize("exit_freeze_bars", [0, 1, 10])
def test_valid_volume_exit_freeze_bars_values_pass_validation(exit_freeze_bars):
    volume = _valid_volume()
    volume["exit_hysteresis_ratio"] = 1.8
    volume["exit_freeze_bars"] = exit_freeze_bars

    cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert errors == []
    assert cfg.volume.exit_hysteresis_ratio == 1.8
    assert cfg.volume.exit_freeze_bars == exit_freeze_bars


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
        "exit_hysteresis_ratio": None,
        "exit_freeze_bars": -1,
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
        ({"exit_hysteresis_ratio": None}, "trade_filter.volume.exit_hysteresis_ratio must be finite > 0"),
        ({"exit_hysteresis_ratio": 0}, "trade_filter.volume.exit_hysteresis_ratio must be finite > 0"),
        ({"exit_hysteresis_ratio": -1}, "trade_filter.volume.exit_hysteresis_ratio must be finite > 0"),
        ({"exit_hysteresis_ratio": float("inf")}, "trade_filter.volume.exit_hysteresis_ratio must be finite > 0"),
        ({"exit_hysteresis_ratio": float("nan")}, "trade_filter.volume.exit_hysteresis_ratio must be finite > 0"),
        ({"exit_hysteresis_ratio": True}, "trade_filter.volume.exit_hysteresis_ratio must be finite > 0"),
        ({"exit_freeze_bars": None}, "trade_filter.volume.exit_freeze_bars must be int >= 0"),
        ({"exit_freeze_bars": -1}, "trade_filter.volume.exit_freeze_bars must be int >= 0"),
        ({"exit_freeze_bars": 1.0}, "trade_filter.volume.exit_freeze_bars must be int >= 0"),
        ({"exit_freeze_bars": 1.5}, "trade_filter.volume.exit_freeze_bars must be int >= 0"),
        ({"exit_freeze_bars": False}, "trade_filter.volume.exit_freeze_bars must be int >= 0"),
        ({"regime_low_ratio": 0}, "trade_filter.volume.regime_low_ratio must be finite > 0"),
        (
            {"regime_low_ratio": 1.1, "regime_high_ratio": 1.0},
            "trade_filter.volume.regime_high_ratio must be >",
        ),
        ({"direction_lookback_bars": 0}, "trade_filter.volume.direction_lookback_bars must be int >= 1"),
        ({"daily_reset": "true"}, "trade_filter.volume.daily_reset must be bool"),
        ({"cycle_direction_gate": "true"}, "trade_filter.volume.cycle_direction_gate must be bool"),
    ],
)
def test_invalid_volume_fields_are_rejected(patch, expected):
    volume = _valid_volume()
    volume.update(patch)

    _cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert any(expected in error for error in errors)


def test_volume_cycle_direction_gate_requires_standalone_volume_v1():
    volume = _valid_volume()
    volume["cycle_direction_gate"] = True

    cfg, _errors, raw_user_keys = _build_and_validate(_base_filter(volume))
    errors: list[str] = []
    error_keys: list[str] = []

    validate_trade_filter(cfg, errors, raw_user_keys, error_keys=error_keys)

    assert errors == [
        "trade_filter.volume.cycle_direction_gate=true requires "
        "trade_filter.zigzag.enabled=false in v1"
    ]
    assert error_keys == ["cycle_direction_gate_requires_volume_only"]
    assert "cycle_direction_gate_requires_volume_only" in _V3_INIT_FAILURE_KEYS


@pytest.mark.parametrize("aggregation", ["median", "mean"])
def test_volume_aggregation_valid_literals_pass_validation(aggregation):
    volume = _valid_volume()
    volume["aggregation"] = aggregation

    cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert errors == []
    assert cfg.volume.aggregation == aggregation


@pytest.mark.parametrize(
    ("aggregation", "expected"),
    [
        (None, "trade_filter.volume.aggregation must be 'median' or 'mean', got null"),
        ("ema", "trade_filter.volume.aggregation must be 'median' or 'mean'"),
        (123, "trade_filter.volume.aggregation must be 'median' or 'mean'"),
    ],
)
def test_invalid_volume_aggregation_is_rejected(aggregation, expected):
    volume = _valid_volume()
    volume["aggregation"] = aggregation

    _cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert any(expected in error for error in errors)


def test_volume_baseline_session_enabled_with_valid_window_passes_validation():
    volume = _valid_volume()
    volume["baseline_session"] = {
        "enabled": True,
        "window": "09:00-19:00",
    }

    cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert errors == []
    assert cfg.volume.baseline_session.enabled is True
    assert cfg.volume.baseline_session.window == "09:00-19:00"


def test_resolve_volume_baseline_session_materializes_window_fields():
    volume = _valid_volume()
    volume["baseline_session"] = {
        "enabled": True,
        "window": "09:00-19:00",
    }
    cfg, errors, raw_user_keys = _build_and_validate(_base_filter(volume))

    assert errors == []

    resolve_volume_baseline_session_in_place(cfg, raw_user_keys)

    assert cfg.volume.baseline_session._start_hour == 9
    assert cfg.volume.baseline_session._start_minute == 0
    assert cfg.volume.baseline_session._end_hour == 19
    assert cfg.volume.baseline_session._end_minute == 0


def test_volume_baseline_session_enabled_must_be_bool():
    volume = _valid_volume()
    volume["baseline_session"] = {
        "enabled": "true",
        "window": "09:00-19:00",
    }

    _cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert any(
        "trade_filter.volume.baseline_session.enabled must be bool" in error
        for error in errors
    )


def test_volume_baseline_session_enabled_true_requires_window():
    volume = _valid_volume()
    volume["baseline_session"] = {"enabled": True}

    _cfg, errors, _keys = _build_and_validate(_base_filter(volume))

    assert any(
        "trade_filter.volume.baseline_session.window is required" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        ("bad", "must be in HH:MM-HH:MM format"),
        ("09:00", "must be in HH:MM-HH:MM format"),
        ("25:00-26:00", "hours must be in 0-23"),
        ("09:60-10:00", "minutes must be in 0-59"),
        ("09:00-09:00", "must have non-zero length"),
        ("19:00-09:00", "wrap-around"),
    ],
)
def test_invalid_volume_baseline_session_windows_are_rejected(window, expected):
    volume = _valid_volume()
    volume["baseline_session"] = {
        "enabled": True,
        "window": window,
    }

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


def test_volume_aggregation_and_baseline_session_are_allowed_by_strict_schema():
    errors = collect_trade_filter_unknown_keys({
        "enabled": True,
        "volume": {
            "enabled": True,
            "aggregation": "mean",
            "daily_reset": True,
            "cycle_direction_gate": True,
            "exit_hysteresis_ratio": 1.8,
            "exit_freeze_bars": 10,
            "baseline_session": {
                "enabled": True,
                "window": "09:00-19:00",
            },
        },
    })

    assert errors == []


def test_unknown_key_under_volume_baseline_session_is_reported_by_strict_schema():
    errors = collect_trade_filter_unknown_keys({
        "enabled": True,
        "volume": {
            "enabled": True,
            "baseline_session": {
                "enabled": True,
                "window": "09:00-19:00",
                "surprise": 1,
            },
        },
    })

    assert errors == [
        "unknown config key: 'trade_filter.volume.baseline_session.surprise'"
    ]


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
        (
            {"enabled": True, "volume": {"baseline_session": True}},
            "trade_filter.volume.baseline_session must be a YAML mapping",
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


def test_wf_grid_loader_strict_schema_allows_volume_aggregation_and_baseline_session(tmp_path: Path):
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
              enabled: false
              volume:
                enabled: false
                aggregation: mean
                daily_reset: true
                cycle_direction_gate: true
                exit_hysteresis_ratio: 1.8
                exit_freeze_bars: 10
                baseline_session:
                  enabled: false
                  window: "09:00-19:00"
            """
        ),
        encoding="utf-8",
    )

    cfg = load_grid_config(str(path))

    assert cfg.trade_filter.volume.aggregation == "mean"
    assert cfg.trade_filter.volume.daily_reset is True
    assert cfg.trade_filter.volume.cycle_direction_gate is True
    assert cfg.trade_filter.volume.exit_hysteresis_ratio == 1.8
    assert cfg.trade_filter.volume.exit_freeze_bars == 10
    assert cfg.trade_filter.volume.baseline_session.window == "09:00-19:00"


def test_wf_grid_loader_resolves_volume_baseline_session_window_fields(tmp_path: Path):
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
                aggregation: mean
                short_window: 5
                baseline_window: 20
                threshold_ratio: 1.1
                baseline_session:
                  enabled: true
                  window: "09:00-19:00"
            """
        ),
        encoding="utf-8",
    )

    cfg = load_grid_config(str(path))

    baseline_session = cfg.trade_filter.volume.baseline_session
    assert baseline_session._start_hour == 9
    assert baseline_session._start_minute == 0
    assert baseline_session._end_hour == 19
    assert baseline_session._end_minute == 0


def test_repo_config_yaml_keeps_trade_filter_safe_default_disabled():
    cfg = load_grid_config("config.yaml")

    assert cfg.trade_filter.enabled is False
    assert cfg.trade_filter.volume.enabled is False
    assert cfg.trade_filter.volume.aggregation == "median"
    assert cfg.trade_filter.volume.cycle_direction_gate is False
    assert cfg.trade_filter.volume.baseline_session.enabled is False
    assert cfg.trade_filter.volume.baseline_session.window is None


def test_volume_defaults_materialize_only_after_validation():
    cfg, errors, raw_user_keys = _build_and_validate(_base_filter(_valid_volume()))

    assert errors == []
    assert cfg.volume.exit_hysteresis_ratio is None
    assert cfg.volume.exit_freeze_bars is None
    assert cfg.volume.regime_low_ratio is None
    assert cfg.volume.regime_high_ratio is None
    assert cfg.volume.direction_lookback_bars is None

    resolve_volume_defaults_in_place(cfg, raw_user_keys)

    assert cfg.volume.exit_hysteresis_ratio == 1.1
    assert cfg.volume.exit_freeze_bars == 0
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
    assert cfg.trade_filter.volume.exit_hysteresis_ratio == 1.1
    assert cfg.trade_filter.volume.exit_freeze_bars == 0


def test_wf_grid_and_tester_loaders_accept_same_volume_mapping(tmp_path: Path):
    volume = {
        "enabled": True,
        "daily_reset": True,
        "cycle_direction_gate": True,
        "mode": "volume_A",
        "aggregation": "mean",
        "short_window": 30,
        "baseline_window": 1000,
        "threshold_ratio": 2.2,
        "exit_hysteresis_ratio": 1.8,
        "exit_freeze_bars": 10,
        "regime_low_ratio": 0.8,
        "regime_high_ratio": 1.2,
        "direction_lookback_bars": 10,
        "baseline_session": {
            "enabled": True,
            "window": "09:00-19:00",
        },
    }
    wf_raw = {
        "data": {"file_path": "data.csv"},
        "validation": {
            "walk_forward": {
                "train_size": "90D",
                "test_size": "30D",
            },
        },
        "trade_filter": {
            "enabled": True,
            "zigzag": {"enabled": False},
            "volume": volume,
        },
    }
    tester_raw = {
        "segmentation": {"mode": "legacy"},
        "trade_filter": {
            "enabled": True,
            "zigzag": {"enabled": False},
            "volume": volume,
        },
    }
    wf_path = tmp_path / "wf.yaml"
    tester_path = tmp_path / "tester.yaml"
    import yaml

    wf_path.write_text(yaml.safe_dump(wf_raw, sort_keys=False), encoding="utf-8")
    tester_path.write_text(
        yaml.safe_dump(tester_raw, sort_keys=False), encoding="utf-8"
    )

    wf_cfg = load_grid_config(str(wf_path))
    tester_cfg = load_tester_config(str(tester_path))

    wf_volume = wf_cfg.trade_filter.volume
    tester_volume = tester_cfg["trade_filter"].volume
    assert wf_volume.threshold_ratio == tester_volume.threshold_ratio == 2.2
    assert wf_volume.exit_hysteresis_ratio == tester_volume.exit_hysteresis_ratio == 1.8
    assert wf_volume.exit_freeze_bars == tester_volume.exit_freeze_bars == 10
    assert wf_volume.aggregation == tester_volume.aggregation == "mean"
    assert wf_volume.daily_reset is tester_volume.daily_reset is True
    assert wf_volume.cycle_direction_gate is tester_volume.cycle_direction_gate is True
    assert wf_volume.baseline_session.enabled is tester_volume.baseline_session.enabled is True
    assert wf_volume.baseline_session.window == tester_volume.baseline_session.window == "09:00-19:00"
