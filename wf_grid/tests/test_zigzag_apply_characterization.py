from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import math
import os

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    apply,
    build_zigzag_global_stats,
)
from supertrend_optimizer.core.calculator import calculate_supertrend
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel
from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary


SNAPSHOT_DIR = Path(__file__).with_name("golden_snapshots")


@dataclass
class _Toggle:
    enabled: bool = True


@dataclass
class _Triggers:
    candidate_threshold: _Toggle = field(default_factory=_Toggle)
    confirmed_median: _Toggle = field(default_factory=lambda: _Toggle(enabled=True))


@dataclass
class _DurationGate:
    enabled: bool = False
    max_bars: int | None = None


@dataclass
class _ZigZagCfg:
    enabled: bool = True
    mode: str = "A"
    reversal_threshold: float = 0.018
    candidate_trigger_threshold: float = 0.012
    candidate_trigger_quantile: float | None = None
    global_stats_source: str = "full_dataset"
    leg_height_mode: str = "pct"
    global_median: str = "auto"
    local_window: int = 3
    daily_reset: bool = False
    candidate_duration_gate: _DurationGate = field(default_factory=_DurationGate)


@dataclass
class _Lifecycle:
    freeze_confirmed_legs: int = 1
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"
    exit_off_mode: str = "exit A"
    exit_off_zz_leg_count: int | None = None
    exit_b_immediate_off: bool = False


@dataclass
class _TimeFilter:
    enabled: bool = False
    window: str | None = None
    _start_hour: int = 0
    _start_minute: int = 0
    _end_hour: int = 23
    _end_minute: int = 59


@dataclass
class _TradeFilter:
    enabled: bool = True
    type: str = "zigzag_st_mode"
    zigzag: _ZigZagCfg = field(default_factory=_ZigZagCfg)
    triggers: _Triggers = field(default_factory=_Triggers)
    lifecycle: _Lifecycle = field(default_factory=_Lifecycle)
    time_filter: _TimeFilter = field(default_factory=_TimeFilter)


def _case_config(case_id: str) -> _TradeFilter:
    cfg = _TradeFilter()
    if case_id == "f1_mode_a_candidate":
        cfg.zigzag.mode = "A"
    elif case_id == "f2_mode_b_confirmed":
        cfg.zigzag.mode = "B"
    elif case_id == "f3_mode_c_immediate":
        cfg.zigzag.mode = "C"
        cfg.lifecycle.freeze_confirmed_legs = 0
    elif case_id == "f4_mode_ab_both":
        cfg.zigzag.mode = "A+B"
    elif case_id == "f5_mode_cb_rescue":
        cfg.zigzag.mode = "C+B"
        cfg.lifecycle.freeze_confirmed_legs = 0
    elif case_id == "f6_exit_b_counting":
        cfg.zigzag.mode = "A"
        cfg.lifecycle.exit_off_mode = "exit B"
        cfg.lifecycle.exit_off_zz_leg_count = 2
    elif case_id == "f7_daily_reset":
        cfg.zigzag.mode = "A+B"
        cfg.zigzag.daily_reset = True
    elif case_id == "f8_time_filter_reset":
        cfg.zigzag.mode = "A+B"
        cfg.time_filter = _TimeFilter(
            enabled=True,
            window="09:00-16:00",
            _start_hour=9,
            _start_minute=0,
            _end_hour=16,
            _end_minute=0,
        )
    else:
        raise KeyError(case_id)
    return cfg


CASES = (
    "f1_mode_a_candidate",
    "f2_mode_b_confirmed",
    "f3_mode_c_immediate",
    "f4_mode_ab_both",
    "f5_mode_cb_rescue",
    "f6_exit_b_counting",
    "f7_daily_reset",
    "f8_time_filter_reset",
)


def _ohlc_for_case(case_id: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DatetimeIndex]:
    n = 72
    base = 100.0 + np.sin(np.arange(n) / 2.2) * 4.5
    swing = ((np.arange(n) % 12) - 6).astype(np.float64) * 0.75
    close = base + swing
    if case_id in {"f3_mode_c_immediate", "f5_mode_cb_rescue"}:
        close = 100.0 + np.sin(np.arange(n) / 1.8) * 7.0
    elif case_id == "f6_exit_b_counting":
        close = 100.0 + np.sin(np.arange(n) / 1.5) * 6.0
    elif case_id == "f7_daily_reset":
        close = 100.0 + np.sin(np.arange(n) / 1.9) * 5.5
    elif case_id == "f8_time_filter_reset":
        close = 100.0 + np.sin(np.arange(n) / 2.0) * 5.0

    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    index = pd.date_range("2026-01-01 09:00", periods=n, freq="h")
    return open_, high, low, close, index


