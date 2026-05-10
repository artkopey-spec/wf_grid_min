import numpy as np

from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.core.trade_filter_config import (
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    is_zigzag_enabled,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.core.zigzag_st_filter import ZigZagGlobalStats


def _raw_filter(*, zigzag_enabled_marker=...):
    zigzag = {
        "reversal_threshold": 0.02,
        "candidate_trigger_threshold": 0.01,
    }
    if zigzag_enabled_marker is not ...:
        zigzag["enabled"] = zigzag_enabled_marker
    return {
        "enabled": True,
        "type": "zigzag_st_mode",
        "zigzag": zigzag,
        "lifecycle": {},
    }


def _build_resolved(raw_tf: dict):
    raw = {"trade_filter": raw_tf}
    keys = collect_raw_user_keys(raw)
    cfg = build_trade_filter_config_from_raw(raw_tf)
    resolve_zigzag_enabled_in_place(cfg, keys)
    return cfg, keys


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


def _ohlc(n: int = 80):
    x = np.linspace(0.0, 8.0 * np.pi, n)
    close = 100.0 + np.sin(x) * 4.0 + np.linspace(0.0, 1.0, n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    return open_, high, low, close


def _run(cfg):
    open_, high, low, close = _ohlc()
    return run_backtest_fast(
        open_,
        high,
        low,
        close,
        atr_period=5,
        multiplier=2.0,
        trade_mode="revers",
        commission=0.001,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        trade_filter_config=cfg,
        zigzag_global_stats=_global_stats(),
    )


def test_legacy_yaml_without_zigzag_enabled_resolves_true():
    cfg, _keys = _build_resolved(_raw_filter())

    assert cfg.zigzag.enabled is True
    assert is_zigzag_enabled(cfg) is True


def test_explicit_zigzag_enabled_true_is_preserved():
    cfg, _keys = _build_resolved(_raw_filter(zigzag_enabled_marker=True))

    assert cfg.zigzag.enabled is True
    assert is_zigzag_enabled(cfg) is True


def test_explicit_zigzag_enabled_false_without_volume_rejected_by_validator():
    cfg, keys = _build_resolved(_raw_filter(zigzag_enabled_marker=False))
    errors: list[str] = []

    validate_trade_filter(cfg, errors, keys)

    assert any("at least one subfilter must be enabled" in error for error in errors)


def test_empty_zigzag_block_with_root_enabled_remains_legacy_compatible():
    cfg, _keys = _build_resolved({
        "enabled": True,
        "type": "zigzag_st_mode",
        "zigzag": {},
        "lifecycle": {},
    })

    assert cfg.zigzag.enabled is True
    assert is_zigzag_enabled(cfg) is True


def test_zigzag_only_behavior_is_bit_identical_with_explicit_enabled_true():
    legacy_cfg, _legacy_keys = _build_resolved(_raw_filter())
    explicit_cfg, _explicit_keys = _build_resolved(_raw_filter(zigzag_enabled_marker=True))

    legacy = _run(legacy_cfg)
    explicit = _run(explicit_cfg)

    np.testing.assert_array_equal(legacy.positions, explicit.positions)
    np.testing.assert_array_equal(legacy.returns, explicit.returns)
