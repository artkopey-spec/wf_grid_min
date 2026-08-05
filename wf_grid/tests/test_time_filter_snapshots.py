from __future__ import annotations

from types import SimpleNamespace

from supertrend_optimizer.core.trade_filter_config import (
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    resolve_time_filter_in_place,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
    trade_filter_effective_snapshot,
    trade_filter_public_snapshot,
    validate_trade_filter,
)
from wf_grid.baseline.fingerprint import compute_pipeline_fingerprint


def _resolved_time_only(*, window=None, windows=None, inert_value=10):
    time_filter = {"enabled": True}
    if window is not None:
        time_filter["window"] = window
    if windows is not None:
        time_filter["windows"] = windows
    raw = {
        "enabled": True,
        "zigzag": {
            "enabled": False,
            "local_window": inert_value,
        },
        "volume": {
            "enabled": False,
            "short_window": inert_value,
        },
        "time_filter": time_filter,
    }
    keys = collect_raw_user_keys({"trade_filter": raw})
    config = build_trade_filter_config_from_raw(raw)
    resolve_zigzag_enabled_in_place(config, keys)
    resolve_volume_enabled_in_place(config, keys)
    errors: list[str] = []
    validate_trade_filter(
        config,
        errors,
        keys,
        caller_pipeline="wf_grid",
    )
    assert errors == []
    resolve_time_filter_in_place(config, keys)
    return config


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def test_public_snapshot_preserves_user_window_order_without_internal_fields():
    config = _resolved_time_only(
        windows=["16:00-18:00", "09:00-12:00"]
    )
    snapshot = trade_filter_public_snapshot(config)

    assert snapshot["trade_filter"]["time_filter"]["windows"] == [
        "16:00-18:00",
        "09:00-12:00",
    ]
    assert snapshot["trade_filter"]["time_filter"]["window"] is None
    assert all(not key.startswith("_") for key in _all_keys(snapshot))


def test_effective_snapshot_canonicalizes_order_and_legacy_single_window():
    first = trade_filter_effective_snapshot(
        _resolved_time_only(windows=["16:00-18:00", "09:00-12:00"])
    )
    second = trade_filter_effective_snapshot(
        _resolved_time_only(windows=["09:00-12:00", "16:00-18:00"])
    )
    assert first == second
    assert first == {
        "effective_mode": "time_only",
        "zigzag_active": False,
        "volume_active": False,
        "time_filter_active": True,
        "canonical_windows_minutes": [[540, 720], [960, 1080]],
        "time_reset_semantics": "calendar_cycle_close_reset",
    }

    legacy = trade_filter_effective_snapshot(
        _resolved_time_only(window="09:00-12:00")
    )
    modern = trade_filter_effective_snapshot(
        _resolved_time_only(windows=["09:00-12:00"])
    )
    assert legacy == modern


def test_inactive_templates_do_not_change_effective_snapshot_or_fingerprint():
    first = _resolved_time_only(
        windows=["09:00-12:00", "16:00-18:00"],
        inert_value=10,
    )
    second = _resolved_time_only(
        windows=["16:00-18:00", "09:00-12:00"],
        inert_value=999,
    )
    assert trade_filter_effective_snapshot(first) == trade_filter_effective_snapshot(
        second
    )

    result_a = SimpleNamespace(
        config=SimpleNamespace(trade_filter=first),
        diagnostics=None,
    )
    result_b = SimpleNamespace(
        config=SimpleNamespace(trade_filter=second),
        diagnostics=None,
    )
    fingerprint_a = compute_pipeline_fingerprint(result_a)
    fingerprint_b = compute_pipeline_fingerprint(result_b)
    assert (
        fingerprint_a.metadata["trade_filter_effective_snapshot"]
        == fingerprint_b.metadata["trade_filter_effective_snapshot"]
    )
    assert fingerprint_a == fingerprint_b
