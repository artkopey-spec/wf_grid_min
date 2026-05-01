"""
Tests for ZigZag integration in io/excel_tester.py (plan §3.8).

Covers:
  - _ZZ_ENUM_LABELS structure and values (§3.8.1a)
  - SIGNALS_DISPLAY_NAMES contains all 9 zz_* keys (§3.8.1)
  - TRADES_DISPLAY_NAMES contains triggering leg keys (§3.8.3)
  - _link_trades_to_legs: correct mapping shot_bar → trade_id (§3.8.3a)
  - _link_trades_to_legs: multiple-match warning, no linkage (§3.8.3a)
  - _write_legs_sheet: produces correct columns (§3.8.2)
  - _write_filters_summary_sheet: zz params and aggregates present (§3.8.4)
  - Non-zz modes do not get zz columns in filters_summary
"""
from __future__ import annotations

import dataclasses
import io
import warnings

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.io.excel_tester import (
    SIGNALS_DISPLAY_NAMES,
    TRADES_DISPLAY_NAMES,
    _ZZ_ENUM_LABELS,
    _link_trades_to_legs,
    _write_legs_sheet,
    _write_filters_summary_sheet,
)
from supertrend_optimizer.core.zigzag_filter import (
    LegRecord,
    FIRED_YES_SHOT,
    FIRED_NO_NEW_PIVOT,
    FIRED_SESSION_RESET,
    LEG_DIR_UP,
    LEG_DIR_DOWN,
    ARMED_SIDE_LONG,
    ARMED_SIDE_SHORT,
    REGIME_OPEN_ACTIVE,
    REGIME_CLOSED,
)
from supertrend_optimizer.utils.enums import ExecutionModel


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_leg(
    leg_id: int = 0,
    start_bar: int = 0,
    end_bar: int = 5,
    confirm_bar: int = 7,
    direction: int = LEG_DIR_UP,
    height_pct: float = 0.02,
    armed_side: int = ARMED_SIDE_SHORT,
    arm_bar: int = 7,
    fired: int = FIRED_YES_SHOT,
    shot_bar: int = 10,
    trade_id_if_fired=None,
    regime_state_at_confirm: int = REGIME_OPEN_ACTIVE,
    global_median_at_confirm: float = 0.015,
) -> LegRecord:
    return LegRecord(
        leg_id=leg_id,
        start_bar=start_bar,
        end_bar=end_bar,
        confirm_bar=confirm_bar,
        start_price=100.0,
        end_price=102.0,
        direction=direction,
        height_pct=height_pct,
        length_bars=end_bar - start_bar,
        confirm_lag_bars=confirm_bar - end_bar,
        n_legs_before=leg_id,
        global_median_at_confirm=global_median_at_confirm,
        global_p80_at_confirm=0.03,
        local_median_at_confirm=0.018,
        regime_state_at_confirm=regime_state_at_confirm,
        opened_regime=False,
        closed_regime=False,
        is_strong=True,
        armed_side=armed_side,
        arm_bar=arm_bar,
        fired=fired,
        shot_bar=shot_bar,
        trade_id_if_fired=trade_id_if_fired,
    )


def _make_trades_df(*entries) -> pd.DataFrame:
    """entries: list of (trade_id, entry_index) tuples."""
    rows = []
    for tid, ei in entries:
        rows.append({
            "trade_id": tid,
            "entry_index": ei,
            "entry_time": pd.Timestamp("2024-01-01"),
            "entry_price": 100.0,
            "exit_time": pd.Timestamp("2024-01-02"),
            "exit_index": ei + 5,
            "exit_price": 101.0,
            "direction": "LONG",
            "bars_held": 5,
            "gross_pnl_pct": 1.0,
            "commission_pct": 0.03,
            "net_pnl_pct": 0.97,
        })
    return pd.DataFrame(rows)


