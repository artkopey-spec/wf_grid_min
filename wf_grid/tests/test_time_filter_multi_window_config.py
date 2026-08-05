from __future__ import annotations

from types import SimpleNamespace
from textwrap import dedent

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.cli.tester import load_tester_config
from supertrend_optimizer.core.trade_filter_config import (
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    is_any_trade_subfilter_enabled,
    is_time_filter_enabled,
    resolve_time_filter_in_place,
    resolve_trade_filter_mode,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from wf_grid.config.loader import load_grid_config


def _build(raw_tf: dict):
    raw = {"trade_filter": raw_tf}
    keys = collect_raw_user_keys(raw)
    cfg = build_trade_filter_config_from_raw(raw_tf)
    resolve_zigzag_enabled_in_place(cfg, keys)
    resolve_volume_enabled_in_place(cfg, keys)
    errors: list[str] = []
    error_keys: list[str] = []
    validate_trade_filter(
        cfg,
        errors,
        keys,
        caller_pipeline="wf_grid",
        error_keys=error_keys,
    )
    if not errors:
        resolve_time_filter_in_place(cfg, keys)
    return cfg, errors, error_keys


def _time_only(*, windows=None, window=None) -> dict:
    time_filter = {"enabled": True}
    if windows is not None:
        time_filter["windows"] = windows
    if window is not None:
        time_filter["window"] = window
    return {
        "enabled": True,
        "zigzag": {"enabled": False},
        "volume": {"enabled": False},
        "time_filter": time_filter,
    }


@pytest.mark.parametrize(
    ("zigzag", "volume", "time_filter", "expected"),
    [
        (False, False, True, "time_only"),
        (False, True, False, "volume_only"),
        (False, True, True, "volume_and_time"),
        (True, False, False, "zigzag_only"),
        (True, False, True, "zigzag_and_time"),
        (True, True, False, "zigzag_and_volume"),
        (True, True, True, "zigzag_volume_and_time"),
    ],
)
def test_effective_mode_matrix(zigzag, volume, time_filter, expected):
    cfg = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(enabled=zigzag),
        volume=SimpleNamespace(enabled=volume),
        time_filter=SimpleNamespace(enabled=time_filter),
    )
    assert resolve_trade_filter_mode(cfg) == expected
    assert is_any_trade_subfilter_enabled(cfg) is True


def test_disabled_root_forces_disabled_mode():
    cfg = SimpleNamespace(
        enabled=False,
        zigzag=SimpleNamespace(enabled=True),
        volume=SimpleNamespace(enabled=True),
        time_filter=SimpleNamespace(enabled=True),
    )
    assert resolve_trade_filter_mode(cfg) == "disabled"
    assert is_time_filter_enabled(cfg) is False
    assert is_any_trade_subfilter_enabled(cfg) is False


def test_all_disabled_has_stable_error_key():
    _, errors, keys = _build(
        {
            "enabled": True,
            "zigzag": {"enabled": False},
            "volume": {"enabled": False},
            "time_filter": {"enabled": False},
        }
    )
    assert keys == ["trade_filter_all_subfilters_disabled"]
    assert errors == [
        "at least one trade subfilter must be enabled when "
        "trade_filter.enabled=true: zigzag, volume, or time_filter"
    ]


def test_windows_are_sorted_into_canonical_minutes():
    cfg, errors, keys = _build(
        _time_only(windows=["16:00-18:00", "09:00-12:00"])
    )
    assert errors == []
    assert keys == []
    assert cfg.time_filter._windows_minutes == ((540, 720), (960, 1080))
    assert cfg.time_filter._start_hour is None
    assert cfg.time_filter._end_hour is None


def test_overlap_message_uses_original_yaml_indexes():
    _, errors, keys = _build(
        _time_only(windows=["11:00-13:00", "09:00-12:00"])
    )
    assert keys == ["time_filter_windows_overlap"]
    assert errors == [
        "trade_filter.time_filter.windows[0] overlaps windows[1]"
    ]


def test_one_item_windows_matches_legacy_canonical_schedule():
    legacy, legacy_errors, _ = _build(_time_only(window="09:00-19:00"))
    modern, modern_errors, _ = _build(
        _time_only(windows=["09:00-19:00"])
    )
    assert legacy_errors == modern_errors == []
    assert legacy.time_filter._windows_minutes == ((540, 1140),)
    assert modern.time_filter._windows_minutes == ((540, 1140),)
    assert modern.time_filter._start_hour == 9
    assert modern.time_filter._end_hour == 19


@pytest.mark.parametrize(
    ("windows", "error_key"),
    [
        (None, "time_filter_windows_invalid_type"),
        ([], "time_filter_windows_empty"),
        ([1], "time_filter_windows_item_invalid_type"),
        (["9:00-12:00"], "time_filter_windows_item_invalid_format"),
        (["24:00-25:00"], "time_filter_windows_item_invalid_hours"),
        (["09:60-12:00"], "time_filter_windows_item_invalid_minutes"),
        (["09:00-09:00"], "time_filter_windows_item_zero_length"),
        (["18:00-09:00"], "time_filter_windows_item_cross_midnight"),
        (
            ["09:00-12:00", "09:00-12:00"],
            "time_filter_windows_duplicate",
        ),
        (
            ["09:00-12:00", "11:00-13:00"],
            "time_filter_windows_overlap",
        ),
        (
            ["09:00-12:00", "12:00-13:00"],
            "time_filter_windows_touching",
        ),
    ],
)
def test_windows_validation_keys(windows, error_key):
    raw = _time_only(windows=["09:00-12:00"])
    raw["time_filter"]["windows"] = windows
    _, _, keys = _build(raw)
    assert keys == [error_key]


