"""
Tests for D1–D4 fixes in io/excel_tester.py (audit §3.8).

D1 — execution_model propagated correctly to _link_trades_to_legs (C2C vs O2O).
D2 — _write_legs_sheet receives real DatetimeIndex → timestamps are not NaT.
D3 — Triggering Leg columns populated in trades_df after _enrich_trades_with_leg_columns.
D4 — "Correction Height % Before Entry" column populated / NaN when applicable.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    ARMED_SIDE_LONG,
    ARMED_SIDE_SHORT,
    FIRED_NO_NEW_PIVOT,
    FIRED_YES_SHOT,
    LEG_DIR_DOWN,
    LEG_DIR_UP,
    LegRecord,
    REGIME_OPEN_ACTIVE,
)
from supertrend_optimizer.io.excel_tester import (
    TRADES_DISPLAY_NAMES,
    _enrich_trades_with_leg_columns,
    _link_trades_to_legs,
    _write_legs_sheet,
)
from supertrend_optimizer.utils.enums import ExecutionModel


# ---------------------------------------------------------------------------
# Shared helpers
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
        regime_state_at_confirm=REGIME_OPEN_ACTIVE,
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
        rows.append(
            {
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
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# D1 — execution_model affects shot_bar matching in _link_trades_to_legs
# ---------------------------------------------------------------------------


class TestD1TradeLeglinkageExecutionModel:
    """D1: _link_trades_to_legs must use execution_model for decision-bar shift."""

    def test_o2o_links_shot_bar_at_entry_minus_1(self):
        """O2O: decision_bar = entry_index - 1 → leg with shot_bar=9 links to trade entry_index=10."""
        # shot_bar=9 == entry_index(10) - 1
        leg = _make_leg(leg_id=1, confirm_bar=7, shot_bar=9, fired=FIRED_YES_SHOT)
        trades = _make_trades_df((42, 10))
        linked = _link_trades_to_legs(trades, (leg,), ExecutionModel.OPEN_TO_OPEN)
        assert linked[0].trade_id_if_fired == 42

    def test_c2c_links_shot_bar_at_entry_index(self):
        """C2C: decision_bar = entry_index → leg with shot_bar=10 links to trade entry_index=10."""
        leg = _make_leg(leg_id=1, confirm_bar=7, shot_bar=10, fired=FIRED_YES_SHOT)
        trades = _make_trades_df((42, 10))
        linked = _link_trades_to_legs(trades, (leg,), ExecutionModel.CLOSE_TO_CLOSE)
        assert linked[0].trade_id_if_fired == 42

    def test_o2o_does_not_link_when_shot_bar_equals_entry_index(self):
        """O2O: decision_bar = entry-1; shot_bar == entry_index (C2C offset) must NOT match."""
        leg = _make_leg(leg_id=1, confirm_bar=7, shot_bar=10, fired=FIRED_YES_SHOT)
        trades = _make_trades_df((42, 10))
        linked = _link_trades_to_legs(trades, (leg,), ExecutionModel.OPEN_TO_OPEN)
        assert linked[0].trade_id_if_fired is None

    def test_c2c_does_not_link_when_shot_bar_is_entry_minus_1(self):
        """C2C: decision_bar = entry_index; shot_bar == entry-1 (O2O offset) must NOT match."""
        leg = _make_leg(leg_id=1, confirm_bar=7, shot_bar=9, fired=FIRED_YES_SHOT)
        trades = _make_trades_df((42, 10))
        linked = _link_trades_to_legs(trades, (leg,), ExecutionModel.CLOSE_TO_CLOSE)
        assert linked[0].trade_id_if_fired is None

    @pytest.mark.parametrize("em", [ExecutionModel.OPEN_TO_OPEN, ExecutionModel.CLOSE_TO_CLOSE])
    def test_non_fired_leg_never_linked(self, em):
        """A leg with fired != YES_SHOT must never receive a trade_id."""
        shot_bar = 9 if em == ExecutionModel.OPEN_TO_OPEN else 10
        leg = _make_leg(
            leg_id=1, confirm_bar=7, shot_bar=shot_bar, fired=FIRED_NO_NEW_PIVOT
        )
        trades = _make_trades_df((42, 10))
        linked = _link_trades_to_legs(trades, (leg,), em)
        assert linked[0].trade_id_if_fired is None


# ---------------------------------------------------------------------------
# D2 — _write_legs_sheet timestamps are not NaT when real index is passed
# ---------------------------------------------------------------------------


class TestD2LegsSheetTimestamps:
    """D2: passing a real DatetimeIndex must produce non-NaT Session/Start/Confirm times."""

    def _run_write_legs_sheet(self, index):
        leg = _make_leg(leg_id=0, start_bar=1, end_bar=3, confirm_bar=5, shot_bar=6)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            _write_legs_sheet(writer, [leg], index, ExecutionModel.OPEN_TO_OPEN)
        buf.seek(0)
        return pd.read_excel(buf, sheet_name="Legs")

    def test_non_nat_timestamps_with_real_index(self):
        """A DatetimeIndex of sufficient length → Start Time / Confirm Time are not NaT."""
        idx = pd.date_range("2024-01-01 09:00", periods=20, freq="1min")
        df = self._run_write_legs_sheet(idx)
        assert "Start Time" in df.columns, "Start Time column missing"
        assert "Confirm Time" in df.columns, "Confirm Time column missing"
        assert pd.notna(df["Start Time"].iloc[0]), "Start Time is NaT with real index"
        assert pd.notna(df["Confirm Time"].iloc[0]), "Confirm Time is NaT with real index"

    def test_nat_timestamps_with_empty_index(self):
        """Empty index (legacy path) still produces NaT — baseline sanity check."""
        df = self._run_write_legs_sheet(pd.Index([]))
        assert pd.isna(df["Start Time"].iloc[0]), "Start Time should be NaT for empty index"
        assert pd.isna(df["Confirm Time"].iloc[0]), "Confirm Time should be NaT for empty index"

    def test_session_date_non_nat_with_real_index(self):
        """Session Date must be set when real DatetimeIndex is provided."""
        idx = pd.date_range("2024-01-02 10:00", periods=20, freq="1min")
        df = self._run_write_legs_sheet(idx)
        assert "Session Date" in df.columns
        assert pd.notna(df["Session Date"].iloc[0]), "Session Date is NaT with real index"


# ---------------------------------------------------------------------------
# D3 — _enrich_trades_with_leg_columns populates Triggering Leg columns
# ---------------------------------------------------------------------------


class TestD3TriggeringLegColumns:
    """D3: _enrich_trades_with_leg_columns must fill all 5 Triggering Leg fields."""

    def _setup(self, em: ExecutionModel):
        """Create a linked leg + matching trade for given execution model."""
        shot_bar = 9 if em == ExecutionModel.OPEN_TO_OPEN else 10  # entry_index=10
        leg = _make_leg(
            leg_id=7,
            confirm_bar=6,
            shot_bar=shot_bar,
            fired=FIRED_YES_SHOT,
            direction=LEG_DIR_UP,
            height_pct=0.03,
            global_median_at_confirm=0.015,
            trade_id_if_fired=42,
        )
        trades = _make_trades_df((42, 10))
        is_o2o = em == ExecutionModel.OPEN_TO_OPEN
        return trades, [leg], is_o2o

    @pytest.mark.parametrize("em", [ExecutionModel.OPEN_TO_OPEN, ExecutionModel.CLOSE_TO_CLOSE])
    def test_triggering_leg_id_matches_leg(self, em):
        trades, legs, is_o2o = self._setup(em)
        result = _enrich_trades_with_leg_columns(trades, legs, is_o2o)
        assert result["triggering_leg_id"].iloc[0] == 7

    @pytest.mark.parametrize("em", [ExecutionModel.OPEN_TO_OPEN, ExecutionModel.CLOSE_TO_CLOSE])
    def test_triggering_leg_height_pct_positive(self, em):
        trades, legs, is_o2o = self._setup(em)
        result = _enrich_trades_with_leg_columns(trades, legs, is_o2o)
        assert result["triggering_leg_height_pct"].iloc[0] == pytest.approx(0.03)

    @pytest.mark.parametrize("em", [ExecutionModel.OPEN_TO_OPEN, ExecutionModel.CLOSE_TO_CLOSE])
    def test_triggering_leg_height_ratio_is_height_div_median(self, em):
        trades, legs, is_o2o = self._setup(em)
        result = _enrich_trades_with_leg_columns(trades, legs, is_o2o)
        # 0.03 / 0.015 = 2.0
        assert result["triggering_leg_height_ratio"].iloc[0] == pytest.approx(2.0)

    @pytest.mark.parametrize("em", [ExecutionModel.OPEN_TO_OPEN, ExecutionModel.CLOSE_TO_CLOSE])
    def test_triggering_leg_direction_label(self, em):
        trades, legs, is_o2o = self._setup(em)
        result = _enrich_trades_with_leg_columns(trades, legs, is_o2o)
        assert result["triggering_leg_direction"].iloc[0] == "up"

    @pytest.mark.parametrize("em", [ExecutionModel.OPEN_TO_OPEN, ExecutionModel.CLOSE_TO_CLOSE])
    def test_bars_from_leg_confirm_to_entry_non_negative(self, em):
        trades, legs, is_o2o = self._setup(em)
        result = _enrich_trades_with_leg_columns(trades, legs, is_o2o)
        val = result["bars_from_leg_confirm_to_entry"].iloc[0]
        assert val is not None and val >= 0, f"bars_from_leg_confirm_to_entry={val}"

    def test_unlinked_trade_gets_none_columns(self):
        """Trade with no matching leg → all Triggering Leg fields are None."""
        leg = _make_leg(
            leg_id=99, confirm_bar=6, shot_bar=9, fired=FIRED_YES_SHOT,
            trade_id_if_fired=None,
        )
        trades = _make_trades_df((42, 10))
        result = _enrich_trades_with_leg_columns(trades, [leg], is_o2o=True)
        assert result["triggering_leg_id"].iloc[0] is None

    def test_all_triggering_leg_keys_in_trades_display_names(self):
        """TRADES_DISPLAY_NAMES must declare all 5 Triggering Leg keys (§3.8.3)."""
        required = {
            "triggering_leg_id",
            "triggering_leg_height_pct",
            "triggering_leg_height_ratio",
            "triggering_leg_direction",
            "bars_from_leg_confirm_to_entry",
        }
        assert required.issubset(set(TRADES_DISPLAY_NAMES.keys()))


# ---------------------------------------------------------------------------
# D4 — correction_height_pct_before_entry column
# ---------------------------------------------------------------------------


class TestD4CorrectionHeight:
    """D4: correction_height_pct_before_entry populated when applicable, NaN otherwise."""

    def test_nan_when_no_opposite_leg_between_confirm_and_entry(self):
        """No correction leg between confirm_bar and entry_bar → value is None."""
        # Trig leg: UP, confirm_bar=5, shot_bar=9. entry_index=10 (O2O → dec=9).
        # No DOWN leg with 5 < confirm_bar < 9.
        trig_leg = _make_leg(
            leg_id=1, confirm_bar=5, shot_bar=9, fired=FIRED_YES_SHOT,
            direction=LEG_DIR_UP, trade_id_if_fired=1,
        )
        trades = _make_trades_df((1, 10))
        result = _enrich_trades_with_leg_columns(trades, [trig_leg], is_o2o=True)
        assert result["correction_height_pct_before_entry"].iloc[0] is None

    def test_populated_when_opposite_leg_exists_between_confirm_and_entry(self):
        """Opposite-direction leg confirmed between trig confirm and entry → value > 0."""
        # Trig leg: UP, confirm_bar=5, shot_bar=9 (O2O dec=9). entry_index=10.
        # Correction leg: DOWN, confirm_bar=7 (between 5 and 9).
        trig_leg = _make_leg(
            leg_id=1, confirm_bar=5, shot_bar=9, fired=FIRED_YES_SHOT,
            direction=LEG_DIR_UP, height_pct=0.02, trade_id_if_fired=1,
        )
        corr_leg = _make_leg(
            leg_id=2, confirm_bar=7, shot_bar=-1, fired=FIRED_NO_NEW_PIVOT,
            direction=LEG_DIR_DOWN, height_pct=0.01, armed_side=ARMED_SIDE_LONG,
        )
        trades = _make_trades_df((1, 10))
        result = _enrich_trades_with_leg_columns(trades, [trig_leg, corr_leg], is_o2o=True)
        val = result["correction_height_pct_before_entry"].iloc[0]
        assert val is not None and val == pytest.approx(0.01)

    def test_correction_leg_outside_window_not_counted(self):
        """Opposite leg confirmed AFTER entry_bar must not be counted."""
        trig_leg = _make_leg(
            leg_id=1, confirm_bar=5, shot_bar=9, fired=FIRED_YES_SHOT,
            direction=LEG_DIR_UP, trade_id_if_fired=1,
        )
        # DOWN leg with confirm_bar=10 == entry dec → strictly < required, so boundary excluded.
        corr_leg_after = _make_leg(
            leg_id=2, confirm_bar=10, shot_bar=-1, fired=FIRED_NO_NEW_PIVOT,
            direction=LEG_DIR_DOWN, height_pct=0.05, armed_side=ARMED_SIDE_LONG,
        )
        trades = _make_trades_df((1, 10))
        result = _enrich_trades_with_leg_columns(trades, [trig_leg, corr_leg_after], is_o2o=True)
        assert result["correction_height_pct_before_entry"].iloc[0] is None

    def test_correction_height_key_in_trades_display_names(self):
        """TRADES_DISPLAY_NAMES must declare correction_height_pct_before_entry (§3.8.3)."""
        assert "correction_height_pct_before_entry" in TRADES_DISPLAY_NAMES
        assert "Correction Height % Before Entry" in TRADES_DISPLAY_NAMES.values()