def _make_zz_fd(n: int = 100, with_legs: bool = True) -> dict:
    legs = (_make_leg(0, shot_bar=40),) if with_legs else ()
    return {
        "mode": "zigzag",
        "thresholds": {
            "zigzag": {
                "reversal_threshold": 0.005,
                "min_legs_global": 5,
                "q_strong": 0.80,
                "k_local": 5,
                "entry_side": "counter_trend",
                "arm_timeout_bars_since_extreme": 24,
                "arm_timeout_bars_hard": 78,
            },
            "global_volume_ma_mean": None,
        },
        "counters": {
            "raw_entry_signals": 10,
            "passed_entry_signals": 3,
            "blocked_entry_signals": 7,
            "blocked_by_volatility": 7,
            "blocked_by_volume": 0,
            "blocked_by_both": 0,
            "blocked_by_vol_ma_invalid": 0,
        },
        "zz_legs": legs,
        "zz_regime_state": np.zeros(n, dtype=np.int8),
        "filtered_reason": np.full(n, "zz_warmup", dtype=object),
        "allow_entry": np.zeros(n, dtype=bool),
    }


# ---------------------------------------------------------------------------
# §3.8.1a _ZZ_ENUM_LABELS
# ---------------------------------------------------------------------------


class TestZzEnumLabels:
    def test_structure_keys(self):
        # Phase 5 (RFC v3.1 §4.1 / §5.3): +"arm_source" for ARM_SRC_* display.
        assert set(_ZZ_ENUM_LABELS.keys()) == {
            "leg_direction", "regime_state", "armed_side", "fired", "arm_source"
        }

    def test_leg_direction_values(self):
        ld = _ZZ_ENUM_LABELS["leg_direction"]
        assert ld[1] == "up"
        assert ld[-1] == "down"
        assert ld[0] == ""

    def test_regime_state_values(self):
        rs = _ZZ_ENUM_LABELS["regime_state"]
        assert rs[0] == "closed"
        assert rs[1] == "grace"
        assert rs[2] == "active"

    def test_armed_side_values(self):
        ars = _ZZ_ENUM_LABELS["armed_side"]
        assert ars[1] == "long"
        assert ars[-1] == "short"
        assert ars[0] == ""

    def test_fired_has_yes_shot(self):
        fired = _ZZ_ENUM_LABELS["fired"]
        assert fired[1] == "yes_shot"
        assert fired[0] == ""


# ---------------------------------------------------------------------------
# §3.8.1 SIGNALS_DISPLAY_NAMES contains zz keys
# ---------------------------------------------------------------------------


class TestSignalsDisplayNamesZz:
    _EXPECTED_ZZ_KEYS = [
        "zz_leg_direction", "zz_cand_height_pct", "zz_global_median",
        "zz_global_p80", "zz_local_median", "zz_n_legs",
        "zz_regime_state", "zz_armed", "zz_armed_side",
    ]

    def test_all_zz_keys_present(self):
        for key in self._EXPECTED_ZZ_KEYS:
            assert key in SIGNALS_DISPLAY_NAMES, f"missing key {key!r}"

    def test_display_names_are_strings(self):
        for key in self._EXPECTED_ZZ_KEYS:
            assert isinstance(SIGNALS_DISPLAY_NAMES[key], str)


# ---------------------------------------------------------------------------
# §3.8.3 TRADES_DISPLAY_NAMES contains triggering leg keys
# ---------------------------------------------------------------------------


class TestTradesDisplayNamesZz:
    _EXPECTED_ZZ_KEYS = [
        "triggering_leg_id",
        "triggering_leg_height_pct",
        "triggering_leg_height_ratio",
        "triggering_leg_direction",
        "bars_from_leg_confirm_to_entry",
    ]

    def test_all_triggering_leg_keys_present(self):
        for key in self._EXPECTED_ZZ_KEYS:
            assert key in TRADES_DISPLAY_NAMES, f"missing key {key!r}"


