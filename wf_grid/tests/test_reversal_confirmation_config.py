from __future__ import annotations

import itertools

import pytest

from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    TradeFilterReversalConfirmationConfig,
    TradeFilterTimeFilterConfig,
    TradeFilterVolumeConfig,
    TradeFilterZigZagConfig,
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    resolve_active_trade_filter_capabilities,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.utils.exceptions import ConfigError


def _config(z: bool, v: bool, t: bool, r: bool) -> TradeFilterConfig:
    return TradeFilterConfig(
        enabled=True,
        zigzag=TradeFilterZigZagConfig(enabled=z),
        volume=TradeFilterVolumeConfig(enabled=v),
        time_filter=TradeFilterTimeFilterConfig(enabled=t),
        reversal_confirmation=TradeFilterReversalConfirmationConfig(
            enabled=r,
            confirm_bars=2 if r else None,
        ),
    )


def test_capability_truth_table_all_16_combinations() -> None:
    for z, v, t, r in itertools.product((False, True), repeat=4):
        config = _config(z, v, t, r)
        if r and (z or v):
            with pytest.raises(
                ConfigError, match="reversal_confirmation_conflicting_capability"
            ):
                resolve_active_trade_filter_capabilities(config)
        elif not any((z, v, t, r)):
            with pytest.raises(ValueError, match="at least one"):
                resolve_active_trade_filter_capabilities(config)
        else:
            assert resolve_active_trade_filter_capabilities(config).effective_mode


@pytest.mark.parametrize(
    ("enabled", "confirm_bars", "expected_key"),
    [
        (True, None, "reversal_confirmation_confirm_bars_missing"),
        (True, True, "reversal_confirmation_confirm_bars_invalid_type"),
        (True, 0, "reversal_confirmation_confirm_bars_out_of_range"),
        (False, 2, "reversal_confirmation_confirm_bars_present_when_disabled"),
    ],
)
def test_reversal_validation_stable_keys(
    enabled: bool,
    confirm_bars: object,
    expected_key: str,
) -> None:
    config = _config(False, False, False, enabled)
    assert config.reversal_confirmation is not None
    config.reversal_confirmation.confirm_bars = confirm_bars
    raw_keys = {
        ("trade_filter",),
        ("trade_filter", "enabled"),
        ("trade_filter", "reversal_confirmation"),
        ("trade_filter", "reversal_confirmation", "enabled"),
    }
    if confirm_bars is not None:
        raw_keys.add(
            ("trade_filter", "reversal_confirmation", "confirm_bars")
        )
    errors: list[str] = []
    error_keys: list[str] = []
    validate_trade_filter(
        config,
        errors,
        frozenset(raw_keys),
        caller_pipeline="single",
        error_keys=error_keys,
    )
    assert expected_key in error_keys


def test_wf_grid_recognizes_reversal_but_returns_stable_unsupported_key() -> None:
    config = _config(False, False, False, True)
    errors: list[str] = []
    error_keys: list[str] = []
    validate_trade_filter(
        config,
        errors,
        frozenset(
            {
                ("trade_filter",),
                ("trade_filter", "enabled"),
                ("trade_filter", "reversal_confirmation"),
                ("trade_filter", "reversal_confirmation", "enabled"),
                ("trade_filter", "reversal_confirmation", "confirm_bars"),
            }
        ),
        caller_pipeline="wf_grid",
        error_keys=error_keys,
    )
    assert "reversal_confirmation_unsupported_pipeline" in error_keys
    assert "reversal_confirmation_unsupported_pipeline" in errors


@pytest.mark.parametrize("caller_pipeline", ["single", "tester", "wf_grid"])
def test_reversal_rejects_wakeup_before_runtime(caller_pipeline: str) -> None:
    raw = {
        "enabled": True,
        "zigzag": {"enabled": False},
        "volume": {"enabled": False},
        "time_filter": {"enabled": False},
        "reversal_confirmation": {"enabled": True, "confirm_bars": 2},
        "wakeup_regime": {"enabled": True},
    }
    raw_keys = collect_raw_user_keys({"trade_filter": raw})
    config = build_trade_filter_config_from_raw(raw)
    resolve_zigzag_enabled_in_place(config, raw_keys)
    resolve_volume_enabled_in_place(config, raw_keys)
    errors: list[str] = []
    error_keys: list[str] = []
    validate_trade_filter(
        config,
        errors,
        raw_keys,
        caller_pipeline=caller_pipeline,
        error_keys=error_keys,
    )
    assert "reversal_confirmation_wakeup_unsupported" in error_keys
    assert config._reversal_validated_for is None


def test_reversal_rejects_position_freeze_before_runtime() -> None:
    raw = {
        "enabled": True,
        "zigzag": {"enabled": False},
        "volume": {"enabled": False},
        "time_filter": {"enabled": False},
        "reversal_confirmation": {"enabled": True, "confirm_bars": 2},
        "wakeup_regime": {
            "enabled": False,
            "position_freeze": {"enabled": True},
        },
    }
    raw_keys = collect_raw_user_keys({"trade_filter": raw})
    config = build_trade_filter_config_from_raw(raw)
    resolve_zigzag_enabled_in_place(config, raw_keys)
    resolve_volume_enabled_in_place(config, raw_keys)
    errors: list[str] = []
    error_keys: list[str] = []
    validate_trade_filter(
        config,
        errors,
        raw_keys,
        caller_pipeline="single",
        error_keys=error_keys,
    )
    assert "reversal_confirmation_position_freeze_unsupported" in error_keys
    assert config._reversal_validated_for is None