def _stats_for_case(close: np.ndarray, cfg: _TradeFilter) -> ZigZagGlobalStats:
    stats = build_zigzag_global_stats(close=close, trade_filter_config=cfg)
    return ZigZagGlobalStats(
        reversal_threshold=stats.reversal_threshold,
        global_stats_source=stats.global_stats_source,
        leg_height_mode=stats.leg_height_mode,
        confirmed_legs=stats.confirmed_legs,
        confirmed_heights_pct=stats.confirmed_heights_pct,
        global_median=stats.global_median,
        candidate_trigger_threshold=stats.candidate_trigger_threshold,
        candidate_trigger_source=stats.candidate_trigger_source,
        candidate_trigger_quantile=stats.candidate_trigger_quantile,
        n_legs_total=stats.n_legs_total,
        insufficient_data=stats.insufficient_data,
        fail_closed_reason=stats.fail_closed_reason,
        metadata=stats.metadata,
        zigzag_mode=cfg.zigzag.mode,
        candidate_duration_gate_enabled=False,
        candidate_duration_max_bars=None,
    )


def _run_case(case_id: str) -> dict:
    cfg = _case_config(case_id)
    open_, high, low, close, index = _ohlc_for_case(case_id)
    stats = _stats_for_case(close, cfg)
    result = run_single_backtest(
        open_prices=open_,
        high=high,
        low=low,
        close=close,
        index=index,
        atr_period=5,
        multiplier=1.6,
        trade_mode="revers",
        commission=0.0005,
        warmup_period=0,
        periods_per_year=252.0,
        min_trades_required=0,
        extract_trades_flag=True,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
    )
    trades_df = result.trades_df
    if trades_df is None or trades_df.empty:
        trades = []
    else:
        trades = [
            {
                "entry_index": int(row.entry_index),
                "exit_index": int(row.exit_index),
                "net_pnl_pct": _float_to_json(float(row.net_pnl_pct)),
            }
            for row in trades_df[["entry_index", "exit_index", "net_pnl_pct"]].itertuples()
        ]

    return {
        "positions": [int(x) for x in result.positions.tolist()],
        "returns": [_float_to_json(float(x)) for x in result.returns.tolist()],
        "equity_curve": [_float_to_json(float(x)) for x in result.equity_curve.tolist()],
        "filter_diagnostics_summary": _jsonify_summary(
            _compute_filter_diagnostics_summary(result.filter_diagnostics)
        ),
        "trades": trades,
    }


def _diagnostics_fingerprint_for_case(case_id: str) -> tuple[str, dict[str, str], dict[str, tuple[int, ...]]]:
    cfg = _case_config(case_id)
    open_, high, low, close, index = _ohlc_for_case(case_id)
    stats = _stats_for_case(close, cfg)
    result = run_single_backtest(
        open_prices=open_,
        high=high,
        low=low,
        close=close,
        index=index,
        atr_period=5,
        multiplier=1.6,
        trade_mode="revers",
        commission=0.0005,
        warmup_period=0,
        periods_per_year=252.0,
        min_trades_required=0,
        extract_trades_flag=True,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
    )
    diagnostics = result.filter_diagnostics
    assert diagnostics is not None

    digest = hashlib.sha256()
    digest.update(str(len(diagnostics)).encode("utf-8"))
    dtypes: dict[str, str] = {}
    shapes: dict[str, tuple[int, ...]] = {}
    for key in sorted(diagnostics):
        arr = np.asarray(diagnostics[key])
        dtypes[key] = str(arr.dtype)
        shapes[key] = tuple(arr.shape)
        digest.update(key.encode("utf-8"))
        digest.update(dtypes[key].encode("utf-8"))
        digest.update(str(shapes[key]).encode("utf-8"))
        if arr.dtype == object or arr.dtype.kind in {"O", "U", "S"}:
            payload = "\x1e".join(map(str, arr.tolist())).encode("utf-8")
        else:
            payload = np.ascontiguousarray(arr).view(np.uint8).tobytes()
        digest.update(payload)
    return digest.hexdigest(), dtypes, shapes


def _run_apply_case(case_id: str, *, collect_filter_diagnostics: bool = True):
    cfg = _case_config(case_id)
    open_, high, low, close, index = _ohlc_for_case(case_id)
    stats = _stats_for_case(close, cfg)
    trend, _ = calculate_supertrend(high, low, close, atr_period=5, multiplier=1.6)
    return apply(
        trend=trend,
        trade_mode="revers",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        close=close,
        high=high,
        low=low,
        open_prices=open_,
        index=index,
        collect_filter_diagnostics=collect_filter_diagnostics,
    )