# ---------------------------------------------------------------------------
# §3.8.3a _link_trades_to_legs
# ---------------------------------------------------------------------------


class TestLinkTradesToLegs:
    def test_fired_yes_shot_matched_by_shot_bar(self):
        """O2O: decision_bar = entry_index - 1. shot_bar == decision_bar → match."""
        leg = _make_leg(0, fired=FIRED_YES_SHOT, shot_bar=9)
        trades = _make_trades_df((42, 10))  # entry_index=10 → dec=9 (O2O)
        result = _link_trades_to_legs(trades, (leg,), ExecutionModel.OPEN_TO_OPEN)
        assert result[0].trade_id_if_fired == 42

    def test_c2c_decision_bar(self):
        """C2C: decision_bar = entry_index."""
        leg = _make_leg(0, fired=FIRED_YES_SHOT, shot_bar=10)
        trades = _make_trades_df((99, 10))
        result = _link_trades_to_legs(trades, (leg,), ExecutionModel.CLOSE_TO_CLOSE)
        assert result[0].trade_id_if_fired == 99

    def test_no_match_when_fired_not_yes_shot(self):
        """Legs with fired != YES_SHOT must not be linked."""
        leg = _make_leg(0, fired=FIRED_NO_NEW_PIVOT, shot_bar=9)
        trades = _make_trades_df((1, 10))
        result = _link_trades_to_legs(trades, (leg,), ExecutionModel.OPEN_TO_OPEN)
        assert result[0].trade_id_if_fired is None

    def test_no_match_when_shot_bar_mismatch(self):
        leg = _make_leg(0, fired=FIRED_YES_SHOT, shot_bar=8)  # dec=9, bar mismatch
        trades = _make_trades_df((1, 10))
        result = _link_trades_to_legs(trades, (leg,), ExecutionModel.OPEN_TO_OPEN)
        assert result[0].trade_id_if_fired is None

    def test_multiple_matches_warns_no_link(self):
        """Two legs with same shot_bar and FIRED_YES_SHOT → warn, no link."""
        leg0 = _make_leg(0, fired=FIRED_YES_SHOT, shot_bar=9)
        leg1 = _make_leg(1, fired=FIRED_YES_SHOT, shot_bar=9)
        trades = _make_trades_df((5, 10))
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _link_trades_to_legs(trades, (leg0, leg1), ExecutionModel.OPEN_TO_OPEN)
        assert any("multiple" in str(x.message).lower() for x in w)
        assert result[0].trade_id_if_fired is None
        assert result[1].trade_id_if_fired is None

    def test_empty_trades_df_leaves_legs_unchanged(self):
        leg = _make_leg(0, fired=FIRED_YES_SHOT, shot_bar=9)
        result = _link_trades_to_legs(pd.DataFrame(), (leg,), ExecutionModel.OPEN_TO_OPEN)
        assert result[0].trade_id_if_fired is None

    def test_original_tuple_not_mutated(self):
        """Ensures we work on a copy, not the original (frozen dataclass)."""
        leg = _make_leg(0, fired=FIRED_YES_SHOT, shot_bar=9)
        original_legs = (leg,)
        trades = _make_trades_df((7, 10))
        _link_trades_to_legs(trades, original_legs, ExecutionModel.OPEN_TO_OPEN)
        # Original tuple element must be unchanged
        assert original_legs[0].trade_id_if_fired is None


# ---------------------------------------------------------------------------
# §3.8.2 _write_legs_sheet
# ---------------------------------------------------------------------------


