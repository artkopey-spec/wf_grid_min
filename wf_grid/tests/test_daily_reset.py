"""
Tests for daily_reset feature — plan v3.

§9.1  Config / schema (Этап 2)
§9.2  Helper _infer_daily_reset_event (Этап 3)
§9.3  ZigZag close-only pass (Этап 5)
§9.4  FSM apply() (Этап 6)
§9.5  Diagnostics (Этап 7)
§9.6  OOS prepend (Этап 9)
§9.7  Regression / compatibility (Этап 10)
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wf_grid.config.loader import ConfigError, load_grid_config
from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    ZigZagPerBar,
    _infer_daily_reset_event,
    apply,
    attach_trade_filter_diagnostics,
    build_zigzag_global_stats,
    compute_zigzag_per_bar,
)
from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.core.trades import extract_trades
from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    TradeFilterDiagnosticsConfig,
    TradeFilterLifecycleConfig,
    TradeFilterTriggerToggleConfig,
    TradeFilterTriggersConfig,
    TradeFilterZigZagConfig,
)
from supertrend_optimizer.testing.runner import _compute_summary_counters
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError as STConfigError
from supertrend_optimizer.utils.time_utils import WFWindowSlice
from wf_grid.wf.step_executor import (
    _apply_oos_force_flat,
    _compute_filter_diagnostics_summary,
    execute_oos_step,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MINIMAL_BASE = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""

_ENABLED_BLOCK = """\
trade_filter:
  enabled: true
  type: zigzag_st_mode
  zigzag:
    reversal_threshold: 0.005
    candidate_trigger_threshold: 0.012
    local_window: 5
  triggers:
    candidate_threshold:
      enabled: true
    confirmed_median:
      enabled: true
  lifecycle:
    freeze_confirmed_legs: 3
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
"""


def _write(tmp_path: Path, content: str) -> str:
    p = tmp_path / "cfg.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def _write_enabled(tmp_path: Path, override: str = "") -> str:
    return _write(tmp_path, _MINIMAL_BASE + _ENABLED_BLOCK + override)


def _write_disabled(tmp_path: Path, extra: str = "") -> str:
    block = "trade_filter:\n  enabled: false\n  type: zigzag_st_mode\n"
    return _write(tmp_path, _MINIMAL_BASE + block + extra)


@dataclass
class _ToggleDouble:
    enabled: bool = True


@dataclass
class _TriggersDouble:
    candidate_threshold: _ToggleDouble = field(default_factory=_ToggleDouble)
    confirmed_median: _ToggleDouble = field(default_factory=_ToggleDouble)


@dataclass
class _LifecycleDouble:
    freeze_confirmed_legs: int = 5
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"


@dataclass
class _ZigZagDouble:
    daily_reset: bool = False
    local_window: int = 5


@dataclass
class _FilterCfgDouble:
    zigzag: _ZigZagDouble = field(default_factory=_ZigZagDouble)
    triggers: _TriggersDouble = field(default_factory=_TriggersDouble)
    lifecycle: _LifecycleDouble = field(default_factory=_LifecycleDouble)


def _make_filter_cfg(
    *,
    daily_reset: bool = False,
    a_enabled: bool = True,
    b_enabled: bool = True,
    freeze_confirmed_legs: int = 5,
) -> _FilterCfgDouble:
    return _FilterCfgDouble(
        zigzag=_ZigZagDouble(daily_reset=daily_reset),
        triggers=_TriggersDouble(
            candidate_threshold=_ToggleDouble(enabled=a_enabled),
            confirmed_median=_ToggleDouble(enabled=b_enabled),
        ),
        lifecycle=_LifecycleDouble(freeze_confirmed_legs=freeze_confirmed_legs),
    )


def _make_global_stats(
    *,
    global_median: float = 0.05,
    candidate_trigger_threshold: float = 0.05,
    reversal_threshold: float = 0.01,
) -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=reversal_threshold,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=global_median,
        candidate_trigger_threshold=candidate_trigger_threshold,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
    )


def _make_per_bar(
    *,
    n: int,
    candidate_height_pct: np.ndarray | None = None,
    confirm_event: np.ndarray | None = None,
    confirmed_leg_idx_at_t: np.ndarray | None = None,
    last_confirmed_leg_height_pct: np.ndarray | None = None,
    local_median_N: np.ndarray | None = None,
    local_median_available: np.ndarray | None = None,
) -> ZigZagPerBar:
    if candidate_height_pct is None:
        candidate_height_pct = np.full(n, np.nan, dtype=np.float64)
    if confirm_event is None:
        confirm_event = np.zeros(n, dtype=np.int8)
    if confirmed_leg_idx_at_t is None:
        confirmed_leg_idx_at_t = np.full(n, -1, dtype=np.int64)
    if last_confirmed_leg_height_pct is None:
        last_confirmed_leg_height_pct = np.full(n, np.nan, dtype=np.float64)
    if local_median_N is None:
        local_median_N = np.full(n, np.nan, dtype=np.float64)
    if local_median_available is None:
        local_median_available = np.zeros(n, dtype=bool)
    return ZigZagPerBar(
        candidate_height_pct=candidate_height_pct,
        confirm_event=confirm_event,
        confirmed_leg_idx_at_t=confirmed_leg_idx_at_t,
        last_confirmed_leg_height_pct=last_confirmed_leg_height_pct,
        local_median_N=local_median_N,
        local_median_available=local_median_available,
    )


def _run_apply(
    *,
    trend: np.ndarray,
    per_bar: ZigZagPerBar,
    daily_reset_event,
    cfg: _FilterCfgDouble | None = None,
    stats: ZigZagGlobalStats | None = None,
):
    return apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=cfg if cfg is not None else _make_filter_cfg(),
        zigzag_global_stats=stats if stats is not None else _make_global_stats(),
        per_bar=per_bar,
        daily_reset_event=daily_reset_event,
    )


@dataclass
class _WFBacktestCfgDouble:
    commission: float = 0.001
    early_exit_max_drawdown: float = 0.5
    early_exit_check_bars: int = 0
    min_trades_required: int = 1


@dataclass
class _WFValidationCfgDouble:
    warmup_period: int = 0


@dataclass
class _WFGridConfigDouble:
    trade_filter: TradeFilterConfig
    backtest: _WFBacktestCfgDouble = field(default_factory=_WFBacktestCfgDouble)
    validation: _WFValidationCfgDouble = field(default_factory=_WFValidationCfgDouble)
    resolved_periods_per_year: float = 252.0


@dataclass
class _WFGridPointDouble:
    grid_point_id: str = "gp_daily_reset"
    atr_period: int = 5
    multiplier: float = 2.0
    trade_mode: str = "revers"


def _make_wf_slice(
    train_start: int,
    train_end: int,
    test_start: int,
    test_end: int,
    step_index: int = 0,
) -> WFWindowSlice:
    return WFWindowSlice(
        train_start_idx=train_start,
        train_end_idx=train_end,
        test_start_idx=test_start,
        test_end_idx=test_end,
        step_index=step_index,
    )


def _make_daily_reset_trade_filter(*, daily_reset: bool = True) -> TradeFilterConfig:
    return TradeFilterConfig(
        enabled=True,
        type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            reversal_threshold=0.03,
            candidate_trigger_threshold=0.01,
            local_window=3,
            daily_reset=daily_reset,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=1,
            stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
        ),
        diagnostics=TradeFilterDiagnosticsConfig(
            export_state_columns=True,
            export_trigger_columns=True,
        ),
    )


def _make_wf_ohlc(n: int = 60) -> pd.DataFrame:
    base = 100.0 + 8.0 * np.sin(np.arange(n, dtype=np.float64) * np.pi / 3.0)
    drift = np.arange(n, dtype=np.float64) * 0.03
    close = base + drift
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    idx = pd.date_range("2026-04-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def _run_daily_reset_oos_step(*, prepend: int, test_start: int = 20, test_end: int = 35):
    data = _make_wf_ohlc()
    trade_filter = _make_daily_reset_trade_filter(daily_reset=True)
    config = _WFGridConfigDouble(trade_filter=trade_filter)
    stats = build_zigzag_global_stats(data["close"].values, trade_filter)
    wf_slice = _make_wf_slice(0, test_start, test_start, test_end)
    return execute_oos_step(
        grid_point=_WFGridPointDouble(),
        wf_slice=wf_slice,
        full_open=data["open"].values,
        full_high=data["high"].values,
        full_low=data["low"].values,
        full_close=data["close"].values,
        full_index=data.index,
        config=config,
        prepend_bars_requested=prepend,
        zigzag_global_stats=stats,
    )


def _run_fast_for_daily_reset_regression(
    data: pd.DataFrame,
    *,
    trade_filter: TradeFilterConfig | None,
    index=None,
):
    stats = (
        build_zigzag_global_stats(data["close"].values, trade_filter)
        if trade_filter is not None and trade_filter.enabled else None
    )
    return run_backtest_fast(
        data["open"].values,
        data["high"].values,
        data["low"].values,
        data["close"].values,
        5,
        2.0,
        "revers",
        0.001,
        False,
        0.5,
        0,
        ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=trade_filter,
        zigzag_global_stats=stats,
        index=index,
    )


# ===========================================================================
# §9.1  Config / schema
# ===========================================================================

class TestDailyResetConfig:
    def test_daily_reset_default_false(self, tmp_path):
        """YAML без поля → cfg.trade_filter.zigzag.daily_reset == False."""
        path = _write_enabled(tmp_path)
        cfg = load_grid_config(path)
        assert cfg.trade_filter.zigzag.daily_reset is False

    def test_daily_reset_explicit_true_parses(self, tmp_path):
        """daily_reset: true парсится в True."""
        yaml_text = (
            _MINIMAL_BASE
            + "trade_filter:\n"
            + "  enabled: true\n"
            + "  type: zigzag_st_mode\n"
            + "  zigzag:\n"
            + "    reversal_threshold: 0.005\n"
            + "    candidate_trigger_threshold: 0.012\n"
            + "    local_window: 5\n"
            + "    daily_reset: true\n"
            + "  triggers:\n"
            + "    candidate_threshold:\n"
            + "      enabled: true\n"
            + "    confirmed_median:\n"
            + "      enabled: true\n"
            + "  lifecycle:\n"
            + "    freeze_confirmed_legs: 3\n"
            + "    stop_check: confirm_bar_only\n"
            + "    stopping_exit: opposite_st_flip\n"
        )
        p = tmp_path / "cfg.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        cfg = load_grid_config(str(p))
        assert cfg.trade_filter.zigzag.daily_reset is True

    def test_daily_reset_invalid_type_only_rejected_when_enabled_true(self, tmp_path):
        """Invalid type rejected при enabled=true; при enabled=false — нет (инвариант 0.16)."""
        # enabled=true + invalid type → ConfigError
        yaml_enabled = (
            _MINIMAL_BASE + _ENABLED_BLOCK.replace(
                "    local_window: 5\n",
                "    local_window: 5\n    daily_reset: not_a_bool\n",
            )
        )
        p_enabled = tmp_path / "enabled.yaml"
        p_enabled.write_text(yaml_enabled, encoding="utf-8")
        with pytest.raises(ConfigError, match="daily_reset"):
            load_grid_config(str(p_enabled))

        # enabled=false + invalid type → НЕТ ошибки (весь zigzag-блок не валидируется)
        yaml_disabled = (
            _MINIMAL_BASE
            + "trade_filter:\n"
            + "  enabled: false\n"
            + "  type: zigzag_st_mode\n"
            + "  zigzag:\n"
            + "    daily_reset: not_a_bool\n"
        )
        p_disabled = tmp_path / "disabled.yaml"
        p_disabled.write_text(yaml_disabled, encoding="utf-8")
        cfg = load_grid_config(str(p_disabled))
        assert cfg.trade_filter.enabled is False

    def test_daily_reset_in_both_whitelists(self):
        """'daily_reset' присутствует в обоих whitelist'ах синхронно (drift-guard §2.6)."""
        from supertrend_optimizer.core.trade_filter_config import TRADE_FILTER_ALLOWED_KEYS
        from wf_grid.config.loader import _ALLOWED_KEYS  # type: ignore[attr-defined]

        assert "daily_reset" in TRADE_FILTER_ALLOWED_KEYS["trade_filter.zigzag"], (
            "daily_reset missing from TRADE_FILTER_ALLOWED_KEYS"
        )
        assert "daily_reset" in _ALLOWED_KEYS["trade_filter.zigzag"], (
            "daily_reset missing from wf_grid loader _ALLOWED_KEYS"
        )