def test_window_and_windows_conflict_is_presence_based_when_disabled():
    _, errors, keys = _build(
        {
            "enabled": False,
            "time_filter": {
                "enabled": False,
                "window": None,
                "windows": None,
            },
        }
    )
    assert keys == ["time_filter_window_and_windows_conflict"]
    assert len(errors) == 1


def test_active_time_without_window_keys_has_stable_missing_key():
    _, _, keys = _build(_time_only())
    assert keys == ["time_filter_windows_missing"]


@pytest.mark.parametrize("invalid", [1, "true", "yes", None])
def test_truthy_non_bool_time_enabled_is_rejected_under_disabled_root(invalid):
    _, _, keys = _build(
        {
            "enabled": False,
            "time_filter": {"enabled": invalid, "windows": "unfinished"},
        }
    )
    assert keys == ["time_filter_enabled_invalid_type"]


def test_disabled_time_skips_deep_windows_validation():
    _, errors, keys = _build(
        {
            "enabled": False,
            "time_filter": {"enabled": False, "windows": "unfinished"},
        }
    )
    assert errors == []
    assert keys == []


def test_time_only_allows_inert_zigzag_lifecycle_and_mode_d_templates():
    raw = _time_only(windows=["09:00-12:00"])
    raw["zigzag"].update(
        {
            "mode": "D",
            "reversal_threshold": "unfinished",
            "candidate_duration_gate": {"enabled": False},
        }
    )
    raw["lifecycle"] = {"freeze_confirmed_legs": "unfinished"}
    raw["wakeup_regime"] = {
        "enabled": False,
        "entry": {"direction_mode": "unfinished"},
    }
    _, errors, keys = _build(raw)
    assert errors == []
    assert keys == []


def test_explicit_zigzag_marker_contradicts_explicit_zigzag_false():
    raw = _time_only(windows=["09:00-12:00"])
    raw["type"] = "zigzag_st_mode"
    _, errors, _ = _build(raw)
    assert errors == [
        "trade_filter.type=zigzag_st_mode contradicts explicit "
        "trade_filter.zigzag.enabled=false"
    ]


def test_wakeup_enabled_requires_zigzag_owner():
    raw = _time_only(windows=["09:00-12:00"])
    raw["wakeup_regime"] = {"enabled": True}
    _, errors, _ = _build(raw)
    assert errors == [
        "trade_filter.wakeup_regime.enabled=true requires "
        "trade_filter.zigzag.enabled=true"
    ]


def test_time_only_runtime_dispatches_without_zigzag_or_volume():
    cfg, errors, _ = _build(_time_only(windows=["09:00-12:00"]))
    assert errors == []
    prices = np.array([100.0, 101.0, 102.0])
    result = run_backtest_fast(
        open_prices=prices,
        high=prices + 1.0,
        low=prices - 1.0,
        close=prices,
        atr_period=2,
        multiplier=2.0,
        trade_mode="both",
        commission=0.0,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=10,
        trade_filter_config=cfg,
        index=pd.DatetimeIndex(
            [
                "2026-03-06 09:00",
                "2026-03-06 09:01",
                "2026-03-06 09:02",
            ]
        ),
    )
    assert result.filter_diagnostics is not None
    assert "trade_filter_state" not in result.filter_diagnostics
    assert "volume_regime" not in result.filter_diagnostics


def test_wf_loader_accepts_multi_window_time_only(tmp_path):
    path = tmp_path / "grid.yaml"
    path.write_text(
        dedent(
            f"""
            data:
              file_path: "{(tmp_path / 'data.csv').as_posix()}"
            validation:
              walk_forward:
                train_size: "90D"
                test_size: "30D"
            trade_filter:
              enabled: true
              zigzag:
                enabled: false
              volume:
                enabled: false
              time_filter:
                enabled: true
                windows:
                  - "16:00-18:00"
                  - "09:00-12:00"
            """
        ),
        encoding="utf-8",
    )
    cfg = load_grid_config(str(path))
    assert cfg.trade_filter.time_filter._windows_minutes == (
        (540, 720),
        (960, 1080),
    )
    assert resolve_trade_filter_mode(cfg.trade_filter) == "time_only"


def test_tester_loader_accepts_multi_window_time_only(tmp_path):
    path = tmp_path / "tester.yaml"
    path.write_text(
        dedent(
            """
            supertrend:
              atr_period: 18
              multiplier: 1.5
            trade_mode: long
            commission: 0.0003
            warmup_period_auto: true
            periods_per_year: auto
            market: stocks
            segmentation:
              mode: legacy
              n_parts: 5
            trade_filter:
              enabled: true
              zigzag:
                enabled: false
              volume:
                enabled: false
              time_filter:
                enabled: true
                windows:
                  - "09:00-12:00"
                  - "16:00-18:00"
            """
        ),
        encoding="utf-8",
    )
    cfg = load_tester_config(str(path))
    trade_filter = cfg["trade_filter"]
    assert trade_filter.time_filter._windows_minutes == (
        (540, 720),
        (960, 1080),
    )
    assert resolve_trade_filter_mode(trade_filter) == "time_only"