def _float_to_json(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value.hex()


def _float_from_json(value: str) -> float:
    if value == "nan":
        return float("nan")
    if value == "inf":
        return float("inf")
    if value == "-inf":
        return float("-inf")
    return float.fromhex(value)


def _jsonify_summary(value):
    if isinstance(value, dict):
        return {str(k): _jsonify_summary(v) for k, v in sorted(value.items())}
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        return _float_to_json(float(value))
    if value is None or isinstance(value, str):
        return value
    return value


def _load_snapshot(case_id: str) -> dict:
    return json.loads((SNAPSHOT_DIR / f"{case_id}.json").read_text(encoding="utf-8"))


def _write_snapshots() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for case_id in CASES:
        (SNAPSHOT_DIR / f"{case_id}.json").write_text(
            json.dumps(_run_case(case_id), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize("case_id", CASES)
def test_zigzag_apply_characterization_matches_golden_snapshot(case_id):
    if os.environ.get("UPDATE_ZIGZAG_APPLY_GOLDENS") == "1":
        _write_snapshots()

    expected = _load_snapshot(case_id)
    actual = _run_case(case_id)

    np.testing.assert_array_equal(
        np.asarray(actual["positions"], dtype=np.int8),
        np.asarray(expected["positions"], dtype=np.int8),
    )
    np.testing.assert_array_equal(
        np.asarray([_float_from_json(v) for v in actual["returns"]], dtype=np.float64),
        np.asarray([_float_from_json(v) for v in expected["returns"]], dtype=np.float64),
    )
    np.testing.assert_array_equal(
        np.asarray([_float_from_json(v) for v in actual["equity_curve"]], dtype=np.float64),
        np.asarray([_float_from_json(v) for v in expected["equity_curve"]], dtype=np.float64),
    )
    assert actual["filter_diagnostics_summary"] == expected["filter_diagnostics_summary"]
    assert actual["trades"] == expected["trades"]


def test_zigzag_apply_diagnostics_keyset_dtypes_and_values_stable():
    digest, dtypes, shapes = _diagnostics_fingerprint_for_case("f8_time_filter_reset")

    assert len(dtypes) == 46
    assert set(dtypes) == {
        "b_component_ok",
        "candidate_age_bars",
        "candidate_component_ok",
        "candidate_duration_gate_enabled",
        "candidate_duration_gate_passed",
        "candidate_duration_max_bars",
        "candidate_height_pct",
        "candidate_leg_direction",
        "candidate_threshold_ok",
        "candidate_trigger_threshold",
        "confirmed_legs_at_bar_start",
        "confirmed_legs_since_start",
        "confirmed_median_ok",
        "daily_reset_enabled",
        "daily_reset_event",
        "exit_b_immediate_off_config",
        "exit_b_immediate_off_triggered",
        "exit_off_mode",
        "exit_off_zz_leg_count",
        "filter_allowed_entry",
        "filter_block_reason",
        "freeze_confirmed_legs",
        "global_median",
        "global_stats_available",
        "held_pos_at_bar_start",
        "immediate_allowed",
        "immediate_candidate_entry_block_reason",
        "immediate_candidate_entry_used",
        "local_median_N",
        "local_median_available",
        "local_window",
        "median_stop_triggered",
        "st_flip_dir",
        "state_at_bar_start",
        "stopping_started_at_index",
        "time_filter_enabled",
        "time_filter_in_window",
        "time_filter_reset_event",
        "trade_filter_enabled",
        "trade_filter_state",
        "trade_filter_state_code",
        "trade_filter_trigger_source",
        "zigzag_mode",
        "zigzag_reversal_threshold",
        "zz_leg_stop_triggered",
        "zz_legs_since_lifecycle_start",
    }
    assert all(shape == (72,) for shape in shapes.values())
    assert digest == "3934356f91e70621f688f0a2c5963425f061d87f65a357394360de6e4d50398d"


def test_zigzag_apply_collect_filter_diagnostics_false_returns_none_and_same_positions():
    enabled = _run_apply_case("f8_time_filter_reset", collect_filter_diagnostics=True)
    disabled = _run_apply_case("f8_time_filter_reset", collect_filter_diagnostics=False)

    assert enabled.filter_diagnostics is not None
    assert disabled.filter_diagnostics is None
    np.testing.assert_array_equal(enabled.positions, disabled.positions)


def test_try_lifecycle_start_from_off_helper_exists_and_is_callable():
    import supertrend_optimizer.core.zigzag_st_filter as zz

    helper = getattr(zz, "_try_lifecycle_start_from_off", None)
    assert callable(helper)