# ===========================================================================
# §9.2  Helper _infer_daily_reset_event
# Тесты 1-6: адаптации из donor zigzag/tests/core/test_zigzag_filter.py:316-369
# Тесты 7-10: наши (§0.3, §0.4, §0.5, §0.14 edge-cases)
# ===========================================================================

class TestInferDailyResetEvent:

    # --- Заимствовано из донора (адаптировано: bool[] вместо int64[]) -------

    def test_tz_naive_index(self):
        """tz-naive: 23:55 → 00:05 — переход дня виден."""
        idx = pd.DatetimeIndex([
            "2026-04-20 23:55", "2026-04-21 00:05", "2026-04-21 12:00",
        ])
        event = _infer_daily_reset_event(idx, len(idx), enabled=True)
        assert event.dtype == bool
        assert event[0] is np.bool_(False)
        assert event[1] is np.bool_(True)   # новый день
        assert event[2] is np.bool_(False)  # тот же день

    def test_non_datetime_index_raises_when_enabled(self):
        """enabled=True + RangeIndex → ConfigError (§0.4)."""
        idx = pd.RangeIndex(5)
        with pytest.raises(STConfigError, match="DatetimeIndex"):
            _infer_daily_reset_event(idx, 5, enabled=True)

    def test_non_datetime_index_ok_when_disabled(self):
        """enabled=False + RangeIndex → all-False, без падения (§0.5)."""
        idx = pd.RangeIndex(5)
        event = _infer_daily_reset_event(idx, 5, enabled=False)
        assert event.dtype == bool
        assert len(event) == 5
        assert not np.any(event)

    def test_tz_aware_index_moscow_midnight(self):
        """CRITICAL: tz_localize(None) не tz_convert(None).
        23:55 MSK и 00:05 MSK — разные календарные дни в московском времени."""
        idx = pd.DatetimeIndex([
            "2026-04-20 23:55",
            "2026-04-21 00:05",
        ]).tz_localize("Europe/Moscow")
        event = _infer_daily_reset_event(idx, len(idx), enabled=True)
        assert event[1] is np.bool_(True), (
            "MSK wall-clock date crossed — reset required"
        )

    def test_tz_aware_index_utc_midnight(self):
        """UTC midnight: переход дня виден."""
        idx = pd.DatetimeIndex([
            "2026-04-20 23:55",
            "2026-04-21 00:05",
        ]).tz_localize("UTC")
        event = _infer_daily_reset_event(idx, len(idx), enabled=True)
        assert event[1] is np.bool_(True)

    def test_weekend_gap_single_boundary(self):
        """Пятница 20:00 → Понедельник 09:00 — один переход."""
        idx = pd.DatetimeIndex([
            "2026-04-17 20:00",  # Friday
            "2026-04-20 09:00",  # Monday
        ])
        event = _infer_daily_reset_event(idx, len(idx), enabled=True)
        assert event[0] is np.bool_(False)
        assert event[1] is np.bool_(True)

    def test_crypto_24_7_same_day(self):
        """Все бары внутри одного дня → all-False."""
        idx = pd.DatetimeIndex([
            "2026-04-21 00:00",
            "2026-04-21 06:00",
            "2026-04-21 12:00",
            "2026-04-21 18:00",
        ])
        event = _infer_daily_reset_event(idx, len(idx), enabled=True)
        assert not np.any(event)

    # --- Наши тесты (§0.3, §0.5, edge-cases) --------------------------------

    def test_helper_disabled_short_circuit(self):
        """enabled=False: non-monotonic и RangeIndex не вызывают ошибку (§0.5)."""
        # Non-monotonic DatetimeIndex
        non_mono = pd.DatetimeIndex(["2026-04-21", "2026-04-20"])
        event = _infer_daily_reset_event(non_mono, 2, enabled=False)
        assert len(event) == 2
        assert not np.any(event)

        # RangeIndex
        event2 = _infer_daily_reset_event(pd.RangeIndex(3), 3, enabled=False)
        assert len(event2) == 3
        assert not np.any(event2)

    def test_helper_dst_intraday_no_reset(self):
        """DST-переход внутри одного дня → нет reset (одна дата в wall-clock)."""
        # US/Eastern spring-forward 2026-03-08: 01:59 → 03:00
        # Оба бара — одна календарная дата 2026-03-08
        idx = pd.DatetimeIndex([
            "2026-03-08 01:30",
            "2026-03-08 03:30",
        ]).tz_localize("US/Eastern", ambiguous="NaT", nonexistent="NaT")
        event = _infer_daily_reset_event(idx, len(idx), enabled=True)
        assert event[1] is np.bool_(False), (
            "DST intraday — same wall-clock date, no reset expected"
        )

    def test_helper_non_monotonic_raises(self):
        """enabled=True + non-monotonic DatetimeIndex → ConfigError (§0.3)."""
        idx = pd.DatetimeIndex([
            "2026-04-21 00:00",
            "2026-04-20 00:00",  # идёт назад
        ])
        with pytest.raises(STConfigError, match="monotonic"):
            _infer_daily_reset_event(idx, len(idx), enabled=True)

    def test_helper_n0_n1_edge(self):
        """n=0 и n=1 → корректная длина массива, без падений."""
        idx0 = pd.DatetimeIndex([])
        event0 = _infer_daily_reset_event(idx0, 0, enabled=True)
        assert len(event0) == 0
        assert event0.dtype == bool

        idx1 = pd.DatetimeIndex(["2026-04-21"])
        event1 = _infer_daily_reset_event(idx1, 1, enabled=True)
        assert len(event1) == 1
        assert event1[0] is np.bool_(False)


