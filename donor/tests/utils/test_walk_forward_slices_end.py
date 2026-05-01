"""
Tests for Walk-Forward window slicing with anchor='end'.
"""

import pytest
import pandas as pd
from supertrend_optimizer.utils.time_utils import (
    make_walk_forward_slices,
    WFWindowSlice
)


class TestMakeWalkForwardSlicesBarBasedEnd:
    """Tests for bar-based Walk-Forward slices with anchor='end'."""
    
    @pytest.fixture
    def daily_index(self):
        """3 years of daily data."""
        return pd.date_range("2020-01-01", periods=365 * 3, freq="D")
    
    def test_rolling_bars_end_basic(self, daily_index):
        """Rolling scheme with anchor='end' - basic test."""
        n = len(daily_index)
        
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="500bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # Last window should end at data end
        last = windows[-1]
        assert last.test_end_idx == n
    
    def test_rolling_bars_end_chronological_order(self, daily_index):
        """Windows should be in chronological order after reverse."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="500bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        # Check chronological order
        for i in range(len(windows) - 1):
            assert windows[i].test_start_idx < windows[i + 1].test_start_idx
    
    def test_rolling_bars_end_step_indices(self, daily_index):
        """Step indices should be 0..N-1 after reverse."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="500bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        for i, w in enumerate(windows):
            assert w.step_index == i
    
    def test_rolling_bars_end_last_window_at_end(self, daily_index):
        """Last window test_end should be exactly at data end."""
        n = len(daily_index)
        
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="300bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert windows[-1].test_end_idx == n
        assert windows[-1].test_start_idx == n - 100
    
    def test_expanding_bars_end(self, daily_index):
        """Expanding scheme with anchor='end': train_start always 0."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="300bars",
            test_size="100bars",
            step_size="100bars",
            scheme="expanding",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # All windows should have train_start_idx == 0
        for w in windows:
            assert w.train_start_idx == 0
        
        # train_end should equal test_start
        for w in windows:
            assert w.train_end_idx == w.test_start_idx
    
    def test_expanding_bars_end_last_window(self, daily_index):
        """Expanding end: last window should end at data end."""
        n = len(daily_index)
        
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="300bars",
            test_size="100bars",
            step_size="100bars",
            scheme="expanding",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert windows[-1].test_end_idx == n


class TestMakeWalkForwardSlicesTimeBasedEnd:
    """Tests for time-based Walk-Forward slices with anchor='end'."""
    
    @pytest.fixture
    def daily_index(self):
        """3 years of daily data."""
        return pd.date_range("2020-01-01", periods=365 * 3, freq="D")
    
    def test_rolling_time_end_basic(self, daily_index):
        """Rolling with time-based sizes and anchor='end'."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="3mo",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # Last window should be near data end
        last = windows[-1]
        n = len(daily_index)
        # Should be within a few bars of end (due to searchsorted)
        assert last.test_end_idx >= n - 10
    
    def test_rolling_time_end_chronological_order(self, daily_index):
        """Windows should be in chronological order."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="3mo",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        # Check chronological order
        for i in range(len(windows) - 1):
            assert windows[i].test_start_idx < windows[i + 1].test_start_idx
    
    def test_rolling_time_end_step_default(self, daily_index):
        """Rolling time end: step_size defaults to test_size."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="3mo",
            step_size=None,  # Should default to test_size
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
    
    def test_expanding_time_end(self, daily_index):
        """Expanding with time-based sizes and anchor='end'."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="3mo",
            scheme="expanding",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert len(windows) > 0
        
        # All windows should start at index 0
        for w in windows:
            assert w.train_start_idx == 0
        
        # train_end should equal test_start
        for w in windows:
            assert w.train_end_idx == w.test_start_idx
    
    def test_expanding_time_end_train_grows(self, daily_index):
        """Expanding time end: train grows with each step."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="6mo",
            test_size="2mo",
            step_size="2mo",
            scheme="expanding",
            anchor="end",
            min_train_bars=100,
            min_test_bars=30
        )
        
        assert len(windows) >= 2
        
        # Check train grows (earlier windows have smaller train)
        for i in range(len(windows) - 1):
            assert windows[i].train_end_idx < windows[i + 1].train_end_idx