class TestWriteLegsSheet:
    def _write_and_read(self, legs, index=None):
        if index is None:
            index = pd.date_range("2024-01-01", periods=200, freq="1h")
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            _write_legs_sheet(writer, legs, index, ExecutionModel.OPEN_TO_OPEN)
        buf.seek(0)
        return pd.read_excel(buf, sheet_name="Legs")

    def test_creates_legs_sheet(self):
        legs = [_make_leg(0)]
        df = self._write_and_read(legs)
        assert len(df) == 1

    def test_empty_legs_creates_empty_sheet(self):
        df = self._write_and_read([])
        assert len(df) == 0

    def test_required_columns_present(self):
        legs = [_make_leg(0)]
        df = self._write_and_read(legs)
        for col in ("Leg ID", "Direction", "Start Bar", "End Bar",
                    "Confirm Bar", "Height %", "Fired"):
            assert col in df.columns, f"missing column {col!r}"

    def test_direction_is_string(self):
        legs = [_make_leg(0, direction=LEG_DIR_UP)]
        df = self._write_and_read(legs)
        assert df["Direction"].iloc[0] == "up"

    def test_fired_is_string(self):
        legs = [_make_leg(0, fired=FIRED_YES_SHOT)]
        df = self._write_and_read(legs)
        assert df["Fired"].iloc[0] == "yes_shot"

    def test_height_ratio_computed(self):
        leg = _make_leg(0, height_pct=0.02, global_median_at_confirm=0.01)
        df = self._write_and_read([leg])
        assert abs(df["Height Ratio"].iloc[0] - 2.0) < 1e-6

    def test_height_ratio_nan_when_median_zero(self):
        leg = _make_leg(0, height_pct=0.02, global_median_at_confirm=0.0)
        df = self._write_and_read([leg])
        assert pd.isna(df["Height Ratio"].iloc[0])

    def test_tz_aware_index_strips_timezone_before_excel_write(self):
        """
        Regression: real-world OHLC CSVs (e.g. ``data.csv`` is UTC+03:00)
        produce a tz-aware DatetimeIndex.  ``openpyxl`` rejects tz-aware
        datetimes with "Excel does not support datetimes with timezones",
        so the Legs writer must strip tz info before ``to_excel``.

        Other sheets (Trades / Signals) already do this at their own
        write-sites; this test guards the analogous fix for Legs.
        """
        tz_index = pd.date_range(
            "2024-01-01", periods=200, freq="1h", tz="Europe/Moscow"
        )
        assert tz_index.tz is not None, "fixture must be tz-aware"

        legs = [_make_leg(0)]
        df = self._write_and_read(legs, index=tz_index)

        for col in ("Start Time", "End Time", "Confirm Time"):
            assert col in df.columns
            s = pd.to_datetime(df[col], errors="coerce")
            assert getattr(s.dt, "tz", None) is None, (
                f"{col}: tz info leaked into Excel output ({s.dt.tz!r})"
            )


# ---------------------------------------------------------------------------
# §3.8.4 _write_filters_summary_sheet for zz mode
# ---------------------------------------------------------------------------


