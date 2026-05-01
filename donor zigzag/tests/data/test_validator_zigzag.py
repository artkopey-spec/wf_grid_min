"""
Tests for validate_filter_columns with zigzag modes
(plan v2.0.1 §3.6 integration point 4).
"""
from __future__ import annotations

import pandas as pd
import pytest

from supertrend_optimizer.data.validator import validate_filter_columns
from supertrend_optimizer.utils.exceptions import DataValidationError


def _make_df(with_volume_ma: bool = False, col_name: str = "volume ma"):
    n = 10
    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    data = {
        "open": range(100, 100 + n),
        "high": range(101, 101 + n),
        "low":  range(99, 99 + n),
        "close": range(100, 100 + n),
    }
    if with_volume_ma:
        data[col_name] = [1000.0] * n
    return pd.DataFrame(data, index=idx)


class TestValidateFilterColumnsZigZag:

    def test_zigzag_pure_mode_does_not_require_volume_ma_column(self):
        # Plan v2.0 §3.6 integration point 4: mode='zigzag' has no volume
        # dependency → no DataValidationError even without Volume MA column.
        df = _make_df(with_volume_ma=False)
        cfg = {"mode": "zigzag",
               "volume": {"volume_ma_column": "Volume MA"}}
        # Must not raise
        validate_filter_columns(df, cfg)

    def test_zigzag_and_volume_missing_volume_ma_column_raises(self):
        # Plan v2.0 §3.6: zigzag_and_volume must check Volume MA column.
        df = _make_df(with_volume_ma=False)
        cfg = {"mode": "zigzag_and_volume",
               "volume": {"volume_ma_column": "Volume MA"}}
        with pytest.raises(DataValidationError, match="Volume MA"):
            validate_filter_columns(df, cfg)

    def test_zigzag_and_volume_with_volume_ma_column_ok(self):
        df = _make_df(with_volume_ma=True, col_name="volume ma")
        cfg = {"mode": "zigzag_and_volume",
               "volume": {"volume_ma_column": "Volume MA"}}
        validate_filter_columns(df, cfg)

    def test_zigzag_and_volume_column_case_insensitive(self):
        # Column 'Volume MA' in df.columns; cfg says 'volume ma' — must match
        # case-insensitively (same rule as other _and_volume modes).
        df = _make_df(with_volume_ma=True, col_name="Volume MA")
        cfg = {"mode": "zigzag_and_volume",
               "volume": {"volume_ma_column": "volume ma"}}
        validate_filter_columns(df, cfg)

    def test_zigzag_pure_mode_ok_even_without_any_volume_column(self):
        df = _make_df(with_volume_ma=False)
        cfg = {"mode": "zigzag", "volume": {"volume_ma_column": ""}}
        # Empty column name is ignored for pure zigzag (no check triggered).
        validate_filter_columns(df, cfg)
