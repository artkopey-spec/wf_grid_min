"""
Tests for time_utils module (warmup-in-time conversion).
"""

import pytest
import pandas as pd
import numpy as np
from supertrend_optimizer.utils.time_utils import (
    parse_time_string,
    calculate_median_timedelta,
    convert_time_to_bars,
    resolve_warmup_bars
)


class TestParseTimeString:
    """Tests for parse_time_string function."""
    
    def test_parse_days(self):
        """Test parsing days."""
        result = parse_time_string("7d")
        assert result == pd.Timedelta(days=7)
    
    def test_parse_hours(self):
        """Test parsing hours."""
        result = parse_time_string("48h")
        assert result == pd.Timedelta(hours=48)
    
    def test_parse_minutes(self):
        """Test parsing minutes."""
        result = parse_time_string("180m")
        assert result == pd.Timedelta(minutes=180)
    
    def test_parse_seconds(self):
        """Test parsing seconds."""
        result = parse_time_string("3600s")
        assert result == pd.Timedelta(seconds=3600)
    
    def test_parse_uppercase(self):
        """Test parsing with uppercase units."""
        assert parse_time_string("7D") == pd.Timedelta(days=7)
        assert parse_time_string("48H") == pd.Timedelta(hours=48)
        assert parse_time_string("180M") == pd.Timedelta(minutes=180)
    
    def test_parse_float_value(self):
        """Test parsing float values."""
        result = parse_time_string("1.5h")
        assert result == pd.Timedelta(hours=1.5)
    
    def test_parse_with_whitespace(self):
        """Test parsing with whitespace."""
        result = parse_time_string(" 7d ")
        assert result == pd.Timedelta(days=7)
    
    def test_invalid_format_raises(self):
        """Test that invalid format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_string("7")
        
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_string("d7")
        
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_string("7x")
    
    def test_non_positive_value_raises(self):
        """Test that non-positive value raises ValueError."""
        with pytest.raises(ValueError, match="must be positive"):
            parse_time_string("0d")
        
        # Negative values are caught by regex (invalid format)
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_string("-7d")
    
    def test_non_string_input_raises(self):
        """Test that non-string input raises ValueError."""
        with pytest.raises(ValueError, match="must be a string"):
            parse_time_string(7)


class TestCalculateMedianTimedelta:
    """Tests for calculate_median_timedelta function."""
    
    def test_uniform_5min_data(self):
        """Test with uniform 5min data."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        result = calculate_median_timedelta(index)
        assert result == pd.Timedelta(minutes=5)
    
    def test_uniform_1h_data(self):
        """Test with uniform 1h data."""
        index = pd.date_range("2023-01-01", periods=100, freq="1h")
        result = calculate_median_timedelta(index)
        assert result == pd.Timedelta(hours=1)
    
    def test_uniform_daily_data(self):
        """Test with uniform daily data."""
        index = pd.date_range("2023-01-01", periods=100, freq="1D")
        result = calculate_median_timedelta(index)
        assert result == pd.Timedelta(days=1)
    
    def test_data_with_gaps_weekends(self):
        """Test with weekday-only data (gaps on weekends)."""
        # Create weekday-only data (Mon-Fri)
        index = pd.date_range("2023-01-02", periods=50, freq="1D")  # Start on Monday
        index = index[index.dayofweek < 5]  # Filter to weekdays only
        
        result = calculate_median_timedelta(index)
        # Median should be 1 day (most deltas are 1 day, some are 3 days for weekends)
        assert result == pd.Timedelta(days=1)
    
    def test_data_with_irregular_gaps(self):
        """Test with irregular gaps."""
        # Create data with irregular gaps
        timestamps = [
            "2023-01-01 09:30",
            "2023-01-01 09:35",  # 5 min
            "2023-01-01 09:40",  # 5 min
            "2023-01-01 10:00",  # 20 min (gap)
            "2023-01-01 10:05",  # 5 min
            "2023-01-01 10:10",  # 5 min
        ]
        index = pd.DatetimeIndex(timestamps)
        
        result = calculate_median_timedelta(index)
        # Median should be 5 min (4 out of 5 deltas are 5 min)
        assert result == pd.Timedelta(minutes=5)
    
    def test_less_than_2_timestamps_raises(self):
        """Test that < 2 timestamps raises ValueError."""
        index = pd.DatetimeIndex(["2023-01-01"])
        with pytest.raises(ValueError, match="at least 2 timestamps"):
            calculate_median_timedelta(index)
    
    def test_non_datetimeindex_raises(self):
        """Test that non-DatetimeIndex raises TypeError."""
        with pytest.raises(TypeError, match="must be pd.DatetimeIndex"):
            calculate_median_timedelta([1, 2, 3])


