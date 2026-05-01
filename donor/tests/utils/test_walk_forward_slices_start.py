"""
Tests for Walk-Forward window slicing with anchor='start'.
"""

import pytest
import pandas as pd
from supertrend_optimizer.utils.time_utils import (
    make_walk_forward_slices,
    WFWindowSlice
)


class TestMakeWalkForwardSlicesBarBased:
    """Tests for bar-based Walk-Forward slices with anchor='start'."""
    
    @pytest.fixture
    def daily_index(self):
        """3 years of daily data."""
        return pd.date_range("2020-01-01", periods=365 * 3, freq="D")
    
    def test_rolling_bars_basic(self, daily_index):
        """Rolling scheme with bar-based sizes."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="500bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # First window
        assert windows[0].step_index == 0
        assert windows[0].train_start_idx == 0
        assert windows[0].train_end_idx == 500
        assert windows[0].test_start_idx == 500
        assert windows[0].test_end_idx == 600
        
        # Check timestamps are set
        assert windows[0].train_start_time is not None
        assert windows[0].train_end_time is not None
        assert windows[0].test_start_time is not None
        assert windows[0].test_end_time is not None
    
    def test_rolling_bars_second_window_shift(self, daily_index):
        """Rolling: train moves by step on second window."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="500bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        # Rolling: train moves by step
        if len(windows) > 1:
            assert windows[1].step_index == 1
            assert windows[1].train_start_idx == 100  # Shifted by step_size
            assert windows[1].train_end_idx == 600
            assert windows[1].test_start_idx == 600
            assert windows[1].test_end_idx == 700
    
    def test_rolling_bars_with_range_index(self):
        """Bar-based works with RangeIndex (no DatetimeIndex required)."""
        index = pd.RangeIndex(0, 1000)
        
        windows = make_walk_forward_slices(
            index=index,
            train_size="300bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        assert windows[0].train_start_idx == 0
        assert windows[0].train_end_idx == 300
        assert windows[0].test_start_idx == 300
        assert windows[0].test_end_idx == 400
        
        # Timestamps should be None for RangeIndex
        assert windows[0].train_start_time is None
        assert windows[0].train_end_time is None
    
    def test_expanding_bars(self, daily_index):
        """Expanding scheme: train always starts at 0."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="300bars",
            test_size="100bars",
            step_size="100bars",
            scheme="expanding",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # All windows should have train_start_idx == 0
        for w in windows:
            assert w.train_start_idx == 0
        
        # Train grows: window 0 has 300 bars, window 1 has 400 bars, etc.
        assert windows[0].train_end_idx == 300
        if len(windows) > 1:
            assert windows[1].train_end_idx == 400
        if len(windows) > 2:
            assert windows[2].train_end_idx == 500
    
    def test_expanding_bars_train_grows(self, daily_index):
        """Expanding: train_end_idx grows with each step."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            step_size="50bars",
            scheme="expanding",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # Check train grows by step_size
        for i in range(len(windows) - 1):
            train_growth = windows[i + 1].train_end_idx - windows[i].train_end_idx
            # Should be approximately step_size (50 bars)
            assert train_growth == 50


class TestMakeWalkForwardSlicesTimeBased:
    """Tests for time-based Walk-Forward slices with anchor='start'."""
    
    @pytest.fixture
    def daily_index(self):
        """3 years of daily data."""
        return pd.date_range("2020-01-01", periods=365 * 3, freq="D")
    
    def test_rolling_time_basic(self, daily_index):
        """Rolling with time-based sizes (6mo train, 3mo test)."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="3mo",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # First window starts at 0
        assert windows[0].train_start_idx == 0
        
        # Train should be ~6 months
        # 6 months of daily data is roughly 180-185 bars
        assert windows[0].train_end_idx > 150
        assert windows[0].train_end_idx < 200
        
        # Timestamps should be set
        assert windows[0].train_start_time is not None
        assert windows[0].test_end_time is not None
    
    def test_rolling_time_train_end_idx_range(self, daily_index):
        """Rolling time: train_end_idx should be in expected range for 6mo."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="3mo",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        # 6 months should be roughly 180 days (150-200 range)
        assert windows[0].train_end_idx >= 150
        assert windows[0].train_end_idx <= 200
    
    def test_rolling_time_step_default(self, daily_index):
        """Rolling time: step_size defaults to test_size."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="3mo",
            step_size=None,  # Should default to test_size
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # Step should be ~3 months (test_size)
        if len(windows) > 1:
            step = windows[1].train_start_idx - windows[0].train_start_idx
            # ~3 months = ~90 days
            assert 70 < step < 110
    
    def test_expanding_time(self, daily_index):
        """Expanding with time-based sizes."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="3mo",
            scheme="expanding",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # All windows should start at index 0
        for w in windows:
            assert w.train_start_idx == 0
        
        # Train should grow with each step
        if len(windows) > 1:
            assert windows[1].train_end_idx > windows[0].train_end_idx
    
    def test_expanding_time_train_grows(self, daily_index):
        """Expanding time: train grows by step_size each iteration."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="2mo",
            step_size="2mo",
            scheme="expanding",
            anchor="start",
            min_train_bars=100,
            min_test_bars=30
        )
        
        assert len(windows) >= 2
        
        # Check train grows
        for i in range(len(windows) - 1):
            assert windows[i + 1].train_end_idx > windows[i].train_end_idx
            # Growth should be roughly step_size (~2 months = ~60 days)
            growth = windows[i + 1].train_end_idx - windows[i].train_end_idx
            assert 50 < growth < 70


class TestMakeWalkForwardSlicesValidation:
    """Tests for validation and error cases."""
    
    @pytest.fixture
    def daily_index(self):
        return pd.date_range("2020-01-01", periods=365 * 3, freq="D")
    
    def test_insufficient_data_raises(self):
        """Should raise if not enough data for even one window."""
        short_index = pd.date_range("2023-01-01", periods=50, freq="D")
        
        with pytest.raises(ValueError, match="No valid Walk-Forward windows"):
            make_walk_forward_slices(
                index=short_index,
                train_size="100bars",
                test_size="50bars",
                anchor="start",
                min_train_bars=100,
                min_test_bars=50
            )
    
    def test_min_train_bars_not_satisfied_raises(self):
        """Should raise if min_train_bars cannot be satisfied."""
        index = pd.date_range("2023-01-01", periods=200, freq="D")
        
        with pytest.raises(ValueError, match="Insufficient data for train"):
            make_walk_forward_slices(
                index=index,
                train_size="50bars",
                test_size="50bars",
                anchor="start",
                min_train_bars=100,  # Cannot satisfy with 50 bars
                min_test_bars=50
            )
    
    def test_mixed_units_raises(self, daily_index):
        """Should raise if mixing bar-based and time-based durations."""
        with pytest.raises(ValueError, match="Cannot mix"):
            make_walk_forward_slices(
                index=daily_index,
                train_size="500bars",
                test_size="3mo",  # Mixed!
                anchor="start",
                min_train_bars=100,
                min_test_bars=50
            )
    
    def test_time_based_with_range_index_raises(self):
        """Time-based durations require DatetimeIndex."""
        index = pd.RangeIndex(0, 1000)
        
        with pytest.raises(ValueError, match="requires DatetimeIndex"):
            make_walk_forward_slices(
                index=index,
                train_size="6mo",
                test_size="3mo",
                anchor="start",
                min_train_bars=100,
                min_test_bars=50
            )
    
    def test_invalid_scheme_raises(self, daily_index):
        """Invalid scheme raises ValueError."""
        with pytest.raises(ValueError, match="scheme must be"):
            make_walk_forward_slices(
                index=daily_index,
                train_size="100bars",
                test_size="50bars",
                scheme="invalid",  # Invalid
                anchor="start"
            )
    
    def test_anchor_invalid_value(self, daily_index):
        """Invalid anchor value should raise ValueError."""
        with pytest.raises(ValueError, match="anchor must be"):
            make_walk_forward_slices(
                index=daily_index,
                train_size="100bars",
                test_size="50bars",
                anchor="middle"  # Invalid
            )
    
    def test_no_windows_generated_raises(self):
        """If no windows can be generated, raise ValueError."""
        # Very small data with large requirements
        index = pd.date_range("2023-01-01", periods=100, freq="D")
        
        with pytest.raises(ValueError, match="No valid Walk-Forward windows"):
            make_walk_forward_slices(
                index=index,
                train_size="200bars",  # Too large
                test_size="100bars",
                anchor="start",
                min_train_bars=200,
                min_test_bars=100
            )


class TestMakeWalkForwardSlicesInvariants:
    """Tests for invariants and properties of generated windows."""
    
    @pytest.fixture
    def daily_index(self):
        return pd.date_range("2020-01-01", periods=365 * 3, freq="D")
    
    def test_windows_non_overlapping_test(self, daily_index):
        """Test windows should not overlap."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        for i in range(len(windows) - 1):
            # Test window i ends at or before test window i+1 starts
            assert windows[i].test_end_idx <= windows[i + 1].test_start_idx
    
    def test_step_indices_sequential(self, daily_index):
        """Step indices should be sequential starting from 0."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        for i, w in enumerate(windows):
            assert w.step_index == i
    
    def test_train_test_contiguous(self, daily_index):
        """Test should start immediately after train ends."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        for w in windows:
            assert w.test_start_idx == w.train_end_idx
    
    def test_indices_exclusive_end(self, daily_index):
        """train_end_idx and test_end_idx should be exclusive."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        # Check that slicing works correctly (exclusive end)
        w = windows[0]
        train_slice = slice(w.train_start_idx, w.train_end_idx)
        test_slice = slice(w.test_start_idx, w.test_end_idx)
        
        # These should not overlap
        train_indices = set(range(w.train_start_idx, w.train_end_idx))
        test_indices = set(range(w.test_start_idx, w.test_end_idx))
        
        assert len(train_indices & test_indices) == 0  # No overlap
    
    def test_timestamps_match_indices_datetime(self, daily_index):
        """For DatetimeIndex, timestamps should match indices."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        w = windows[0]
        assert w.train_start_time == daily_index[w.train_start_idx]
        assert w.train_end_time == daily_index[w.train_end_idx - 1]  # Inclusive
        assert w.test_start_time == daily_index[w.test_start_idx]
        assert w.test_end_time == daily_index[w.test_end_idx - 1]  # Inclusive
    
    def test_minimum_bars_respected(self, daily_index):
        """All windows should respect minimum bar requirements."""
        min_train = 150
        min_test = 75
        
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=min_train,
            min_test_bars=min_test
        )
        
        for w in windows:
            actual_train = w.train_end_idx - w.train_start_idx
            actual_test = w.test_end_idx - w.test_start_idx
            
            assert actual_train >= min_train
            assert actual_test >= min_test