# ===========================================================================
# §9.3  ZigZag close-only pass
# ===========================================================================

def _assert_per_bar_equal(left, right) -> None:
    np.testing.assert_allclose(
        left.candidate_height_pct, right.candidate_height_pct, equal_nan=True
    )
    np.testing.assert_array_equal(left.confirm_event, right.confirm_event)
    np.testing.assert_array_equal(
        left.confirmed_leg_idx_at_t, right.confirmed_leg_idx_at_t
    )
    np.testing.assert_allclose(
        left.last_confirmed_leg_height_pct,
        right.last_confirmed_leg_height_pct,
        equal_nan=True,
    )
    np.testing.assert_allclose(left.local_median_N, right.local_median_N, equal_nan=True)
    np.testing.assert_array_equal(
        left.local_median_available, right.local_median_available
    )


class TestZigZagDailyResetPass:
    def test_zigzag_candidate_does_not_cross_day(self):
        """UP-candidate from day 1 is not confirmed by first day-2 bar (§9.3)."""
        close = np.array([100.0, 110.0, 104.0], dtype=np.float64)

        baseline = compute_zigzag_per_bar(close, 0.05, 1)
        reset = compute_zigzag_per_bar(
            close,
            0.05,
            1,
            daily_reset_event=np.array([False, False, True], dtype=bool),
        )

        assert baseline.confirm_event[2] == 1
        assert reset.confirm_event[2] == 0
        assert reset.confirmed_leg_idx_at_t[2] == -1
        assert np.isnan(reset.candidate_height_pct[2])

    def test_zigzag_legs_history_survives_reset(self):
        """Confirmed legs from day 1 remain available for day-2 local_median_N."""
        close = np.array([100.0, 110.0, 104.0, 104.0, 115.0, 108.0])
        event = np.array([False, False, False, True, False, False], dtype=bool)

        per_bar = compute_zigzag_per_bar(
            close, 0.05, local_window=2, daily_reset_event=event
        )

        first_height = (110.0 - 100.0) / 100.0
        second_height = (115.0 - 104.0) / 104.0
        expected_median = float(np.median([first_height, second_height]))

        assert per_bar.confirm_event[2] == 1
        assert per_bar.confirmed_leg_idx_at_t[3] == 0
        assert per_bar.confirm_event[5] == 1
        assert per_bar.confirmed_leg_idx_at_t[5] == 1
        assert per_bar.local_median_available[5] is np.bool_(True)
        assert per_bar.local_median_N[5] == pytest.approx(expected_median)

    def test_zigzag_pass_disabled_bit_identical(self):
        """daily_reset_event=None, all-zeros and all-False are bit-identical."""
        close = np.array([100.0, 106.0, 100.0, 108.0, 102.0], dtype=np.float64)

        baseline = compute_zigzag_per_bar(close, 0.05, 2)
        zeros_int = compute_zigzag_per_bar(
            close, 0.05, 2, daily_reset_event=np.zeros(len(close), dtype=np.int8)
        )
        all_false = compute_zigzag_per_bar(
            close, 0.05, 2, daily_reset_event=np.zeros(len(close), dtype=bool)
        )

        _assert_per_bar_equal(baseline, zeros_int)
        _assert_per_bar_equal(baseline, all_false)

    def test_zigzag_pass_reset_on_pathological_bar(self):
        """Pathological reset clears in-flight state and next valid bar re-seeds."""
        close = np.array([100.0, 110.0, np.nan, 120.0, 114.0, 121.0])
        event = np.array([False, False, True, False, False, False], dtype=bool)

        baseline = compute_zigzag_per_bar(close, 0.05, 1)
        reset = compute_zigzag_per_bar(close, 0.05, 1, daily_reset_event=event)

        assert baseline.confirm_event[4] == 1
        assert reset.confirm_event[4] == 0
        assert reset.confirm_event[5] == 1
        assert reset.local_median_available[5] is np.bool_(True)
        assert reset.local_median_N[5] == pytest.approx((120.0 - 114.0) / 120.0)