class TestConvertTimeToBars:
    """Tests for convert_time_to_bars function."""
    
    def test_1h_with_5min_bars(self):
        """Test 1 hour with 5min bars."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        result = convert_time_to_bars("1h", index)
        assert result == 12  # 60 min / 5 min = 12 bars
    
    def test_1d_with_5min_bars(self):
        """Test 1 day with 5min bars."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        result = convert_time_to_bars("1d", index)
        assert result == 288  # 24 hours * 12 bars/hour = 288 bars
    
    def test_1d_with_1h_bars(self):
        """Test 1 day with 1h bars."""
        index = pd.date_range("2023-01-01", periods=100, freq="1h")
        result = convert_time_to_bars("1d", index)
        assert result == 24  # 24 hours / 1 hour = 24 bars
    
    def test_7d_with_daily_bars(self):
        """Test 7 days with daily bars."""
        index = pd.date_range("2023-01-01", periods=100, freq="1D")
        result = convert_time_to_bars("7d", index)
        assert result == 7  # 7 days / 1 day = 7 bars
    
    def test_rounds_up(self):
        """Test that result is rounded up."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        result = convert_time_to_bars("1m", index)
        # 1 min / 5 min = 0.2 → rounds up to 1
        assert result == 1
    
    def test_minimum_1_bar(self):
        """Test that result is at least 1 bar."""
        index = pd.date_range("2023-01-01", periods=100, freq="1h")
        result = convert_time_to_bars("1s", index)
        # 1 sec / 1 hour = very small → rounds up to 1
        assert result == 1
    
    def test_with_gaps_weekends(self):
        """Test with weekday-only data (gaps on weekends)."""
        # Create weekday-only data
        index = pd.date_range("2023-01-02", periods=50, freq="1D")
        index = index[index.dayofweek < 5]
        
        result = convert_time_to_bars("7d", index)
        # Median delta is 1 day, so 7 days = 7 bars
        assert result == 7


class TestResolveWarmupBars:
    """Tests for resolve_warmup_bars function."""
    
    def test_warmup_period_only(self):
        """Test with warmup_period only."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        result = resolve_warmup_bars(
            warmup_period=20,
            warmup_time=None,
            index=index,
            atr_period=14,
            auto_warmup=False
        )
        assert result == 20
    
    def test_warmup_time_only(self):
        """Test with warmup_time only."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        result = resolve_warmup_bars(
            warmup_period=None,
            warmup_time="1h",
            index=index,
            atr_period=14,
            auto_warmup=False
        )
        assert result == 12  # 1 hour / 5 min = 12 bars
    
    def test_neither_provided(self):
        """Test with neither warmup_period nor warmup_time."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        result = resolve_warmup_bars(
            warmup_period=None,
            warmup_time=None,
            index=index,
            atr_period=14,
            auto_warmup=False
        )
        assert result == 0
    
    def test_auto_warmup_with_period(self):
        """Test auto_warmup with warmup_period."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        
        # warmup_period < atr_period
        result = resolve_warmup_bars(
            warmup_period=10,
            warmup_time=None,
            index=index,
            atr_period=14,
            auto_warmup=True
        )
        assert result == 14  # max(10, 14) = 14
        
        # warmup_period > atr_period
        result = resolve_warmup_bars(
            warmup_period=20,
            warmup_time=None,
            index=index,
            atr_period=14,
            auto_warmup=True
        )
        assert result == 20  # max(20, 14) = 20
    
    def test_auto_warmup_with_time(self):
        """Test auto_warmup with warmup_time."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        
        # warmup_time < atr_period
        result = resolve_warmup_bars(
            warmup_period=None,
            warmup_time="30m",  # 30 min / 5 min = 6 bars
            index=index,
            atr_period=14,
            auto_warmup=True
        )
        assert result == 14  # max(6, 14) = 14
        
        # warmup_time > atr_period
        result = resolve_warmup_bars(
            warmup_period=None,
            warmup_time="2h",  # 2 hours / 5 min = 24 bars
            index=index,
            atr_period=14,
            auto_warmup=True
        )
        assert result == 24  # max(24, 14) = 24
    
    def test_auto_warmup_with_neither(self):
        """Test auto_warmup with neither warmup_period nor warmup_time."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        result = resolve_warmup_bars(
            warmup_period=None,
            warmup_time=None,
            index=index,
            atr_period=14,
            auto_warmup=True
        )
        assert result == 14  # max(0, 14) = 14
    
    def test_both_provided_raises(self):
        """Test that providing both warmup_period and warmup_time raises ValueError."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        with pytest.raises(ValueError, match="Cannot specify both"):
            resolve_warmup_bars(
                warmup_period=20,
                warmup_time="1h",
                index=index,
                atr_period=14,
                auto_warmup=False
            )
    
    def test_negative_warmup_period_raises(self):
        """Test that negative warmup_period raises ValueError."""
        index = pd.date_range("2023-01-01", periods=100, freq="5min")
        with pytest.raises(ValueError, match="must be non-negative"):
            resolve_warmup_bars(
                warmup_period=-10,
                warmup_time=None,
                index=index,
                atr_period=14,
                auto_warmup=False
            )
    
    def test_1m_bars_with_short_warmup(self):
        """Test 1min bars with short warmup time."""
        index = pd.date_range("2023-01-01 09:30", periods=1000, freq="1min")
        result = resolve_warmup_bars(
            warmup_period=None,
            warmup_time="15m",
            index=index,
            atr_period=14,
            auto_warmup=False
        )
        assert result == 15  # 15 min / 1 min = 15 bars
    
    def test_1h_bars_with_long_warmup(self):
        """Test 1h bars with long warmup time."""
        index = pd.date_range("2023-01-01", periods=1000, freq="1h")
        result = resolve_warmup_bars(
            warmup_period=None,
            warmup_time="7d",
            index=index,
            atr_period=14,
            auto_warmup=False
        )
        assert result == 168  # 7 days * 24 hours = 168 bars


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