class TestWriteFiltersSummaryZz:
    def _write_and_read(self, fd_list, labels, include_total=False):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            _write_filters_summary_sheet(writer, fd_list, labels, include_total)
        buf.seek(0)
        return pd.read_excel(buf, sheet_name="filters_summary")

    def test_zz_params_columns_present(self):
        fd = _make_zz_fd()
        df = self._write_and_read([fd], ["100%"])
        assert "ZZ Reversal Threshold" in df.columns
        assert "ZZ Min Legs Global" in df.columns
        assert "ZZ Q Strong" in df.columns

    def test_zz_per_reason_columns_present(self):
        fd = _make_zz_fd()
        df = self._write_and_read([fd], ["100%"])
        for col in ("ZZ Blocked: Warmup", "ZZ Blocked: Regime Off",
                    "ZZ Blocked: Not Armed", "ZZ Blocked: Armed Waiting"):
            assert col in df.columns, f"missing: {col}"

    def test_zz_warmup_count_correct(self):
        n = 100
        fd = _make_zz_fd(n=n)
        # All 100 bars blocked by zz_warmup
        fd["filtered_reason"] = np.full(n, "zz_warmup", dtype=object)
        df = self._write_and_read([fd], ["100%"])
        assert df["ZZ Blocked: Warmup"].iloc[0] == n

    def test_zz_leg_aggregates_present(self):
        fd = _make_zz_fd(with_legs=True)
        df = self._write_and_read([fd], ["100%"])
        for col in ("ZZ Total Legs", "ZZ Legs Up", "ZZ Legs Down",
                    "ZZ Median Height %"):
            assert col in df.columns, f"missing: {col}"
        assert df["ZZ Total Legs"].iloc[0] == 1

    def test_non_zz_mode_no_zz_columns(self):
        """Non-zz modes must NOT have zz-specific columns."""
        fd = {
            "mode": "amplitude",
            "thresholds": {"amplitude": {"n": 20, "lookback": 500,
                                          "q": 0.6, "min_separation": None,
                                          "atr_floor": 0.0}},
            "counters": {
                "raw_entry_signals": 5,
                "passed_entry_signals": 3,
                "blocked_entry_signals": 2,
                "blocked_by_volatility": 2,
                "blocked_by_volume": 0,
                "blocked_by_both": 0,
                "blocked_by_vol_ma_invalid": 0,
            },
            "zz_legs": (),
        }
        df = self._write_and_read([fd], ["100%"])
        # ZZ reason columns should NOT be present for amplitude mode
        assert "ZZ Blocked: Warmup" not in df.columns
        assert "ZZ Total Legs" not in df.columns

    def test_total_row_sums_zz_counters(self):
        fd1 = _make_zz_fd(n=50)
        fd1["filtered_reason"] = np.full(50, "zz_warmup", dtype=object)
        fd2 = _make_zz_fd(n=50)
        fd2["filtered_reason"] = np.full(50, "zz_warmup", dtype=object)
        df = self._write_and_read([fd1, fd2], ["S1", "S2"], include_total=True)
        total_row = df[df["Label"] == "Total"]
        assert len(total_row) == 1
        # Each fd has 50 zz_warmup → total should be 100
        assert total_row["ZZ Blocked: Warmup"].iloc[0] == 100


# ---------------------------------------------------------------------------
# Fix 3: _n_armed must count only legs with fired != FIRED_NONE (arm_bar != -1)
# ---------------------------------------------------------------------------


from supertrend_optimizer.core.zigzag_filter import (
    FIRED_NONE,
    FIRED_NO_TIMEOUT_SOFT,
    FIRED_NO_TIMEOUT_HARD,
)


