from __future__ import annotations

import numpy as np
import pandas as pd

from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    TradeFilterReversalConfirmationConfig,
    TradeFilterTimeFilterConfig,
    TradeFilterVolumeConfig,
    TradeFilterZigZagConfig,
)
from supertrend_optimizer.testing.run_identity import (
    build_tester_run_fingerprint,
)


def _df() -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=5, freq="min")
    values = np.arange(100.0, 105.0)
    return pd.DataFrame(
        {
            "open": values,
            "high": values + 1,
            "low": values - 1,
            "close": values + 0.5,
        },
        index=index,
    )


def _config(confirm_bars: int, *, enabled: bool = True) -> TradeFilterConfig:
    return TradeFilterConfig(
        enabled=True,
        zigzag=TradeFilterZigZagConfig(enabled=False),
        volume=TradeFilterVolumeConfig(enabled=False),
        time_filter=TradeFilterTimeFilterConfig(enabled=False),
        reversal_confirmation=TradeFilterReversalConfirmationConfig(
            enabled=enabled,
            confirm_bars=confirm_bars if enabled else None,
        ),
    )


def _fingerprint(config: TradeFilterConfig) -> str:
    return build_tester_run_fingerprint(
        df=_df(),
        atr_period=2,
        multiplier=1.5,
        trade_mode="revers",
        commission=0.001,
        trade_filter_config=config,
        period_splits=(("100%", 1.0), ("50%", 0.5)),
    )


def test_fingerprint_is_stable_and_sensitive_to_active_confirm_bars() -> None:
    assert _fingerprint(_config(1)) == _fingerprint(_config(1))
    assert _fingerprint(_config(1)) != _fingerprint(_config(2))


def test_disabled_template_is_not_in_effective_identity() -> None:
    assert _fingerprint(_config(1, enabled=False)) == _fingerprint(
        _config(999, enabled=False)
    )


def test_input_change_updates_identity() -> None:
    config = _config(1)
    first = _fingerprint(config)
    changed = _df()
    changed.iloc[0, 0] += 1
    second = build_tester_run_fingerprint(
        df=changed,
        atr_period=2,
        multiplier=1.5,
        trade_mode="revers",
        commission=0.001,
        trade_filter_config=config,
        period_splits=(("100%", 1.0), ("50%", 0.5)),
    )
    assert first != second


def test_volume_change_updates_identity() -> None:
    config = _config(1)
    first_frame = _df()
    first_frame["volume"] = np.arange(10.0, 15.0)
    second_frame = first_frame.copy()
    second_frame.iloc[0, second_frame.columns.get_loc("volume")] += 1.0

    kwargs = {
        "atr_period": 2,
        "multiplier": 1.5,
        "trade_mode": "revers",
        "commission": 0.001,
        "trade_filter_config": config,
        "period_splits": (("100%", 1.0), ("50%", 0.5)),
    }
    assert build_tester_run_fingerprint(
        df=first_frame, **kwargs
    ) != build_tester_run_fingerprint(df=second_frame, **kwargs)


def test_canonical_time_window_change_updates_identity() -> None:
    first_config = _config(1)
    first_config.time_filter.enabled = True
    first_config.time_filter._windows_minutes = ((9 * 60, 12 * 60),)
    second_config = _config(1)
    second_config.time_filter.enabled = True
    second_config.time_filter._windows_minutes = ((10 * 60, 12 * 60),)

    assert _fingerprint(first_config) != _fingerprint(second_config)


def test_actual_period_split_definition_updates_identity() -> None:
    config = _config(1)
    common = {
        "df": _df(),
        "atr_period": 2,
        "multiplier": 1.5,
        "trade_mode": "revers",
        "commission": 0.001,
        "trade_filter_config": config,
    }
    full_sample_only = build_tester_run_fingerprint(
        **common,
        period_splits=(("100%", 1.0),),
    )
    legacy_periods = build_tester_run_fingerprint(
        **common,
        period_splits=(
            ("100%", 1.0),
            ("75%", 0.75),
            ("50%", 0.50),
            ("33%", 0.33),
            ("25%", 0.25),
        ),
    )
    assert full_sample_only != legacy_periods
