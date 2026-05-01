"""
Phase 5 tests — Export / Excel migration + trade↔leg linkage (RFC v3.1 §7).

Covers:
- TP-14  orphan contour-A trade (no candidate match)
- TP-16  Legs sheet new fields on pre-confirm linkage
- TP-17  sign-ful bars_from_leg_confirm_to_entry + entry_vs_leg_phase
- TP-27  pre_confirm_shot_bar populated after two-step linkage
- TP-42  orphan trade carries cand_*_at_entry columns
- TP-44  SIGNALS_DISPLAY_NAMES + _ZZ_ENUM_LABELS new keys (fix N-10 / N-11)
- TP-45  FilterDiagnostics aggregate `ZZ Legs No Regime Off` (fix N-12)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    ARMED_SIDE_LONG,
    ARMED_SIDE_SHORT,
    ARM_SRC_A,
    ARM_SRC_B,
    ARM_SRC_BOTH,
    ARM_SRC_NONE,
    FIRED_NONE,
    FIRED_NO_REGIME_OFF,
    FIRED_YES_SHOT,
    LEG_DIR_DOWN,
    LEG_DIR_UP,
    LegRecord,
    REGIME_OPEN_ACTIVE,
)
from supertrend_optimizer.io.excel_tester import (
    SIGNALS_DISPLAY_NAMES,
    TRADES_DISPLAY_NAMES,
    _ZZ_ENUM_LABELS,
    _enrich_trades_with_leg_columns,
    _link_trades_to_legs,
    _write_filters_summary_sheet,
)
from supertrend_optimizer.utils.enums import ExecutionModel


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _leg(
    leg_id=0,
    start_bar=0,
    end_bar=5,
    confirm_bar=7,
    direction=LEG_DIR_UP,
    height_pct=0.02,
    fired=FIRED_NONE,
    shot_bar=-1,
    arm_bar=-1,
    armed_side=0,
    trade_id_if_fired=None,
    arm_source=ARM_SRC_NONE,
    armed_by_candidate=False,
    pre_confirm_arm_bar=-1,
    pre_confirm_shot_bar=-1,
    _cand_leg_id_at_confirm=-1,
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
        global_median_at_confirm=0.015,
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
        arm_source=arm_source,
        armed_by_candidate=armed_by_candidate,
        pre_confirm_arm_bar=pre_confirm_arm_bar,
        pre_confirm_shot_bar=pre_confirm_shot_bar,
        _cand_leg_id_at_confirm=_cand_leg_id_at_confirm,
    )


def _trade_row(tid: int, entry_index: int) -> dict:
    return {
        "trade_id": tid,
        "entry_index": entry_index,
        "entry_time": pd.Timestamp("2024-01-01"),
        "entry_price": 100.0,
        "exit_time": pd.Timestamp("2024-01-02"),
        "exit_index": entry_index + 5,
        "exit_price": 101.0,
        "direction": "LONG",
        "bars_held": 5,
        "gross_pnl_pct": 1.0,
        "commission_pct": 0.03,
        "net_pnl_pct": 0.97,
    }


def _trades_df(*rows) -> pd.DataFrame:
    return pd.DataFrame([_trade_row(*r) for r in rows])


# ---------------------------------------------------------------------------
# TP-27 / G-04  — two-step pre-confirm linkage
# ---------------------------------------------------------------------------


class TestTP27TwoStepLinkage:
    """Trade whose decision_bar is < confirm_bar links via cand_leg_id."""

    def test_pre_confirm_trade_links_via_cand_leg_id(self):
        # Candidate leg: start=0, confirm_bar=20, cand_leg_id_at_confirm=5.
        # Pre-confirm shot at decision_bar=15 (before confirm).
        lg = _leg(
            leg_id=42, start_bar=0, end_bar=18, confirm_bar=20,
            fired=FIRED_NONE,  # session was pre-confirm, disarmed elsewhere
            arm_bar=-1, shot_bar=-1,
            arm_source=ARM_SRC_A,
            armed_by_candidate=True,
            pre_confirm_arm_bar=12,
            _cand_leg_id_at_confirm=5,
        )
        # O2O: decision_bar = entry_index - 1. entry_index=16 → dec=15.
        trades = _trades_df((101, 16))
        # Per-bar cand_leg_id: 5 on bar 15.
        cand_id_arr = np.full(30, -1, dtype=np.int64)
        cand_id_arr[10:20] = 5

        linked = _link_trades_to_legs(
            trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
            zz_cand_leg_id=cand_id_arr,
        )
        assert len(linked) == 1
        lg_linked = linked[0]
        assert lg_linked.trade_id_if_fired == 101
        assert int(lg_linked.pre_confirm_shot_bar) == 15
        # Legacy fields preserved (inv 8.3.7).
        assert int(lg_linked.shot_bar) == -1
        assert int(lg_linked.arm_bar) == -1
        assert bool(lg_linked.armed_by_candidate) is True

    def test_step1_still_works_for_post_confirm(self):
        """Post-confirm YES_SHOT linkage must not regress."""
        lg = _leg(
            leg_id=1, confirm_bar=5, end_bar=4,
            fired=FIRED_YES_SHOT, arm_bar=5, shot_bar=10,
            arm_source=ARM_SRC_A,
            _cand_leg_id_at_confirm=2,
        )
        trades = _trades_df((1, 11))  # O2O → dec=10.
        cand_id_arr = np.zeros(20, dtype=np.int64)
        linked = _link_trades_to_legs(
            trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
            zz_cand_leg_id=cand_id_arr,
        )
        assert linked[0].trade_id_if_fired == 1
        assert int(linked[0].pre_confirm_shot_bar) == -1


# ---------------------------------------------------------------------------
# TP-14 / TP-42 — orphan trade, cand_*_at_entry columns
# ---------------------------------------------------------------------------


class TestTP14TP42OrphanTrade:
    def test_orphan_linkage_emits_warning_and_no_leg_patch(self):
        lg = _leg(
            leg_id=0, confirm_bar=50, end_bar=48,
            fired=FIRED_NONE, arm_bar=-1, shot_bar=-1,
            _cand_leg_id_at_confirm=9,
        )
        # Trade entry_index=11 (O2O dec=10): no candidate-id anywhere.
        trades = _trades_df((999, 11))
        cand_id_arr = np.full(60, -1, dtype=np.int64)

        with pytest.warns(UserWarning, match="orphan trade"):
            linked = _link_trades_to_legs(
                trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
                zz_cand_leg_id=cand_id_arr,
            )
        assert linked[0].trade_id_if_fired is None

    def test_orphan_trade_cand_columns_filled(self):
        """TP-42: orphan trades carry cand_side_at_entry / height / leg_id."""
        # Build a fd-shaped dict with per-bar cand arrays.
        n = 30
        fd = {
            "zz_leg_direction":   np.full(n, LEG_DIR_UP, dtype=np.int8),
            "zz_cand_height_pct": np.full(n, 0.017, dtype=np.float64),
            "zz_cand_leg_id":     np.full(n, 7, dtype=np.int64),
            "zz_arm_source":      np.full(n, ARM_SRC_A, dtype=np.int8),
        }
        trades = _trades_df((501, 12))  # dec=11 for O2O
        # No legs linked → orphan.
        enriched = _enrich_trades_with_leg_columns(
            trades, zz_legs_linked=[], is_o2o=True, fd=fd,
        )
        row = enriched.iloc[0]
        assert row["triggering_leg_id"] is None
        # Orphan arm_source attribution from per-bar arrays (§7.8).
        assert row["triggering_arm_source"] == "A"
        # cand_*_at_entry populated from per-bar arrays (§7.6 fix N-07).
        assert row["cand_side_at_entry"] == "SHORT"  # UP leg → candidate SHORT
        assert float(row["cand_height_pct_at_entry"]) == pytest.approx(0.017)
        assert int(row["cand_leg_id_at_entry"]) == 7
        assert row["entry_vs_leg_phase"] == "unlinked"


# ---------------------------------------------------------------------------
# TP-16 — Legs sheet new fields on pre-confirm linkage
# ---------------------------------------------------------------------------


class TestTP16LegsSheetNewFields:
    def test_armed_by_candidate_true_after_preconfirm_linkage(self):
        lg = _leg(
            leg_id=3, start_bar=0, end_bar=18, confirm_bar=20,
            fired=FIRED_NONE, arm_bar=-1, shot_bar=-1,
            arm_source=ARM_SRC_A,
            armed_by_candidate=True,
            pre_confirm_arm_bar=12,
            _cand_leg_id_at_confirm=5,
        )
        trades = _trades_df((77, 16))  # O2O dec=15
        cand_id_arr = np.full(30, -1, dtype=np.int64)
        cand_id_arr[10:20] = 5

        linked = _link_trades_to_legs(
            trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
            zz_cand_leg_id=cand_id_arr,
        )
        lg2 = linked[0]
        # §7.5: legacy fields stay -1; pre-confirm fields carry data.
        assert int(lg2.shot_bar) == -1
        assert int(lg2.arm_bar) == -1
        assert int(lg2.pre_confirm_shot_bar) == 15
        assert int(lg2.pre_confirm_arm_bar) == 12
        assert bool(lg2.armed_by_candidate) is True
        assert int(lg2.arm_source) == int(ARM_SRC_A)


# ---------------------------------------------------------------------------
# TP-17 — sign-ful bars_from_leg_confirm_to_entry + entry_vs_leg_phase
# ---------------------------------------------------------------------------


class TestTP17SignfulBarsFromConfirm:
    def test_pre_confirm_trade_has_negative_bars_and_none_correction(self):
        lg = _leg(
            leg_id=0, start_bar=0, end_bar=18, confirm_bar=20,
            fired=FIRED_NONE, arm_bar=-1, shot_bar=-1,
            arm_source=ARM_SRC_A,
            armed_by_candidate=True,
            pre_confirm_arm_bar=12,
            pre_confirm_shot_bar=15,
            _cand_leg_id_at_confirm=5,
            trade_id_if_fired=77,
        )
        n = 30
        fd = {
            "zz_leg_direction":   np.full(n, LEG_DIR_UP, dtype=np.int8),
            "zz_cand_height_pct": np.full(n, 0.02, dtype=np.float64),
            "zz_cand_leg_id":     np.full(n, 5, dtype=np.int64),
            "zz_arm_source":      np.full(n, ARM_SRC_A, dtype=np.int8),
        }
        trades = _trades_df((77, 16))  # O2O → dec=15. confirm_bar=20.
        enriched = _enrich_trades_with_leg_columns(
            trades, [lg], is_o2o=True, fd=fd,
        )
        row = enriched.iloc[0]
        # §7.7: sign-ful, dec - confirm_bar = 15 - 20 = -5.
        assert int(row["bars_from_leg_confirm_to_entry"]) == -5
        assert row["entry_vs_leg_phase"] == "pre_confirm"
        # pre_confirm → correction_height is None.
        assert row["correction_height_pct_before_entry"] is None
        # triggering_arm_source from leg (not per-bar).
        assert row["triggering_arm_source"] == "A"

    def test_post_confirm_trade_has_positive_bars_and_legacy_correction(self):
        lg = _leg(
            leg_id=0, start_bar=0, end_bar=5, confirm_bar=7,
            fired=FIRED_YES_SHOT, arm_bar=7, shot_bar=10,
            arm_source=ARM_SRC_A,
            _cand_leg_id_at_confirm=1,
            trade_id_if_fired=10,
        )
        trades = _trades_df((10, 11))  # O2O → dec=10. confirm_bar=7.
        enriched = _enrich_trades_with_leg_columns(
            trades, [lg], is_o2o=True, fd=None,
        )
        row = enriched.iloc[0]
        assert int(row["bars_from_leg_confirm_to_entry"]) == 3
        assert row["entry_vs_leg_phase"] == "post_confirm"


# ---------------------------------------------------------------------------
# TP-44 — Signals display + fired enum labels
# ---------------------------------------------------------------------------


class TestTP44SignalsDisplayNames:
    def test_new_zz_ready_and_arm_source_display_keys_present(self):
        assert SIGNALS_DISPLAY_NAMES["zz_ready_a"] == "ZZ Ready A"
        assert SIGNALS_DISPLAY_NAMES["zz_ready_b"] == "ZZ Ready B"
        assert SIGNALS_DISPLAY_NAMES["zz_arm_source"] == "ZZ Arm Source"

    def test_fired_enum_has_no_regime_off_at_6(self):
        assert _ZZ_ENUM_LABELS["fired"][6] == "no_regime_off"

    def test_arm_source_enum_labels_complete(self):
        # ARM_SRC_NONE=0 / A=1 / B=2 / BOTH=3 per RFC §5.3.
        labels = _ZZ_ENUM_LABELS["arm_source"]
        assert labels == {0: "NONE", 1: "A", 2: "B", 3: "BOTH"}


# ---------------------------------------------------------------------------
# TP-45 — FilterDiagnostics: ZZ Legs No Regime Off aggregate
# ---------------------------------------------------------------------------


class TestTP45FilterDiagNoRegimeOff:
    def _minimal_fd(self, *, legs):
        """Minimal filter_diagnostics shape required by the writer (zigzag mode)."""
        n = 40
        return {
            "mode": "zigzag",
            "filtered_reason": np.full(n, "", dtype=object),
            "zz_regime_state": np.zeros(n, dtype=np.int8),
            "zz_legs": tuple(legs),
            "counters": {
                "raw_entry_signals": 0,
                "passed_entry_signals": 0,
                "blocked_entry_signals": 0,
                "blocked_by_volatility": 0,
                "blocked_by_volume": 0,
                "blocked_by_both": 0,
                "blocked_by_vol_ma_invalid": 0,
            },
            "thresholds": {"reversal_threshold": 0.01},
            "volatility_thresh": {},
            "volume_thresh": {},
            "amplitude_thresh": {},
            "zigzag_thresh": {
                "reversal_threshold": 0.01,
                "min_legs_global": 10,
                "q_strong": 0.80,
                "k_local": 5,
                "entry_side": "counter_trend",
                "arm_timeout_bars_since_extreme": 24,
                "arm_timeout_bars_hard": 78,
            },
        }

    def test_no_regime_off_count_exposed(self):
        legs = [
            _leg(leg_id=0, fired=FIRED_NO_REGIME_OFF, arm_bar=7, shot_bar=-1,
                 confirm_bar=7, end_bar=5, arm_source=ARM_SRC_B),
            _leg(leg_id=1, fired=FIRED_NO_REGIME_OFF, arm_bar=12, shot_bar=-1,
                 confirm_bar=12, end_bar=10, arm_source=ARM_SRC_BOTH),
            _leg(leg_id=2, fired=FIRED_YES_SHOT, arm_bar=20, shot_bar=22,
                 confirm_bar=20, end_bar=18, arm_source=ARM_SRC_A),
        ]
        fd = self._minimal_fd(legs=legs)
        import io as _io
        buf = _io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            _write_filters_summary_sheet(
                writer=writer,
                filter_diagnostics_list=[fd],
                labels=["Full"],
            )
        buf.seek(0)
        df = pd.read_excel(buf, sheet_name="filters_summary")
        assert "ZZ Legs No Regime Off" in df.columns
        assert int(df["ZZ Legs No Regime Off"].iloc[0]) == 2


# ---------------------------------------------------------------------------
# TRADES_DISPLAY_NAMES extensions present (soft check)
# ---------------------------------------------------------------------------


def test_phase5_trades_display_names_present():
    for key in (
        "entry_vs_leg_phase",
        "triggering_arm_source",
        "cand_side_at_entry",
        "cand_height_pct_at_entry",
        "cand_leg_id_at_entry",
    ):
        assert key in TRADES_DISPLAY_NAMES, f"missing {key!r} in TRADES_DISPLAY_NAMES"


# ---------------------------------------------------------------------------
# RP-1 Block 1-D — two-step linkage under the core-as-source-of-truth rule
# ---------------------------------------------------------------------------
#
# After RP-1A / RP-1C the core (`_unified_armament_fsm` flush) writes:
#   - `fired`, `shot_bar`, `arm_source` on post-confirm shots (Step 1 path)
#   - `armed_by_candidate`, `pre_confirm_arm_bar`, `arm_source`,
#     `pre_confirm_shot_bar` on pre-confirm cycles (Step 2 backfill path)
#
# RP-1 Block 1-D tightens `_link_trades_to_legs` so the io layer:
#   - never overwrites a core-set `pre_confirm_shot_bar` silently,
#   - raises on core↔io divergence,
#   - does not touch `pre_confirm_shot_bar` from the Step 1 path,
#   - guarantees mutual-exclusion between Step 1 and Step 2 on a
#     per-trade basis (LX-08 / RFC §7.6).
# ---------------------------------------------------------------------------


class TestRP1DLinkage:
    """
    RP-1 Block 1-D focused tests.  Use the shared `_leg` / `_trades_df`
    helpers so the fixtures stay small and obviously correct.
    """

    # ---- R-4: Step 1 does not touch pre_confirm_shot_bar -------------
    def test_step1_post_confirm_does_not_write_pre_confirm_shot_bar(self):
        """
        Post-confirm shot (legacy Step 1 criterion).  The leg carries
        `fired=FIRED_YES_SHOT, shot_bar=dec`; `pre_confirm_shot_bar`
        must remain -1 after linkage because a post-confirm shot is
        recorded on the legacy `shot_bar` slot and has no pre-confirm
        counterpart.
        """
        lg = _leg(
            leg_id=7, start_bar=0, end_bar=4, confirm_bar=5,
            fired=FIRED_YES_SHOT, arm_bar=5, shot_bar=10,
            arm_source=ARM_SRC_A,
            _cand_leg_id_at_confirm=3,
        )
        trades = _trades_df((555, 11))  # O2O → dec=10
        cand_id_arr = np.zeros(20, dtype=np.int64)  # all zeros; leg has 3
        linked = _link_trades_to_legs(
            trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
            zz_cand_leg_id=cand_id_arr,
        )
        lg2 = linked[0]
        assert lg2.trade_id_if_fired == 555
        assert int(lg2.shot_bar) == 10
        assert int(lg2.pre_confirm_shot_bar) == -1
        assert int(lg2.pre_confirm_arm_bar) == -1

    # ---- R-3 (a): io preserves core-set pre_confirm_shot_bar ---------
    def test_step2_keeps_core_set_pre_confirm_shot_bar_when_agreeing(self):
        """
        RP-1C behaviour: core already wrote `pre_confirm_shot_bar=dec`.
        The io Step 2 path links the trade and must NOT mutate the
        already-correct `pre_confirm_shot_bar` value (dataclasses.replace
        with the same value is semantically OK, but the value must
        remain equal to `dec`).
        """
        lg = _leg(
            leg_id=42, start_bar=0, end_bar=18, confirm_bar=20,
            fired=FIRED_NONE, arm_bar=-1, shot_bar=-1,
            arm_source=ARM_SRC_A,
            armed_by_candidate=True,
            pre_confirm_arm_bar=12,
            pre_confirm_shot_bar=15,  # core-set (RP-1C)
            _cand_leg_id_at_confirm=5,
        )
        trades = _trades_df((101, 16))  # O2O → dec=15, matches core value
        cand_id_arr = np.full(30, -1, dtype=np.int64)
        cand_id_arr[10:20] = 5

        linked = _link_trades_to_legs(
            trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
            zz_cand_leg_id=cand_id_arr,
        )
        lg2 = linked[0]
        assert lg2.trade_id_if_fired == 101
        assert int(lg2.pre_confirm_shot_bar) == 15
        # Legacy fields untouched.
        assert int(lg2.shot_bar) == -1
        assert int(lg2.arm_bar) == -1

    # ---- R-3 (b): legacy backfill path still works -------------------
    def test_step2_legacy_backfill_sets_pre_confirm_shot_bar_when_minus1(self):
        """
        Legacy call-site (fixture test, pre-RP-1C snapshot, etc.):
        core did not run, so `pre_confirm_shot_bar == -1` on the leg.
        Step 2 backfills it from the trade's decision_bar.  This keeps
        TP-27 / TP-16 fixture tests working.
        """
        lg = _leg(
            leg_id=42, start_bar=0, end_bar=18, confirm_bar=20,
            fired=FIRED_NONE, arm_bar=-1, shot_bar=-1,
            arm_source=ARM_SRC_A,
            armed_by_candidate=True,
            pre_confirm_arm_bar=12,
            pre_confirm_shot_bar=-1,  # not set by core (legacy / test fixture)
            _cand_leg_id_at_confirm=5,
        )
        trades = _trades_df((202, 16))  # O2O → dec=15
        cand_id_arr = np.full(30, -1, dtype=np.int64)
        cand_id_arr[10:20] = 5

        linked = _link_trades_to_legs(
            trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
            zz_cand_leg_id=cand_id_arr,
        )
        lg2 = linked[0]
        assert lg2.trade_id_if_fired == 202
        assert int(lg2.pre_confirm_shot_bar) == 15

    # ---- R-3 (c): io↔core divergence raises --------------------------
    def test_step2_raises_on_pre_confirm_shot_bar_divergence(self):
        """
        Core wrote `pre_confirm_shot_bar=14` (RP-1C) but the io trade
        pipeline presents `decision_bar=15` for the same leg.  This
        indicates a core↔io divergence that must not be silently
        squashed — raise RuntimeError so the caller investigates.
        """
        lg = _leg(
            leg_id=42, start_bar=0, end_bar=18, confirm_bar=20,
            fired=FIRED_NONE, arm_bar=-1, shot_bar=-1,
            arm_source=ARM_SRC_A,
            armed_by_candidate=True,
            pre_confirm_arm_bar=12,
            pre_confirm_shot_bar=14,  # core says 14
            _cand_leg_id_at_confirm=5,
        )
        trades = _trades_df((303, 16))  # O2O → dec=15 (disagrees)
        cand_id_arr = np.full(30, -1, dtype=np.int64)
        cand_id_arr[10:20] = 5

        with pytest.raises(RuntimeError, match="disagrees"):
            _link_trades_to_legs(
                trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
                zz_cand_leg_id=cand_id_arr,
            )

    # ---- LX-08: mutual-exclusion on a trade that could match both ----
    def test_lx08_mutual_exclusion_step1_wins_no_step2_link(self):
        """
        Construct a pair of legs such that one trade could in principle
        match BOTH:
          - Step 1 on leg A:  fired=YES_SHOT, shot_bar=dec
          - Step 2 on leg B:  _cand_leg_id_at_confirm matches
                              zz_cand_leg_id[dec] and dec is in
                              [start_bar, confirm_bar)
        The `continue` after Step 1 must short-circuit Step 2 — leg A
        gets the trade, leg B stays unlinked.  The end-of-pass LX-08
        invariant also verifies that the two sets are disjoint.
        """
        # Leg A — post-confirm shot at dec=10.
        lg_a = _leg(
            leg_id=10, start_bar=0, end_bar=4, confirm_bar=5,
            fired=FIRED_YES_SHOT, arm_bar=5, shot_bar=10,
            arm_source=ARM_SRC_A,
            _cand_leg_id_at_confirm=1,
        )
        # Leg B — pre-confirm window covering bar 10.
        lg_b = _leg(
            leg_id=20, start_bar=0, end_bar=18, confirm_bar=20,
            fired=FIRED_NONE, arm_bar=-1, shot_bar=-1,
            arm_source=ARM_SRC_B,
            armed_by_candidate=True,
            pre_confirm_arm_bar=8,
            _cand_leg_id_at_confirm=99,
        )
        trades = _trades_df((700, 11))  # O2O → dec=10
        # cand_leg_id array says bar 10 is inside candidate 99 → Step 2
        # would match leg B in the absence of mutual-exclusion.
        cand_id_arr = np.full(30, -1, dtype=np.int64)
        cand_id_arr[5:20] = 99

        linked = _link_trades_to_legs(
            trades, (lg_a, lg_b), ExecutionModel.OPEN_TO_OPEN,
            zz_cand_leg_id=cand_id_arr,
        )
        assert len(linked) == 2
        lg_a_linked, lg_b_linked = linked
        # Step 1 wins.
        assert lg_a_linked.trade_id_if_fired == 700
        assert int(lg_a_linked.pre_confirm_shot_bar) == -1
        # Step 2 must NOT also link this trade.
        assert lg_b_linked.trade_id_if_fired is None
        assert int(lg_b_linked.pre_confirm_shot_bar) == -1

    # ---- R-1/R-5: post-confirm shot from a pre-confirm session -------
    def test_post_confirm_shot_from_preconfirm_session_links_via_step1(self):
        """
        RP-1A scenario: session starts pre-confirm, its owning leg
        confirms at bar c, the session transitions to post-confirm
        ownership, and a ST flip fires YES_SHOT at bar t >= c.  Core
        writes this to the owning leg's LEGACY slot:
            fired=FIRED_YES_SHOT, shot_bar=t, arm_bar=c.

        For the io pipeline this is a plain Step 1 match; the trade
        must NOT fall into the Step 2 branch even though the leg also
        carries `armed_by_candidate=True` (the pre-confirm flush from
        Phase-3).  Observable contract:
            - trade_id_if_fired set via Step 1
            - pre_confirm_shot_bar stays -1 (the shot was post-confirm)
            - entry_vs_leg_phase resolves to "post_confirm"
            - bars_from_leg_confirm_to_entry >= 0
            - triggering_arm_source == "A" (from lg.arm_source)
        """
        lg = _leg(
            leg_id=88, start_bar=0, end_bar=4, confirm_bar=5,
            fired=FIRED_YES_SHOT, arm_bar=5, shot_bar=8,  # post-confirm shot
            arm_source=ARM_SRC_A,
            armed_by_candidate=True,                     # RP-1A flush
            pre_confirm_arm_bar=3,
            pre_confirm_shot_bar=-1,                     # pure post-confirm
            _cand_leg_id_at_confirm=4,
        )
        trades = _trades_df((801, 9))  # O2O → dec=8
        cand_id_arr = np.zeros(20, dtype=np.int64)
        cand_id_arr[:] = 4  # would match Step 2 if Step 1 didn't fire

        linked = _link_trades_to_legs(
            trades, (lg,), ExecutionModel.OPEN_TO_OPEN,
            zz_cand_leg_id=cand_id_arr,
        )
        lg2 = linked[0]
        assert lg2.trade_id_if_fired == 801
        assert int(lg2.shot_bar) == 8
        assert int(lg2.pre_confirm_shot_bar) == -1  # R-4 reaffirmed

        # Enrichment must see this as post-confirm.
        n = 20
        fd = {
            "zz_leg_direction":   np.full(n, LEG_DIR_UP, dtype=np.int8),
            "zz_cand_height_pct": np.full(n, 0.02, dtype=np.float64),
            "zz_cand_leg_id":     np.full(n, 4, dtype=np.int64),
            "zz_arm_source":      np.full(n, ARM_SRC_A, dtype=np.int8),
        }
        enriched = _enrich_trades_with_leg_columns(
            trades, [lg2], is_o2o=True, fd=fd,
        )
        row = enriched.iloc[0]
        assert row["entry_vs_leg_phase"] == "post_confirm"
        assert int(row["bars_from_leg_confirm_to_entry"]) == (8 - 5)
        assert row["triggering_arm_source"] == "A"
        assert int(row["triggering_leg_id"]) == 88
