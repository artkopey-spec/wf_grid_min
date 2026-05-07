"""
Time-filter Tester integration tests.

docs/time_filter_plan_v1_final.txt §8.2

Test groups:
  1. load_tester_config accepts time_filter block (disabled path).
  2. load_tester_config accepts time_filter block (enabled path).
  3. equal_blocks + time_filter.enabled=true still rejected (existing gate).
  4. Smoke run_single_backtest with enabled time_filter on synthetic minute data.
  5. time_filter_reset_count consistent with filter_block_reason and
     time_filter_reset_event array (§8.2 invariant).
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.cli.tester import load_tester_config
from supertrend_optimizer.core.trade_filter_config import TradeFilterConfig
from supertrend_optimizer.utils.exceptions import ConfigError


# ---------------------------------------------------------------------------
# YAML helpers (mirrors test_wp_t2_load_tester_config.py pattern)
# ---------------------------------------------------------------------------

_BASE_YAML = dedent(
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
    """
).strip()

_BASE_YAML_EQUAL_BLOCKS = _BASE_YAML.replace("mode: legacy", "mode: equal_blocks")


def _write_config(tmp_path: Path, trade_filter_block: str | None, *, base: str = _BASE_YAML) -> Path:
    parts = [base]
    if trade_filter_block is not None:
        parts.append(trade_filter_block)
    cfg_path = tmp_path / "config_tester.yaml"
    cfg_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return cfg_path


_ENABLED_BLOCK_NO_TF = dedent(
    """
    trade_filter:
      enabled: true
      type: zigzag_st_mode
      zigzag:
        global_stats_source: full_dataset
        leg_height_mode: pct
        reversal_threshold: 0.005
        candidate_trigger_threshold: 0.012
        global_median: auto
        local_window: 5
      triggers:
        candidate_threshold:
          enabled: true
        confirmed_median:
          enabled: true
      lifecycle:
        freeze_confirmed_legs: 5
        stop_check: confirm_bar_only
        stopping_exit: opposite_st_flip
      diagnostics:
        export_state_columns: true
        export_trigger_columns: true
    """
).strip()

_ENABLED_BLOCK_WITH_TF_DISABLED = dedent(
    """
    trade_filter:
      enabled: true
      type: zigzag_st_mode
      zigzag:
        global_stats_source: full_dataset
        leg_height_mode: pct
        reversal_threshold: 0.005
        candidate_trigger_threshold: 0.012
        global_median: auto
        local_window: 5
      triggers:
        candidate_threshold:
          enabled: true
        confirmed_median:
          enabled: true
      lifecycle:
        freeze_confirmed_legs: 5
        stop_check: confirm_bar_only
        stopping_exit: opposite_st_flip
      diagnostics:
        export_state_columns: true
        export_trigger_columns: true
      time_filter:
        enabled: false
        window: "09:00-19:00"
    """
).strip()

_ENABLED_BLOCK_WITH_TF_ENABLED = dedent(
    """
    trade_filter:
      enabled: true
      type: zigzag_st_mode
      zigzag:
        global_stats_source: full_dataset
        leg_height_mode: pct
        reversal_threshold: 0.005
        candidate_trigger_threshold: 0.012
        global_median: auto
        local_window: 5
      triggers:
        candidate_threshold:
          enabled: true
        confirmed_median:
          enabled: true
      lifecycle:
        freeze_confirmed_legs: 5
        stop_check: confirm_bar_only
        stopping_exit: opposite_st_flip
      diagnostics:
        export_state_columns: true
        export_trigger_columns: true
      time_filter:
        enabled: true
        window: "09:00-19:00"
    """
).strip()


# ---------------------------------------------------------------------------
# Group 1 — load_tester_config accepts time_filter block (disabled path)
# ---------------------------------------------------------------------------

class TestLoadTesterConfigTimeFilterDisabled:
    """load_tester_config parses time_filter.enabled=false without errors."""

    def test_block_with_disabled_time_filter_accepted(self, tmp_path: Path) -> None:
        cfg_path = _write_config(tmp_path, _ENABLED_BLOCK_WITH_TF_DISABLED)
        cfg = load_tester_config(str(cfg_path))
        tf = cfg["trade_filter"]
        assert isinstance(tf, TradeFilterConfig)
        assert tf.enabled is True
        assert tf.time_filter is not None
        assert tf.time_filter.enabled is False

    def test_disabled_time_filter_window_preserved(self, tmp_path: Path) -> None:
        cfg_path = _write_config(tmp_path, _ENABLED_BLOCK_WITH_TF_DISABLED)
        cfg = load_tester_config(str(cfg_path))
        tf = cfg["trade_filter"]
        assert tf.time_filter.window == "09:00-19:00"

    def test_resolver_noop_when_tf_disabled(self, tmp_path: Path) -> None:
        """Resolver fields remain None when time_filter.enabled=false."""
        cfg_path = _write_config(tmp_path, _ENABLED_BLOCK_WITH_TF_DISABLED)
        cfg = load_tester_config(str(cfg_path))
        tf = cfg["trade_filter"]
        assert tf.time_filter._start_hour is None
        assert tf.time_filter._end_hour is None


