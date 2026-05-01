"""
Unit tests for OOS boundary force-flat (§4.4 of the fix plan, OOS1).

Covers the acceptance criteria from the plan's Unit/Integration list:

  - carry-in win;
  - carry-in loss;
  - carry-in flat (exit exactly at boundary);
  - reversal exactly at boundary (not carry-in);
  - only carry-in exposure in OOS;
  - train metrics are not recalculated by force-flat;
  - OOS bar and trade economics are reconciled under force-flat.

The helper under test — ``_apply_oos_force_flat`` — is pure: it takes the
already-trimmed OOS arrays, the extended-slice ``trades_df``, and the
``oos_boundary`` and returns copies with carry-in exposure zeroed on the
OOS side.  Trade-level filter (``entry_index >= oos_boundary``) remains
where it is in the pipeline; we also assert no double-filter here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wf_grid.wf.step_executor import _apply_oos_force_flat


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

def _make_trades(rows: list[dict]) -> pd.DataFrame:
    """Build a trades_df with at least entry_index / exit_index / net_pnl_pct."""
    base_cols = {
        "trade_id": 0,
        "direction": "LONG",
        "entry_time": pd.Timestamp("2020-01-01"),
        "entry_price": 100.0,
        "exit_time": pd.Timestamp("2020-01-02"),
        "exit_price": 101.0,
        "bars_held": 1,
        "gross_pnl_pct": 0.0,
        "commission_pct": 0.0,
        "net_pnl_pct": 0.0,
    }
    filled = []
    for i, r in enumerate(rows):
        d = dict(base_cols)
        d.update(r)
        d["trade_id"] = d.get("trade_id", i)
        filled.append(d)
    return pd.DataFrame(filled)


# ---------------------------------------------------------------------------
# Baseline: no-op paths
# ---------------------------------------------------------------------------

class TestNoOpCases:
    def test_none_trades_df_returns_unchanged(self):
        r = np.array([0.01, -0.02, 0.03])
        p = np.array([1, 1, 0], dtype=np.int8)
        e = np.array([1.0, 1.01, 1.01 * 0.98, 1.01 * 0.98 * 1.03])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, None, oos_boundary=10)

        np.testing.assert_array_equal(r, r2)
        np.testing.assert_array_equal(p, p2)
        np.testing.assert_array_equal(e, e2)

    def test_empty_trades_df_returns_unchanged(self):
        r = np.array([0.01, -0.02])
        p = np.array([1, 0], dtype=np.int8)
        e = np.array([1.0, 1.01, 1.01 * 0.98])

        r2, p2, e2 = _apply_oos_force_flat(
            r, p, e, pd.DataFrame(columns=["entry_index", "exit_index"]),
            oos_boundary=5,
        )

        np.testing.assert_array_equal(r, r2)
        np.testing.assert_array_equal(p, p2)
        np.testing.assert_array_equal(e, e2)

    def test_all_trades_start_in_oos_no_force_flat(self):
        """No carry-in: every trade has entry_index >= oos_boundary."""
        r = np.array([0.01, -0.02, 0.03])
        p = np.array([1, 1, 0], dtype=np.int8)
        e = np.array([1.0, 1.01, 0.9898, 1.0195])

        trades = _make_trades([
            {"entry_index": 5, "exit_index": 6},  # entry == oos_boundary
            {"entry_index": 7, "exit_index": 9},  # both > oos_boundary
        ])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        np.testing.assert_array_equal(r, r2)
        np.testing.assert_array_equal(p, p2)
        np.testing.assert_array_equal(e, e2)

    def test_function_does_not_mutate_inputs(self):
        r = np.array([0.01, -0.02, 0.03], dtype=np.float64)
        p = np.array([1, 1, 0], dtype=np.int8)
        e = np.array([1.0, 1.01, 0.9898, 1.0195])
        r_copy, p_copy, e_copy = r.copy(), p.copy(), e.copy()

        trades = _make_trades([
            {"entry_index": 4, "exit_index": 7, "net_pnl_pct": 0.5},
        ])
        _ = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        np.testing.assert_array_equal(r, r_copy)
        np.testing.assert_array_equal(p, p_copy)
        np.testing.assert_array_equal(e, e_copy)


# ---------------------------------------------------------------------------
# Carry-in win / loss / flat-at-boundary
# ---------------------------------------------------------------------------

class TestCarryInWin:
    def test_carry_in_win_zeroed_on_oos_side(self):
        """Winning carry-in trade: its OOS-side bars become flat (returns=0, positions=0)."""
        # Extended slice has 10 bars. oos_boundary=5.
        # Carry-in trade: entry_index=3, exit_index=7 (exclusive exit bar 7).
        # Local OOS indices for carry-in tail = [0..1] (exit_local = 7-5 = 2).
        r = np.array([0.05, 0.06, -0.01, 0.02], dtype=np.float64)  # 4 returns
        p = np.array([1, 1, 0, 1], dtype=np.int8)                   # 4 positions
        e = np.array([1.0, 1.05, 1.113, 1.102, 1.1244])            # 5 equity pts

        trades = _make_trades([
            {"entry_index": 3, "exit_index": 7, "net_pnl_pct": 10.0},  # carry-in win
        ])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        # exit_local = 2 => zero local returns [0:2] and positions [0:2]
        assert r2[0] == 0.0 and r2[1] == 0.0
        assert p2[0] == 0 and p2[1] == 0
        # OOS-originated bars after carry-in tail unchanged
        assert r2[2] == pytest.approx(-0.01)
        assert r2[3] == pytest.approx(0.02)
        assert p2[3] == 1
        # Equity rebuilt: equity[0]=1.0, equity[1]=1.0, equity[2]=1.0,
        # equity[3]=1.0*(1-0.01)=0.99, equity[4]=0.99*1.02=1.0098
        assert e2[0] == pytest.approx(1.0)
        assert e2[1] == pytest.approx(1.0)
        assert e2[2] == pytest.approx(1.0)
        assert e2[3] == pytest.approx(0.99)
        assert e2[4] == pytest.approx(0.99 * 1.02)


class TestCarryInLoss:
    def test_carry_in_loss_zeroed_on_oos_side(self):
        """Losing carry-in trade: its OOS-side bars become flat."""
        r = np.array([-0.05, -0.02, 0.01, 0.02], dtype=np.float64)
        p = np.array([1, 1, 0, 0], dtype=np.int8)
        e = np.array([1.0, 0.95, 0.931, 0.94031, 0.959])

        trades = _make_trades([
            {"entry_index": 2, "exit_index": 7, "net_pnl_pct": -3.0},
        ])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        # exit_local = 2 => zero local [0:2]
        assert r2[0] == 0.0 and r2[1] == 0.0
        assert p2[0] == 0 and p2[1] == 0
        # Remaining bars unchanged
        assert r2[2] == pytest.approx(0.01)
        assert r2[3] == pytest.approx(0.02)
        # Equity rebuild: [1, 1, 1, 1.01, 1.01*1.02]
        assert e2[0] == pytest.approx(1.0)
        assert e2[1] == pytest.approx(1.0)
        assert e2[2] == pytest.approx(1.0)
        assert e2[3] == pytest.approx(1.01)
        assert e2[4] == pytest.approx(1.01 * 1.02)


class TestCarryInFlatAtBoundary:
    def test_exit_exactly_at_boundary_is_no_op(self):
        """Carry-in trade closing exactly on the boundary: nothing to force-flat.

        Its closing commission sits in returns[oos_boundary-1], which is NOT
        part of the OOS slice.  The OOS bar arrays already contain only
        OOS-originated data, so force-flat must be a no-op.
        """
        r = np.array([0.01, 0.02, -0.01], dtype=np.float64)
        p = np.array([0, 1, 1], dtype=np.int8)
        e = np.array([1.0, 1.01, 1.01 * 1.02, 1.01 * 1.02 * 0.99])

        trades = _make_trades([
            # entry=3 < boundary=5, exit=5 (== boundary, exit_local = 0)
            {"entry_index": 3, "exit_index": 5, "net_pnl_pct": 1.5},
        ])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        np.testing.assert_array_equal(r, r2)
        np.testing.assert_array_equal(p, p2)
        np.testing.assert_array_almost_equal(e, e2)


# ---------------------------------------------------------------------------
# Reversal at boundary — new trade, not carry-in
# ---------------------------------------------------------------------------

class TestReversalAtBoundary:
    def test_trade_entering_at_boundary_is_not_carry_in(self):
        """entry_index == oos_boundary is OOS-originated (>=), not carry-in.

        Force-flat must leave such a trade's bars untouched.
        """
        r = np.array([0.02, 0.03, -0.01, 0.04], dtype=np.float64)
        p = np.array([-1, -1, 0, 0], dtype=np.int8)
        # equity values are not used to derive force-flat decisions
        e = np.array([1.0, 1.02, 1.0506, 1.0401, 1.0817])

        trades = _make_trades([
            # new trade opens exactly at boundary (oos-originated, not carry-in)
            {"entry_index": 5, "exit_index": 7, "net_pnl_pct": 5.0},
        ])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        np.testing.assert_array_equal(r, r2)
        np.testing.assert_array_equal(p, p2)
        np.testing.assert_array_almost_equal(e, e2)

    def test_mixed_carry_in_and_reversal(self):
        """Carry-in trade closes, reversal opens the same bar.

        Force-flat must zero only the carry-in tail — the reversal trade
        (entry_index == carry_in exit_index >= boundary) stays intact.
        """
        # Carry-in: entry=3, exit=7  -> exit_local=2, zero local [0:2]
        # Reversal: entry=7, exit=9  -> OOS-originated, untouched
        r = np.array([-0.02, -0.03, 0.05, 0.06, -0.01], dtype=np.float64)
        p = np.array([1, 1, -1, -1, 0], dtype=np.int8)
        e = np.array([1.0, 0.98, 0.9506, 0.99813, 1.05802, 1.04744])

        trades = _make_trades([
            {"entry_index": 3, "exit_index": 7, "net_pnl_pct": -5.0},
            {"entry_index": 7, "exit_index": 9, "net_pnl_pct": 11.0},
        ])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        # Carry-in tail zeroed
        assert r2[0] == 0.0 and r2[1] == 0.0
        assert p2[0] == 0 and p2[1] == 0
        # Reversal bars preserved
        assert r2[2] == pytest.approx(0.05)
        assert r2[3] == pytest.approx(0.06)
        assert r2[4] == pytest.approx(-0.01)
        assert p2[2] == -1 and p2[3] == -1


# ---------------------------------------------------------------------------
# Only carry-in exposure in OOS
# ---------------------------------------------------------------------------

class TestOnlyCarryInExposure:
    def test_all_oos_bars_are_carry_in_tail(self):
        """If the only trade overlapping OOS is the carry-in one and it fills
        the entire OOS slice, all OOS bars become flat (zero returns, zero
        positions) and equity stays at 1.0 throughout.
        """
        # OOS slice has 3 bars (indices 5..7), all carry-in (exit_index=8).
        r = np.array([0.02, -0.01, 0.03], dtype=np.float64)
        p = np.array([1, 1, 1], dtype=np.int8)
        e = np.array([1.0, 1.02, 1.02 * 0.99, 1.02 * 0.99 * 1.03])

        trades = _make_trades([
            {"entry_index": 2, "exit_index": 8, "net_pnl_pct": 4.0},
        ])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        # exit_local = 3 -> zero all 3 OOS returns and positions
        np.testing.assert_array_equal(r2, np.zeros(3))
        np.testing.assert_array_equal(p2, np.zeros(3))
        # Equity: all 1.0
        np.testing.assert_array_almost_equal(e2, np.ones(4))


# ---------------------------------------------------------------------------
# Reconciliation: bar-level economy after force-flat matches OOS-only trades
# ---------------------------------------------------------------------------

class TestBarTradeReconciliation:
    def test_bar_final_equity_reflects_only_oos_trade(self):
        """After force-flat, OOS equity growth must come only from the
        OOS-originated trade's bar returns — carry-in contribution is zero.

        Setup:
          - carry-in loss in bars [0,1] would have dropped equity if untouched;
          - OOS trade in bar [2,3] gives +0.05 cumulative return;
          - final equity after force-flat = 1 * 1.02 * 1.03 ≈ 1.0506
            (the +0.02, +0.03 returns of the OOS-originated trade).
        """
        # Carry-in bars 0..1 had losses; OOS trade bars 2..3 had wins.
        r = np.array([-0.10, -0.08, 0.02, 0.03], dtype=np.float64)
        p = np.array([1, 1, -1, -1], dtype=np.int8)
        e = np.array([1.0, 0.90, 0.828, 0.84456, 0.86990])

        trades = _make_trades([
            {"entry_index": 4, "exit_index": 7, "net_pnl_pct": -17.0},  # carry-in
            {"entry_index": 7, "exit_index": 9, "net_pnl_pct": 5.06},   # OOS-originated
        ])

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        # First 2 bars zeroed (carry-in), last 2 preserved
        assert r2[0] == 0.0 and r2[1] == 0.0
        assert r2[2] == pytest.approx(0.02)
        assert r2[3] == pytest.approx(0.03)
        # Final equity reflects ONLY OOS-originated bar returns
        assert e2[-1] == pytest.approx(1.02 * 1.03)

    def test_trade_filter_invariant_unchanged_by_force_flat(self):
        """Force-flat must not touch the trade-level filter policy
        (entry_index >= oos_boundary).  We assert no double-filter by
        applying the same filter before/after force-flat and getting
        identical results (force-flat operates on arrays, not on trades_df).
        """
        trades = _make_trades([
            {"entry_index": 3, "exit_index": 7, "net_pnl_pct": -5.0},
            {"entry_index": 7, "exit_index": 9, "net_pnl_pct": 11.0},
        ])
        boundary = 5
        oos_trades_before = trades[trades["entry_index"] >= boundary].copy()

        # Call force-flat with any arrays of sensible shape
        r = np.zeros(5)
        p = np.zeros(5, dtype=np.int8)
        e = np.ones(6)
        _ = _apply_oos_force_flat(r, p, e, trades, oos_boundary=boundary)

        # Apply the same filter after — result must be identical.
        oos_trades_after = trades[trades["entry_index"] >= boundary].copy()
        pd.testing.assert_frame_equal(
            oos_trades_before.reset_index(drop=True),
            oos_trades_after.reset_index(drop=True),
        )


# ---------------------------------------------------------------------------
# Train metrics: not recalculated by force-flat
# ---------------------------------------------------------------------------

class TestTrainNotRecalculated:
    def test_force_flat_is_not_called_on_train_path(self):
        """execute_train_step must never call _apply_oos_force_flat.

        Sanity check via source inspection: the helper name appears exactly
        once in step_executor.py (inside execute_oos_step) and not inside
        execute_train_step.  This guards against accidental wiring.
        """
        import inspect
        import wf_grid.wf.step_executor as se

        src_execute_train = inspect.getsource(se.execute_train_step)
        assert "_apply_oos_force_flat" not in src_execute_train, (
            "execute_train_step must NOT invoke _apply_oos_force_flat; "
            "force-flat is an OOS-only policy per plan §4.4."
        )

        src_execute_oos = inspect.getsource(se.execute_oos_step)
        assert "_apply_oos_force_flat" in src_execute_oos, (
            "execute_oos_step must invoke _apply_oos_force_flat after OOS trim "
            "and before calculate_all_metrics."
        )


# ---------------------------------------------------------------------------
# Edge case: carry-in trade but OOS slice is empty
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_oos_arrays(self):
        trades = _make_trades([{"entry_index": 1, "exit_index": 7}])
        r = np.array([], dtype=np.float64)
        p = np.array([], dtype=np.int8)
        e = np.array([], dtype=np.float64)

        r2, p2, e2 = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        assert r2.shape == (0,)
        assert p2.shape == (0,)
        assert e2.shape == (0,)

    def test_multiple_carry_in_trades_uses_max_exit(self):
        """If somehow multiple carry-in trades exist, the zone ends at
        the latest exit (defensive — canonical runs produce ≤ 1)."""
        r = np.array([0.05, 0.06, 0.07, 0.08, 0.09], dtype=np.float64)
        p = np.array([1, 1, 1, 1, 0], dtype=np.int8)
        e = np.ones(6)

        trades = _make_trades([
            {"entry_index": 3, "exit_index": 6, "net_pnl_pct": 1.0},  # exit_local=1
            {"entry_index": 4, "exit_index": 8, "net_pnl_pct": 2.0},  # exit_local=3 (max)
        ])

        r2, p2, _ = _apply_oos_force_flat(r, p, e, trades, oos_boundary=5)

        # exit_local = 8 - 5 = 3 -> zero indices [0:3]
        assert r2[0] == 0.0 and r2[1] == 0.0 and r2[2] == 0.0
        assert r2[3] == pytest.approx(0.08)
        assert p2[0] == 0 and p2[1] == 0 and p2[2] == 0
        assert p2[3] == 1