class TestMakeWalkForwardSlicesEndValidation:
    """Tests for validation with anchor='end'."""
    
    @pytest.fixture
    def daily_index(self):
        return pd.date_range("2020-01-01", periods=365 * 3, freq="D")
    
    def test_anchor_invalid_raises(self, daily_index):
        """Invalid anchor value raises ValueError."""
        with pytest.raises(ValueError, match="anchor must be"):
            make_walk_forward_slices(
                index=daily_index,
                train_size="100bars",
                test_size="50bars",
                anchor="middle"  # Invalid
            )
    
    def test_time_based_with_range_index_raises_end(self):
        """Time-based durations require DatetimeIndex (anchor='end')."""
        index = pd.RangeIndex(0, 1000)
        
        with pytest.raises(ValueError, match="requires DatetimeIndex"):
            make_walk_forward_slices(
                index=index,
                train_size="6mo",
                test_size="3mo",
                anchor="end",
                min_train_bars=100,
                min_test_bars=50
            )
    
    def test_mixed_units_raises_end(self, daily_index):
        """Cannot mix bar-based and time-based durations (anchor='end')."""
        with pytest.raises(ValueError, match="Cannot mix"):
            make_walk_forward_slices(
                index=daily_index,
                train_size="500bars",
                test_size="3mo",  # Mixed!
                anchor="end",
                min_train_bars=100,
                min_test_bars=50
            )


class TestMakeWalkForwardSlicesEndInvariants:
    """Tests for invariants with anchor='end'."""
    
    @pytest.fixture
    def daily_index(self):
        return pd.date_range("2020-01-01", periods=365 * 3, freq="D")
    
    def test_windows_non_overlapping_test_end(self, daily_index):
        """Test windows should not overlap (anchor='end')."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        for i in range(len(windows) - 1):
            # Test window i ends at or before test window i+1 starts
            assert windows[i].test_end_idx <= windows[i + 1].test_start_idx
    
    def test_train_test_contiguous_end(self, daily_index):
        """Test should start immediately after train ends (anchor='end')."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        for w in windows:
            assert w.test_start_idx == w.train_end_idx
    
    def test_minimum_bars_respected_end(self, daily_index):
        """All windows should respect minimum bar requirements (anchor='end')."""
        min_train = 150
        min_test = 75
        
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=min_train,
            min_test_bars=min_test
        )
        
        for w in windows:
            actual_train = w.train_end_idx - w.train_start_idx
            actual_test = w.test_end_idx - w.test_start_idx
            
            assert actual_train >= min_train
            assert actual_test >= min_test
    
    def test_timestamps_match_indices_datetime_end(self, daily_index):
        """For DatetimeIndex, timestamps should match indices (anchor='end')."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="200bars",
            test_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        w = windows[0]
        assert w.train_start_time == daily_index[w.train_start_idx]
        assert w.train_end_time == daily_index[w.train_end_idx - 1]  # Inclusive
        assert w.test_start_time == daily_index[w.test_start_idx]
        assert w.test_end_time == daily_index[w.test_end_idx - 1]  # Inclusive


class TestMakeWalkForwardSlicesStartVsEnd:
    """Compare anchor='start' vs anchor='end' behavior."""
    
    @pytest.fixture
    def daily_index(self):
        return pd.date_range("2020-01-01", periods=1000, freq="D")
    
    def test_start_and_end_different_alignment(self, daily_index):
        """anchor='start' and anchor='end' can produce different windows."""
        windows_start = make_walk_forward_slices(
            index=daily_index,
            train_size="300bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        windows_end = make_walk_forward_slices(
            index=daily_index,
            train_size="300bars",
            test_size="100bars",
            step_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        # Both should produce valid windows
        assert len(windows_start) > 0
        assert len(windows_end) > 0
        
        # They may or may not be identical depending on data alignment
        # Just verify both work correctly
    
    def test_start_begins_at_zero(self, daily_index):
        """anchor='start' first window starts at 0."""
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="300bars",
            test_size="100bars",
            scheme="rolling",
            anchor="start",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert windows[0].train_start_idx == 0
    
    def test_end_finishes_at_n(self, daily_index):
        """anchor='end' last window ends at n."""
        n = len(daily_index)
        
        windows = make_walk_forward_slices(
            index=daily_index,
            train_size="300bars",
            test_size="100bars",
            scheme="rolling",
            anchor="end",
            min_train_bars=100,
            min_test_bars=50
        )
        
        assert windows[-1].test_end_idx == n
    
    def test_both_anchors_maintain_invariants(self, daily_index):
        """Both anchors should maintain same invariants."""
        for anchor in ["start", "end"]:
            windows = make_walk_forward_slices(
                index=daily_index,
                train_size="200bars",
                test_size="100bars",
                step_size="100bars",
                scheme="rolling",
                anchor=anchor,
                min_train_bars=100,
                min_test_bars=50
            )
            
            # Check invariants
            for i, w in enumerate(windows):
                # Sequential step_index
                assert w.step_index == i
                
                # Train and test contiguous
                assert w.test_start_idx == w.train_end_idx
                
                # Minimum bars
                assert w.train_end_idx - w.train_start_idx >= 100
                assert w.test_end_idx - w.test_start_idx >= 50
            
            # Non-overlapping test windows
            for i in range(len(windows) - 1):
                assert windows[i].test_end_idx <= windows[i + 1].test_start_idx