# ---------------------------------------------------------------------------
# Group 2 — load_tester_config accepts time_filter block (enabled path)
# ---------------------------------------------------------------------------

class TestLoadTesterConfigTimeFilterEnabled:
    """load_tester_config parses time_filter.enabled=true and resolves fields."""

    def test_enabled_time_filter_accepted(self, tmp_path: Path) -> None:
        cfg_path = _write_config(tmp_path, _ENABLED_BLOCK_WITH_TF_ENABLED)
        cfg = load_tester_config(str(cfg_path))
        tf = cfg["trade_filter"]
        assert isinstance(tf, TradeFilterConfig)
        assert tf.time_filter.enabled is True

    def test_resolver_materialises_fields(self, tmp_path: Path) -> None:
        """After load_tester_config, resolver fields are populated."""
        cfg_path = _write_config(tmp_path, _ENABLED_BLOCK_WITH_TF_ENABLED)
        cfg = load_tester_config(str(cfg_path))
        tf = cfg["trade_filter"]
        assert tf.time_filter._start_hour == 9
        assert tf.time_filter._start_minute == 0
        assert tf.time_filter._end_hour == 19
        assert tf.time_filter._end_minute == 0

    def test_invalid_time_filter_window_rejected(self, tmp_path: Path) -> None:
        """Malformed window raises ConfigError."""
        bad_block = _ENABLED_BLOCK_NO_TF + dedent(
            """
          time_filter:
            enabled: true
            window: "9:00-19:00"
            """
        )
        cfg_path = _write_config(tmp_path, bad_block)
        with pytest.raises(ConfigError):
            load_tester_config(str(cfg_path))


# ---------------------------------------------------------------------------
# Group 3 — equal_blocks gate still works with time_filter present
# ---------------------------------------------------------------------------

class TestEqualBlocksGateWithTimeFilter:
    """equal_blocks + enabled time_filter still rejects (existing §5.5 gate)."""

    def test_equal_blocks_with_time_filter_raises(self, tmp_path: Path) -> None:
        cfg_path = _write_config(
            tmp_path,
            _ENABLED_BLOCK_WITH_TF_ENABLED,
            base=_BASE_YAML_EQUAL_BLOCKS,
        )
        with pytest.raises(ConfigError):
            load_tester_config(str(cfg_path))


# ---------------------------------------------------------------------------
# Group 4 & 5 — smoke run + invariant checks
# ---------------------------------------------------------------------------

def _make_minute_ohlc(n: int = 300, seed: int = 7) -> pd.DataFrame:
    """Synthetic OHLC with minute DatetimeIndex spanning multiple 09:00-19:00 windows."""
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(0.0001, 0.003, size=n)
    close = 100.0 * np.exp(np.cumsum(log_returns))
    noise = rng.uniform(0.0005, 0.002, size=n)
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.clip(close * (1 + rng.uniform(-0.001, 0.001, size=n)), low, high)
    # Start at 2024-01-02 09:00, 1-minute bars
    idx = pd.date_range("2024-01-02 09:00", periods=n, freq="min")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close},
        index=idx,
    )


def _make_tf_config_with_time_filter(*, window: str = "09:00-15:00") -> Any:
    """Build a minimal enabled TradeFilterConfig with time_filter enabled."""
    from supertrend_optimizer.core.trade_filter_config import (
        TradeFilterConfig, TradeFilterDiagnosticsConfig,
        TradeFilterLifecycleConfig, TradeFilterTimeFilterConfig,
        TradeFilterTriggerToggleConfig, TradeFilterTriggersConfig,
        TradeFilterZigZagConfig, resolve_time_filter_in_place,
    )

    tf_cfg = TradeFilterTimeFilterConfig(enabled=True, window=window)
    cfg = TradeFilterConfig(
        enabled=True,
        type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            reversal_threshold=0.002,
            candidate_trigger_threshold=0.002,
            local_window=3,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=0,
            stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
        ),
        diagnostics=TradeFilterDiagnosticsConfig(
            export_state_columns=True,
            export_trigger_columns=True,
        ),
        time_filter=tf_cfg,
    )
    # Simulate resolver: populate _start_hour etc.
    resolve_time_filter_in_place(cfg, raw_user_keys=set())
    return cfg


@pytest.fixture(scope="module")
def minute_df():
    return _make_minute_ohlc(n=300)


@pytest.fixture(scope="module")
def tf_cfg_enabled():
    # Window 09:00-13:00: 240 bars in window, 60 outside for n=300 bars from 09:00
    return _make_tf_config_with_time_filter(window="09:00-13:00")


