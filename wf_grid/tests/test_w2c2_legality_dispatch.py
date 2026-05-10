import numpy as np

from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.core.trade_filter_config import (
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    is_volume_enabled,
    is_zigzag_enabled,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.core.zigzag_st_filter import ZigZagGlobalStats


def _valid_zigzag():
    return {
        "enabled": True,
        "reversal_threshold": 0.02,
        "candidate_trigger_threshold": 0.01,
    }


def _valid_volume():
    return {
        "enabled": True,
        "mode": "volume_A",
        "short_window": 5,
        "baseline_window": 20,
        "threshold_ratio": 1.1,
    }


def _build(raw_tf):
    raw = {"trade_filter": raw_tf}
    keys = collect_raw_user_keys(raw)
    cfg = build_trade_filter_config_from_raw(raw_tf)
    resolve_zigzag_enabled_in_place(cfg, keys)
    resolve_volume_enabled_in_place(cfg, keys)
    errors: list[str] = []
    validate_trade_filter(cfg, errors, keys)
    return cfg, errors


def _base_zigzag_only():
    return {
        "enabled": True,
        "type": "zigzag_st_mode",
        "zigzag": _valid_zigzag(),
        "lifecycle": {},
    }


def test_standalone_volume_without_zigzag_block_is_valid():
    cfg, errors = _build({
        "enabled": True,
        "volume": _valid_volume(),
    })

    assert errors == []
    assert is_zigzag_enabled(cfg) is False
    assert is_volume_enabled(cfg) is True


def test_standalone_volume_with_zigzag_type_is_invalid():
    cfg, errors = _build({
        "enabled": True,
        "type": "zigzag_st_mode",
        "zigzag": {"enabled": False},
        "volume": _valid_volume(),
    })

    assert is_zigzag_enabled(cfg) is False
    assert any("remove trade_filter.type" in error for error in errors)


def test_standalone_volume_with_triggers_block_is_invalid():
    cfg, errors = _build({
        "enabled": True,
        "triggers": {
            "candidate_threshold": {"enabled": True},
            "confirmed_median": {"enabled": True},
        },
        "volume": _valid_volume(),
    })

    assert is_zigzag_enabled(cfg) is False
    assert any("trade_filter.triggers is not allowed" in error for error in errors)


def test_both_subfilters_disabled_is_invalid_with_actionable_hint():
    _cfg, errors = _build({
        "enabled": True,
        "zigzag": {"enabled": False},
        "volume": {"enabled": False},
    })

    assert errors == [
        "at least one subfilter must be enabled: set "
        "trade_filter.zigzag.enabled: true or trade_filter.volume.enabled: true"
    ]


def test_standalone_volume_rejects_zigzag_payload_with_migration_hint():
    _cfg, errors = _build({
        "enabled": True,
        "zigzag": {
            "enabled": False,
            "reversal_threshold": 0.02,
        },
        "volume": _valid_volume(),
    })

    assert any("remove" in error and "trade_filter.zigzag.enabled: true" in error for error in errors)


def test_empty_zigzag_block_without_legacy_marker_is_now_invalid():
    cfg, errors = _build({
        "enabled": True,
        "zigzag": {},
    })

    assert cfg.zigzag.enabled is False
    assert any("at least one subfilter must be enabled" in error for error in errors)


def test_zigzag_and_volume_together_are_valid():
    cfg, errors = _build({
        "enabled": True,
        "type": "zigzag_st_mode",
        "zigzag": _valid_zigzag(),
        "lifecycle": {},
        "volume": _valid_volume(),
    })

    assert errors == []
    assert is_zigzag_enabled(cfg) is True
    assert is_volume_enabled(cfg) is True


def _global_stats() -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=0.02,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=0.03,
        candidate_trigger_threshold=0.01,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
    )


def test_zigzag_only_behavior_is_bit_identical_after_legality_migration():
    cfg_a, errors_a = _build(_base_zigzag_only())
    cfg_b, errors_b = _build({
        **_base_zigzag_only(),
        "volume": {"enabled": False},
    })
    assert errors_a == []
    assert errors_b == []

    x = np.linspace(0.0, 8.0 * np.pi, 80)
    close = 100.0 + np.sin(x) * 4.0 + np.linspace(0.0, 1.0, 80)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5

    kwargs = dict(
        atr_period=5,
        multiplier=2.0,
        trade_mode="revers",
        commission=0.001,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        zigzag_global_stats=_global_stats(),
    )
    result_a = run_backtest_fast(
        open_, high, low, close, trade_filter_config=cfg_a, **kwargs
    )
    result_b = run_backtest_fast(
        open_, high, low, close, trade_filter_config=cfg_b, **kwargs
    )

    np.testing.assert_array_equal(result_a.positions, result_b.positions)
    np.testing.assert_array_equal(result_a.returns, result_b.returns)