class TestNArmedCounting:
    """
    Verifies that the Filter Summary counts n_armed_legs correctly.

    Bug: arm_bar is int (default -1), not Optional[int].  The old code used
    ``getattr(lg, "arm_bar", None) is not None`` which is always True, so
    _n_armed was equal to len(zz_legs) regardless.

    Fix: check ``fired != FIRED_NONE`` (Fix 3 in audit).

    These tests would have failed before Fix 3.
    """

    def _make_fd_with_legs(self, legs):
        n = 100
        return {
            "mode": "zigzag",
            "thresholds": {
                "zigzag": {
                    "reversal_threshold": 0.005,
                    "min_legs_global": 5,
                    "q_strong": 0.80,
                    "k_local": 5,
                    "entry_side": "counter_trend",
                    "arm_timeout_bars_since_extreme": 24,
                    "arm_timeout_bars_hard": 78,
                },
                "global_volume_ma_mean": None,
            },
            "counters": {
                "raw_entry_signals": 10,
                "passed_entry_signals": 3,
                "blocked_entry_signals": 7,
                "blocked_by_volatility": 7,
                "blocked_by_volume": 0,
                "blocked_by_both": 0,
                "blocked_by_vol_ma_invalid": 0,
            },
            "zz_legs": tuple(legs),
            "zz_regime_state": np.zeros(n, dtype=np.int8),
            "filtered_reason": np.full(n, "zz_warmup", dtype=object),
            "allow_entry": np.zeros(n, dtype=bool),
        }

    def _write_and_read(self, legs):
        fd = self._make_fd_with_legs(legs)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            _write_filters_summary_sheet(writer, [fd], ["100%"],
                                         include_total_row=False)
        buf.seek(0)
        return pd.read_excel(buf, sheet_name="filters_summary")

    def test_n_armed_zero_when_no_armed_legs(self):
        """Legs with arm_bar=-1 (fired=FIRED_NONE) must not be counted as armed."""
        unarmed_legs = [
            _make_leg(i, arm_bar=-1, fired=FIRED_NONE, shot_bar=-1)
            for i in range(5)
        ]
        df = self._write_and_read(unarmed_legs)
        assert df["ZZ N Armed Legs"].iloc[0] == 0, (
            "Legs with arm_bar=-1 / fired=FIRED_NONE must count as 0 armed, "
            f"got {df['ZZ N Armed Legs'].iloc[0]}. "
            "This confirms Fix 3: old code counted all 5 due to 'is not None' bug."
        )

    def test_n_armed_counts_only_armed_legs(self):
        """Mix: 2 unarmed + 3 armed → n_armed must be 3."""
        legs = [
            # Unarmed: arm_bar=-1, fired=FIRED_NONE
            _make_leg(0, arm_bar=-1, fired=FIRED_NONE, shot_bar=-1),
            _make_leg(1, arm_bar=-1, fired=FIRED_NONE, shot_bar=-1),
            # Armed: fired is non-NONE
            _make_leg(2, arm_bar=7,  fired=FIRED_YES_SHOT,        shot_bar=10),
            _make_leg(3, arm_bar=14, fired=FIRED_NO_NEW_PIVOT,    shot_bar=-1),
            _make_leg(4, arm_bar=21, fired=FIRED_NO_TIMEOUT_SOFT, shot_bar=-1),
        ]
        df = self._write_and_read(legs)
        assert df["ZZ N Armed Legs"].iloc[0] == 3, (
            f"Expected 3 armed legs, got {df['ZZ N Armed Legs'].iloc[0]}. "
            "Old code with 'is not None' would have returned 5."
        )

    def test_fired_rate_uses_correct_denominator(self):
        """fired_rate = n_yes / n_armed * 100; denominator must not include unarmed."""
        legs = [
            _make_leg(0, arm_bar=-1, fired=FIRED_NONE,     shot_bar=-1),
            _make_leg(1, arm_bar=-1, fired=FIRED_NONE,     shot_bar=-1),
            _make_leg(2, arm_bar=7,  fired=FIRED_YES_SHOT, shot_bar=10),
            _make_leg(3, arm_bar=14, fired=FIRED_YES_SHOT, shot_bar=20),
            _make_leg(4, arm_bar=21, fired=FIRED_NO_TIMEOUT_HARD, shot_bar=-1),
        ]
        df = self._write_and_read(legs)
        # n_armed=3, n_yes=2 → fired_rate = 2/3*100 ≈ 66.67
        armed = df["ZZ N Armed Legs"].iloc[0]
        fired_rate = df["ZZ Fired Rate %"].iloc[0]
        assert armed == 3, f"Expected 3 armed, got {armed}"
        assert abs(fired_rate - (2 / 3 * 100)) < 0.1, (
            f"fired_rate={fired_rate}, expected ≈66.67. "
            "Old code with wrong denominator (5) would give 40.0."
        )

    def test_all_armed_gives_n_armed_eq_total(self):
        """When all legs are armed (FIRED_YES_SHOT), n_armed == n_total."""
        legs = [_make_leg(i, arm_bar=10 + i, fired=FIRED_YES_SHOT,
                          shot_bar=15 + i)
                for i in range(4)]
        df = self._write_and_read(legs)
        assert df["ZZ N Armed Legs"].iloc[0] == 4