# ===========================================================================
# §9.4  FSM apply()
# ===========================================================================

class TestDailyResetFSM:
    def test_fsm_wait_resets_on_new_day(self):
        n = 4
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan]),
        )
        result = _run_apply(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, True, False]),
        )

        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[1] == "WAIT_FIRST_ST_FLIP"
        assert states[2] == "OFF"
        assert result.filter_diagnostics["confirmed_legs_since_start"][2] == -1

    def test_fsm_freeze_resets_on_new_day(self):
        n = 4
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan]),
        )
        result = _run_apply(
            trend=np.array([-1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, True, False]),
            cfg=_make_filter_cfg(freeze_confirmed_legs=5),
        )

        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[1] == "ST_ACTIVE_FREEZE"
        assert states[2] == "OFF"
        assert result.positions[2] == 1
        assert result.positions[3] == 0
        assert result.filter_diagnostics["confirmed_legs_since_start"][2] == -1

    def test_fsm_monitoring_resets_on_new_day(self):
        n = 4
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan]),
        )
        result = _run_apply(
            trend=np.array([-1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, True, False]),
            cfg=_make_filter_cfg(freeze_confirmed_legs=0),
        )

        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[1] == "ST_ACTIVE_MONITORING"
        assert states[2] == "OFF"
        assert result.positions[2] == 1
        assert result.positions[3] == 0

    def test_fsm_stopping_resets_on_new_day(self):
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 0], dtype=np.int8),
            local_median_N=np.array([np.nan, np.nan, 0.01, np.nan, np.nan]),
            local_median_available=np.array([False, False, True, False, False]),
        )
        result = _run_apply(
            trend=np.array([-1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, False, True, False]),
            cfg=_make_filter_cfg(freeze_confirmed_legs=0),
            stats=_make_global_stats(global_median=0.05),
        )

        diag = result.filter_diagnostics
        states = list(diag["trade_filter_state"])
        assert states[2] == "ST_STOPPING"
        assert states[3] == "OFF"
        assert diag["confirmed_legs_since_start"][3] == -1
        assert diag["stopping_started_at_index"][3] == -1
        assert result.positions[3] == 1
        assert result.positions[4] == 0

    def test_fsm_position_flat_from_next_bar(self):
        n = 4
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan]),
        )
        result = _run_apply(
            trend=np.array([-1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, True, False]),
            cfg=_make_filter_cfg(freeze_confirmed_legs=0),
        )

        assert result.positions[2] == 1
        assert result.positions[3] == 0

    def test_fsm_simultaneous_reset_and_trigger(self):
        """Per ТЗ v3 §9.2: reset bar forces all candidate/B/immediate primitives
        to false; no mode-specific OFF transition fires on a reset bar.  End-of-
        bar state must remain OFF even if the candidate height would otherwise
        clear the threshold.  Counter stays at the post-reset value (-1)."""
        n = 3
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, np.nan, 0.06]),
        )
        result = _run_apply(
            trend=np.array([0, -1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, True]),
            cfg=_make_filter_cfg(freeze_confirmed_legs=5),
        )

        states = list(result.filter_diagnostics["trade_filter_state"])
        # v3 contract: trigger on reset bar is suppressed (primitives forced false)
        assert states[2] == "OFF"
        assert result.filter_diagnostics["confirmed_legs_since_start"][2] == -1
        # Trigger source must be "none" — no OFF departure happened on reset bar
        assert result.filter_diagnostics["trade_filter_trigger_source"][2] == "none"

    def test_fsm_apply_test_override_daily_reset_event(self):
        n = 3
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan]),
        )
        result = apply(
            trend=np.zeros(n, dtype=np.int64),
            trade_mode="both",
            trade_filter_config=_make_filter_cfg(daily_reset=True),
            zigzag_global_stats=_make_global_stats(),
            per_bar=per_bar,
            index=pd.RangeIndex(n),
            daily_reset_event=np.array([False, False, True]),
        )

        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[1] == "WAIT_FIRST_ST_FLIP"
        assert states[2] == "OFF"

    def test_apply_daily_reset_event_override_list_normalized(self):
        n = 3
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan]),
        )
        result = _run_apply(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=[False, False, True],
        )

        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[2] == "OFF"

    def test_apply_daily_reset_event_override_2d_raises(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        with pytest.raises(STConfigError, match="1-D"):
            _run_apply(
                trend=np.zeros(n, dtype=np.int64),
                per_bar=per_bar,
                daily_reset_event=np.zeros((1, n), dtype=bool),
            )

    def test_apply_daily_reset_event_override_wrong_length_raises(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        with pytest.raises(STConfigError, match="length"):
            _run_apply(
                trend=np.zeros(n, dtype=np.int64),
                per_bar=per_bar,
                daily_reset_event=np.zeros(n - 1, dtype=bool),
            )

    def test_apply_pre_computed_n_matches_validated_n(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        result = _run_apply(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )

        assert len(result.positions) == n

    def test_apply_pre_computed_n_mismatch_raises_bug(self):
        close = np.array([100.0, 101.0, 102.0, 103.0], dtype=np.float64)
        trend = np.zeros(3, dtype=np.int64)

        with pytest.raises(STConfigError, match=r"\[BUG\].*pre-computed n"):
            apply(
                close=close,
                trend=trend,
                trade_mode="both",
                trade_filter_config=_make_filter_cfg(daily_reset=False),
                zigzag_global_stats=_make_global_stats(),
            )


# ===========================================================================
# §9.5  Diagnostics
# ===========================================================================

class TestDailyResetDiagnostics:
    def test_filter_diagnostics_keyset_invariant_under_default(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        result = _run_apply(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )

        diag = result.filter_diagnostics
        assert "daily_reset_enabled" in diag
        assert "daily_reset_event" in diag
        assert set(diag.keys()) == {
            "trade_filter_state",
            "trade_filter_state_code",
            "trade_filter_trigger_source",
            "confirmed_legs_since_start",
            "st_flip_dir",
            "trade_filter_enabled",
            "zigzag_reversal_threshold",
            "candidate_height_pct",
            "candidate_trigger_threshold",
            "local_median_N",
            "local_median_available",
            "local_window",
            "global_median",
            "global_stats_available",
            "freeze_confirmed_legs",
            "median_stop_triggered",
            "stopping_started_at_index",
            "filter_allowed_entry",
            "filter_block_reason",
            "daily_reset_enabled",
            "daily_reset_event",
            # v3 WP-V3-3: per-bar candidate state
            "candidate_age_bars",
            "candidate_leg_direction",
            # v3 WP-V3-4: runtime primitives + immutable bar-start snapshots
            "candidate_threshold_ok",
            "candidate_component_ok",
            "confirmed_median_ok",
            "b_component_ok",
            "immediate_allowed",
            "candidate_duration_gate_passed",
            "state_at_bar_start",
            "held_pos_at_bar_start",
            "confirmed_legs_at_bar_start",
            # v3 WP-V3-7: §10.2 immediate diagnostics
            "zigzag_mode",
            "candidate_duration_gate_enabled",
            "candidate_duration_max_bars",
            "immediate_candidate_entry_used",
            "immediate_candidate_entry_block_reason",
        }

    def test_daily_reset_arrays_dtype_int8(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        result = _run_apply(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, True, False]),
            cfg=_make_filter_cfg(daily_reset=True),
        )

        diag = result.filter_diagnostics
        assert diag["daily_reset_enabled"].dtype == np.int8
        assert diag["daily_reset_event"].dtype == np.int8
        np.testing.assert_array_equal(diag["daily_reset_enabled"], np.ones(n, dtype=np.int8))
        np.testing.assert_array_equal(
            diag["daily_reset_event"], np.array([0, 1, 0], dtype=np.int8)
        )

    def test_daily_reset_count_in_summary(self):
        diag = {
            "daily_reset_event": np.array([0, 1, 0, 1], dtype=np.int8),
        }

        summary = _compute_filter_diagnostics_summary(diag)
        assert summary is not None
        assert summary["daily_reset_count"] == 2

    def test_tester_summary_counters_daily_reset_count(self):
        diag = {
            "filter_allowed_entry": np.zeros(4, dtype=np.int8),
            "filter_block_reason": np.array(["none"] * 4, dtype=object),
            "trade_filter_state": np.array(["OFF"] * 4, dtype=object),
            "median_stop_triggered": np.zeros(4, dtype=np.int8),
            "daily_reset_event": np.array([0, 1, 0, 1], dtype=np.int8),
        }

        counters = _compute_summary_counters(
            positions_raw=np.zeros(4, dtype=np.int8),
            positions_filtered=np.zeros(4, dtype=np.int8),
            filter_diagnostics=diag,
            trades_df=None,
        )
        assert counters["daily_reset_count"] == 2

    def test_attach_trade_diagnostics_exit_reason_filter_daily_reset(self):
        trades = pd.DataFrame(
            {
                "entry_index": [1],
                "exit_index": [3],
            }
        )
        diag = {
            "trade_filter_state": np.array(
                ["ST_ACTIVE_MONITORING"] * 5, dtype=object
            ),
            "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
            "daily_reset_event": np.array([0, 0, 1, 0, 0], dtype=np.int8),
        }

        enriched = attach_trade_filter_diagnostics(trades, diag)
        assert enriched["exit_reason"].iloc[0] == "filter_daily_reset"

    def test_attach_trade_diagnostics_final_slot_reset_beats_pending(self):
        trades = pd.DataFrame(
            {
                "entry_index": [3],
                "exit_index": [4],
            }
        )
        diag = {
            "trade_filter_state": np.array(
                ["OFF", "WAIT_FIRST_ST_FLIP", "ST_ACTIVE_FREEZE", "OFF", "OFF"],
                dtype=object,
            ),
            "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
            "daily_reset_event": np.array([0, 0, 0, 1, 0], dtype=np.int8),
        }

        enriched = attach_trade_filter_diagnostics(trades, diag)
        assert enriched["exit_reason"].iloc[0] == "filter_daily_reset"

    def test_attach_trade_diagnostics_final_slot_without_reset_stays_pending(self):
        trades = pd.DataFrame(
            {
                "entry_index": [3],
                "exit_index": [4],
            }
        )
        diag = {
            "trade_filter_state": np.array(
                ["OFF", "WAIT_FIRST_ST_FLIP", "ST_ACTIVE_FREEZE", "OFF", "OFF"],
                dtype=object,
            ),
            "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
            "daily_reset_event": np.zeros(5, dtype=np.int8),
        }

        enriched = attach_trade_filter_diagnostics(trades, diag)
        assert enriched["exit_reason"].iloc[0] == "pending_open_trade_at_end"

    def test_filter_block_reason_daily_reset_priority(self):
        n = 3
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, np.nan, 0.06]),
        )
        result = _run_apply(
            trend=np.array([0, -1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, True]),
            cfg=_make_filter_cfg(freeze_confirmed_legs=5),
        )

        assert result.filter_diagnostics["filter_block_reason"][2] == "daily_reset"

    def test_attach_trade_diagnostics_priority_chain(self):
        trades = pd.DataFrame(
            {
                "entry_index": [1, 1],
                "exit_index": [3, 4],
            }
        )
        diag = {
            "trade_filter_state": np.array(
                [
                    "ST_ACTIVE_MONITORING",
                    "ST_ACTIVE_MONITORING",
                    "ST_STOPPING",
                    "ST_STOPPING",
                    "ST_STOPPING",
                    "OFF",
                ],
                dtype=object,
            ),
            "trade_filter_trigger_source": np.array(["none"] * 6, dtype=object),
            "daily_reset_event": np.array([0, 0, 1, 0, 0, 0], dtype=np.int8),
        }

        enriched = attach_trade_filter_diagnostics(trades, diag)
        assert list(enriched["exit_reason"]) == [
            "filter_daily_reset",
            "filter_stopping_opposite_flip",
        ]