@pytest.fixture(scope="module")
def period_result_with_time_filter(minute_df, tf_cfg_enabled):
    from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
    from supertrend_optimizer.testing.runner import run_period

    stats = build_zigzag_global_stats(
        close=minute_df["close"].values,
        trade_filter_config=tf_cfg_enabled,
    )
    return run_period(
        df=minute_df,
        atr_period=5,
        multiplier=1.5,
        trade_mode="revers",
        commission=0.001,
        warmup_period=10,
        periods_per_year=252.0,
        trade_filter_config=tf_cfg_enabled,
        zigzag_global_stats=stats,
        global_offset=0,
    )


class TestTimeFilterSmoke:
    """§8.2 smoke: run_single_backtest with enabled time_filter on minute data."""

    def test_period_result_has_no_error(self, period_result_with_time_filter):
        """run_period completes without raising on minute data with time_filter."""
        pr = period_result_with_time_filter
        assert pr is not None

    def test_filter_diagnostics_present(self, period_result_with_time_filter):
        """filter_diagnostics is not None on enabled path."""
        pr = period_result_with_time_filter
        assert pr.filter_diagnostics is not None

    def test_time_filter_keys_in_diagnostics(self, period_result_with_time_filter):
        """Three time_filter per-bar keys present in filter_diagnostics."""
        fd = period_result_with_time_filter.filter_diagnostics
        assert "time_filter_enabled" in fd
        assert "time_filter_in_window" in fd
        assert "time_filter_reset_event" in fd

    def test_time_filter_enabled_flag_is_one(self, period_result_with_time_filter):
        """time_filter_enabled array is all-ones when time_filter.enabled=true."""
        fd = period_result_with_time_filter.filter_diagnostics
        assert np.all(fd["time_filter_enabled"] == 1)

    def test_some_bars_outside_window(self, period_result_with_time_filter):
        """On minute data spanning past 13:00, some bars are outside window 09:00-13:00."""
        fd = period_result_with_time_filter.filter_diagnostics
        # Window 09:00-13:00; 300 bars from 09:00 → bars 240-299 are outside
        out = int(np.sum(fd["time_filter_in_window"] == 0))
        assert out > 0, (
            f"Expected bars outside window, got 0. "
            f"in_window sum={int(np.sum(fd['time_filter_in_window']))}"
        )


class TestTimeFilterCountersInvariant:
    """§8.2: time_filter counters consistent with bar-level arrays.

    time_filter_reset_count == sum(time_filter_reset_event == 1)
    time_filter_reset is NOT included in blocked_entry_signals (§0.3 #10).
    """

    def test_reset_count_matches_array_sum(self, period_result_with_time_filter):
        fd = period_result_with_time_filter.filter_diagnostics
        summary_counters = period_result_with_time_filter.filter_diagnostics_summary["counters"]
        expected = int(np.sum(fd["time_filter_reset_event"] == 1))
        actual = summary_counters["time_filter_reset_count"]
        assert actual == expected, (
            f"time_filter_reset_count mismatch: "
            f"summary={actual}, array_sum={expected}"
        )

    def test_bars_in_window_matches_array_sum(self, period_result_with_time_filter):
        fd = period_result_with_time_filter.filter_diagnostics
        summary_counters = period_result_with_time_filter.filter_diagnostics_summary["counters"]
        expected_in = int(np.sum(fd["time_filter_in_window"] == 1))
        expected_out = int(np.sum(fd["time_filter_in_window"] == 0))
        assert summary_counters["time_filter_bars_in_window"] == expected_in
        assert summary_counters["time_filter_bars_out_window"] == expected_out

    def test_time_filter_reset_not_in_blocked_entry_signals(self, period_result_with_time_filter):
        """§0.3 #10: time_filter_reset excluded from blocked_entry_signals sum."""
        fd = period_result_with_time_filter.filter_diagnostics
        c = period_result_with_time_filter.filter_diagnostics_summary["counters"]

        # blocked_entry_signals must equal sum of the six blocked_* components
        sum_six = (
            c["blocked_filter_off"]
            + c["blocked_waiting_first"]
            + c["blocked_trade_mode"]
            + c["blocked_local_median"]
            + c["blocked_invalid_stats"]
            + c["blocked_stopping"]
        )
        assert c["blocked_entry_signals"] == sum_six, (
            f"Invariant 2: blocked_entry_signals={c['blocked_entry_signals']} "
            f"!= sum_of_six={sum_six}. "
            "time_filter_reset must NOT be included in blocked_entry_signals."
        )

    def test_filter_block_reason_reset_consistent_with_counter(
        self, period_result_with_time_filter
    ):
        """time_filter_reset_count == bars with filter_block_reason=='time_filter_reset'."""
        fd = period_result_with_time_filter.filter_diagnostics
        c = period_result_with_time_filter.filter_diagnostics_summary["counters"]
        reason_count = int(
            np.sum(np.asarray(fd["filter_block_reason"]) == "time_filter_reset")
        )
        assert c["time_filter_reset_count"] == reason_count, (
            f"time_filter_reset_count={c['time_filter_reset_count']} != "
            f"filter_block_reason count={reason_count}"
        )