# ===========================================================================
# §9.6  OOS prepend / trim
# ===========================================================================

class TestDailyResetOOSPrepend:
    def test_oos_prepend_diag_lengths_after_trim(self):
        step = _run_daily_reset_oos_step(prepend=5)
        assert step.filter_diagnostics_oos is not None
        expected = len(step.filter_diagnostics_oos["daily_reset_event"])

        assert expected == len(step.filter_diagnostics_oos["trade_filter_state"])
        for key, arr in step.filter_diagnostics_oos.items():
            assert len(arr) == expected, (
                f"filter_diagnostics_oos[{key!r}] len={len(arr)} != {expected}"
            )
        assert expected == step.effective_oos_bars + 1

    def test_oos_prepend_reset_on_boundary(self):
        step = _run_daily_reset_oos_step(prepend=5)
        assert step.prepend_bars_applied == 5
        assert step.filter_diagnostics_oos is not None

        dre = step.filter_diagnostics_oos["daily_reset_event"]
        assert int(dre[0]) == 1
        assert step.filter_diagnostics_summary is not None
        assert step.filter_diagnostics_summary["daily_reset_count"] >= 1

    def test_oos_prepend_zero_no_reset_on_first_bar(self):
        step = _run_daily_reset_oos_step(prepend=0)
        assert step.prepend_bars_applied == 0
        assert step.filter_diagnostics_oos is not None

        dre = step.filter_diagnostics_oos["daily_reset_event"]
        assert int(dre[0]) == 0

    def test_oos_prepend_carry_in_closed_by_reset(self):
        n = 6
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan, np.nan]),
        )
        result = _run_apply(
            trend=np.array([0, -1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, False, True, False, False]),
            cfg=_make_filter_cfg(daily_reset=True, freeze_confirmed_legs=0),
        )
        returns = np.zeros(n - 1, dtype=np.float64)
        prices = np.linspace(100.0, 105.0, n, dtype=np.float64)
        ext_trades_df = extract_trades(
            positions=result.positions,
            returns=returns,
            execution_prices=prices,
            index=pd.RangeIndex(n),
            commission_rate=0.0,
            trend=np.array([0, -1, 1, 1, 1, 1], dtype=np.int64),
            execution_model="open_to_open",
        )
        ext_trades_df = attach_trade_filter_diagnostics(
            ext_trades_df,
            result.filter_diagnostics,
        )
        assert len(ext_trades_df) == 1
        assert ext_trades_df["entry_index"].iloc[0] == 3
        assert ext_trades_df["exit_index"].iloc[0] == 4
        assert ext_trades_df["exit_reason"].iloc[0] == "filter_daily_reset"

        oos_boundary = 4
        oos_returns = returns[oos_boundary:]
        oos_positions = result.positions[oos_boundary:]
        oos_equity = np.ones(len(oos_positions), dtype=np.float64)

        returns_out, positions_out, equity_out = _apply_oos_force_flat(
            oos_returns=oos_returns,
            oos_positions=oos_positions,
            oos_equity=oos_equity,
            ext_trades_df=ext_trades_df,
            oos_boundary=oos_boundary,
        )

        np.testing.assert_array_equal(returns_out, oos_returns)
        np.testing.assert_array_equal(positions_out, oos_positions)
        np.testing.assert_array_equal(equity_out, oos_equity)

    def test_daily_reset_event_monotonic_per_step(self):
        data = _make_wf_ohlc()
        for test_start, test_end, prepend in ((20, 35, 5), (35, 50, 7)):
            ext_start = test_start - prepend
            ext_index = data.index[ext_start:test_end]
            normalized = ext_index.tz_localize(None).normalize()
            days = normalized.astype("int64").to_numpy()
            assert np.all(days[1:] >= days[:-1])

            event = _infer_daily_reset_event(ext_index, len(ext_index), enabled=True)
            assert event.dtype == bool
            assert event[0] is np.bool_(False)
            assert event[prepend] is np.bool_(True)


# ===========================================================================
# §9.7  Regression / compatibility
# ===========================================================================

class TestDailyResetRegressionCompatibility:
    def test_run_backtest_fast_index_default_None(self):
        data = _make_wf_ohlc()

        disabled = run_backtest_fast(
            data["open"].values,
            data["high"].values,
            data["low"].values,
            data["close"].values,
            5,
            2.0,
            "revers",
            0.001,
            False,
            0.5,
            0,
            ExecutionModel.OPEN_TO_OPEN,
        )
        assert disabled.filter_diagnostics is None

        cfg = _make_daily_reset_trade_filter(daily_reset=False)
        stats = build_zigzag_global_stats(data["close"].values, cfg)
        enabled = run_backtest_fast(
            data["open"].values,
            data["high"].values,
            data["low"].values,
            data["close"].values,
            5,
            2.0,
            "revers",
            0.001,
            False,
            0.5,
            0,
            ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
        )
        assert enabled.filter_diagnostics is not None
        assert np.all(enabled.filter_diagnostics["daily_reset_event"] == 0)

    def test_run_backtest_fast_daily_reset_true_uses_index(self):
        data = _make_wf_ohlc()
        cfg = _make_daily_reset_trade_filter(daily_reset=True)
        stats = build_zigzag_global_stats(data["close"].values, cfg)

        result = run_backtest_fast(
            data["open"].values,
            data["high"].values,
            data["low"].values,
            data["close"].values,
            5,
            2.0,
            "revers",
            0.001,
            False,
            0.5,
            0,
            ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            index=data.index,
        )

        diag = result.filter_diagnostics
        assert diag is not None
        assert np.all(diag["daily_reset_enabled"] == 1)
        assert int(np.sum(diag["daily_reset_event"] == 1)) > 0

    def test_apply_with_index_None_and_daily_reset_false(self):
        n = 3
        result = apply(
            trend=np.zeros(n, dtype=np.int64),
            trade_mode="both",
            trade_filter_config=_make_filter_cfg(daily_reset=False),
            zigzag_global_stats=_make_global_stats(),
            per_bar=_make_per_bar(n=n),
            index=None,
        )

        assert len(result.positions) == n
        assert np.all(result.filter_diagnostics["daily_reset_event"] == 0)

    def test_apply_with_index_None_and_daily_reset_true_raises(self):
        n = 3
        with pytest.raises(STConfigError, match="DatetimeIndex"):
            apply(
                trend=np.zeros(n, dtype=np.int64),
                trade_mode="both",
                trade_filter_config=_make_filter_cfg(daily_reset=True),
                zigzag_global_stats=_make_global_stats(),
                per_bar=_make_per_bar(n=n),
                index=None,
            )

    def test_baseline_bit_identity_when_daily_reset_false(self):
        data = _make_wf_ohlc()
        cfg = _make_daily_reset_trade_filter(daily_reset=False)

        with_index = _run_fast_for_daily_reset_regression(
            data, trade_filter=cfg, index=data.index
        )
        without_index = _run_fast_for_daily_reset_regression(
            data, trade_filter=cfg, index=None
        )

        np.testing.assert_array_equal(with_index.positions, without_index.positions)
        np.testing.assert_allclose(with_index.returns, without_index.returns)
        np.testing.assert_allclose(with_index.equity_curve, without_index.equity_curve)
        np.testing.assert_array_equal(with_index.trend, without_index.trend)

        assert with_index.filter_diagnostics is not None
        assert without_index.filter_diagnostics is not None
        for key in with_index.filter_diagnostics:
            left = with_index.filter_diagnostics[key]
            right = without_index.filter_diagnostics[key]
            if np.issubdtype(np.asarray(left).dtype, np.floating):
                np.testing.assert_allclose(left, right, equal_nan=True)
            else:
                np.testing.assert_array_equal(left, right)
