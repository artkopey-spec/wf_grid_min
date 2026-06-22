"""
Tests for extract_trades() — last-bar transition coverage.

BUG-01 fix verification: the main loop now iterates over range(n+1) so that
the transition positions[n-1] → positions[n] is handled the same way as any
other transition inside the series.

Tested scenarios
----------------
A  Reversal  ±1 → ∓1  at the last index  (BUG-01 core case)
B  Exit      ±1 → 0   at the last index
C  Entry     0  → ±1  at the last index  (was partially handled by old edge-case block)
D  No change at the last index           (regression — must not add phantom trade)
E  Commission invariant across all scenarios
F  F-16 reversal-split is applied to the last-index reversal pair
G  Regression — existing mid-series logic is unaffected
"""

import numpy as np
import pandas as pd
import pytest

import supertrend_optimizer.core.trades as trades_module
from supertrend_optimizer.core.trades import extract_trades


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(positions_list, prices_list, commission_rate=0.001):
    """
    Build minimal inputs for extract_trades from plain Python lists.

    positions_list : length = n + 1
    prices_list    : length = n + 1  (same as positions)
    returns        : length = n  (all-zero so price PnL is always 0)
    """
    n = len(positions_list) - 1
    positions = np.array(positions_list, dtype=np.int8)
    execution_prices = np.array(prices_list, dtype=np.float64)
    returns = np.zeros(n, dtype=np.float64)
    index = pd.RangeIndex(len(positions_list))
    trend = np.zeros(len(positions_list), dtype=np.int8)
    return positions, returns, execution_prices, index, commission_rate, trend


def _total_commission_from_positions(positions, commission_rate):
    """Compute total commission = sum(abs(diff(positions))) * rate."""
    return float(np.sum(np.abs(np.diff(positions))) * commission_rate)


def test_extract_trades_uses_closed_leg_trade_economics_helper(monkeypatch):
    positions, returns, prices, index, commission_rate, trend = _make_inputs(
        [0, 1, -1, -1, 0],
        [100, 100, 101, 101, 99],
        commission_rate=0.001,
    )
    original = trades_module.closed_leg_trade_economics
    calls = []

    def spy(entry_idx, exit_idx, direction, positions_arg, prices_arg, commission_arg):
        economics = original(
            entry_idx,
            exit_idx,
            direction,
            positions_arg,
            prices_arg,
            commission_arg,
        )
        calls.append((entry_idx, exit_idx, direction, economics))
        return economics

    monkeypatch.setattr(trades_module, "closed_leg_trade_economics", spy)

    df = extract_trades(positions, returns, prices, index, commission_rate, trend)

    assert len(calls) == len(df)
    for (_, _, _, economics), (_, row) in zip(calls, df.iterrows()):
        assert row["gross_pnl_pct"] == economics["gross_pnl_pct"]
        assert row["commission_pct"] == economics["commission_pct"]
        assert row["net_pnl_pct"] == economics["net_pnl_pct"]


# ---------------------------------------------------------------------------
# A — Reversal ±1 → ∓1 on the LAST index
# ---------------------------------------------------------------------------

class TestReversalOnLastIndex:
    """
    positions = [0, 1, 1, -1]   (n=3 returns, n+1=4 positions)
    Transitions:
      i=0: 0→1  open LONG
      i=1: 1→1  hold
      i=2: 1→1  hold        (last iteration in OLD loop: i in range(3))
      i=3: 1→-1 REVERSAL    (was MISSED by old range(n) loop)
    """

    POSITIONS = [0, 1, 1, -1]
    PRICES    = [100.0, 101.0, 102.0, 103.0]
    COMM      = 0.001

    def _run(self):
        args = _make_inputs(self.POSITIONS, self.PRICES, self.COMM)
        return extract_trades(*args)

    def test_two_trades_created(self):
        df = self._run()
        # Must produce exactly 2 trades: closing LONG + opening SHORT (pending)
        assert len(df) == 2, f"Expected 2 trades, got {len(df)}:\n{df}"

    def test_first_trade_is_long(self):
        df = self._run()
        assert df.iloc[0]['direction'] == 'LONG'

    def test_first_trade_exit_index(self):
        df = self._run()
        # LONG closes at index 3 (the reversal point)
        assert df.iloc[0]['exit_index'] == 3

    def test_second_trade_is_short_pending(self):
        df = self._run()
        row = df.iloc[1]
        assert row['direction'] == 'SHORT'
        assert row['entry_index'] == 3
        assert row['exit_index'] == 3
        assert row['bars_held'] == 0

    def test_f16_split_applied(self):
        """Half of the reversal commission must transfer to the opening trade."""
        df = self._run()
        # reversal bar diff = |-1 - 1| = 2  → commission_per_bar = 2 * rate
        # After F-16: each trade carries 1 * rate
        half = self.COMM * 100.0          # in percent
        assert df.iloc[0]['commission_pct'] == pytest.approx(half + half, abs=1e-9), \
            "Closing trade should carry its own 1× (in-trade) + 1× (reversal) commission"
        # Wait — let's think exactly:
        # commission_per_bar[0] = |pos[1]-pos[0]| * rate = 1*rate  (open LONG)
        # commission_per_bar[1] = |pos[2]-pos[1]| * rate = 0        (hold)
        # commission_per_bar[2] = |pos[3]-pos[2]| * rate = 2*rate   (reversal)
        # LONG interval [entry=0, exit=3): bars 0,1,2
        #   sum before F-16 split = 1*rate + 0 + 2*rate = 3*rate
        #   LONG also gets opening_commission moved IN (entry=0 so entry-1=-1 → skipped)
        #   After opening_comm attribution: nothing (positions[-1] not accessible)
        #   After F-16 split: closing gets -1*rate  → 3*rate - 1*rate = 2*rate
        # SHORT pending [entry=3, exit=3): no bars in interval → 0 raw
        #   After F-16 split: opening gets +1*rate → 1*rate
        expected_long_comm = (1 + 2 - 1) * self.COMM * 100.0   # 2 * rate in %
        expected_short_comm = 1 * self.COMM * 100.0             # 1 * rate in %
        assert df.iloc[0]['commission_pct'] == pytest.approx(expected_long_comm, abs=1e-9)
        assert df.iloc[1]['commission_pct'] == pytest.approx(expected_short_comm, abs=1e-9)

    def test_commission_invariant(self):
        """sum(trade commission_pct) == total bar-level commission in percent."""
        df = self._run()
        positions = np.array(self.POSITIONS, dtype=np.int8)
        expected = _total_commission_from_positions(positions, self.COMM) * 100.0
        actual = df['commission_pct'].sum()
        assert actual == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# B — Exit ±1 → 0 on the LAST index
# ---------------------------------------------------------------------------

class TestExitToFlatOnLastIndex:
    """
    positions = [0, 1, 1, 0]
    Transitions: 0→1 open, hold, hold, 1→0 close (last transition).
    OLD code: close handled by "close last trade" block with exit_idx=n.
    NEW code: handled inside loop at i=3.
    Both should produce 1 trade.
    """

    POSITIONS = [0, 1, 1, 0]
    PRICES    = [100.0, 101.0, 102.0, 103.0]
    COMM      = 0.001

    def _run(self):
        args = _make_inputs(self.POSITIONS, self.PRICES, self.COMM)
        return extract_trades(*args)

    def test_one_trade_created(self):
        df = self._run()
        assert len(df) == 1, f"Expected 1 trade, got {len(df)}:\n{df}"

    def test_trade_closes_at_index_3(self):
        df = self._run()
        assert df.iloc[0]['exit_index'] == 3

    def test_commission_invariant(self):
        df = self._run()
        positions = np.array(self.POSITIONS, dtype=np.int8)
        expected = _total_commission_from_positions(positions, self.COMM) * 100.0
        actual = df['commission_pct'].sum()
        assert actual == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# C — Entry 0 → ±1 on the LAST index (pending trade)
# ---------------------------------------------------------------------------

class TestEntryOnLastIndex:
    """
    positions = [0, 0, 1]  (n=2, n+1=3)
    Transitions: flat the whole time, then 0→1 at index 2.
    Must produce 1 pending trade with bars_held=0.
    """

    POSITIONS = [0, 0, 1]
    PRICES    = [100.0, 101.0, 102.0]
    COMM      = 0.001

    def _run(self):
        args = _make_inputs(self.POSITIONS, self.PRICES, self.COMM)
        return extract_trades(*args)

    def test_one_pending_trade_created(self):
        df = self._run()
        assert len(df) == 1, f"Expected 1 pending trade, got {len(df)}:\n{df}"

    def test_pending_trade_properties(self):
        df = self._run()
        row = df.iloc[0]
        assert row['direction'] == 'LONG'
        assert row['entry_index'] == 2
        assert row['exit_index'] == 2
        assert row['bars_held'] == 0
        assert row['gross_pnl_pct'] == pytest.approx(0.0, abs=1e-9)

    def test_commission_invariant(self):
        """
        The 0→1 transition creates commission_per_bar[1] = 1*rate (bar index 1,
        because positions[2]-positions[1] = 1).
        Opening-commission attribution pulls that into the trade.
        """
        df = self._run()
        positions = np.array(self.POSITIONS, dtype=np.int8)
        expected = _total_commission_from_positions(positions, self.COMM) * 100.0
        actual = df['commission_pct'].sum()
        assert actual == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# D — No change on the last index (regression: no phantom trade)
# ---------------------------------------------------------------------------

class TestNoChangeOnLastIndex:
    """
    positions = [0, 1, 1]
    Old loop: last bar is i=1 (range(2)), sees 1→1, no change.
    positions[2] = 1 = positions[1]: no change.
    Must produce exactly 1 trade (held till end).
    """

    POSITIONS = [0, 1, 1]
    PRICES    = [100.0, 101.0, 102.0]
    COMM      = 0.001

    def _run(self):
        args = _make_inputs(self.POSITIONS, self.PRICES, self.COMM)
        return extract_trades(*args)

    def test_one_trade_no_phantom(self):
        df = self._run()
        assert len(df) == 1, f"Expected 1 trade, got {len(df)}:\n{df}"

    def test_trade_held_till_end(self):
        df = self._run()
        # exit_idx should be n = 2
        assert df.iloc[0]['exit_index'] == 2

    def test_commission_invariant(self):
        df = self._run()
        positions = np.array(self.POSITIONS, dtype=np.int8)
        expected = _total_commission_from_positions(positions, self.COMM) * 100.0
        actual = df['commission_pct'].sum()
        assert actual == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# E — Commission invariant on complex sequence with mid-series reversal
# ---------------------------------------------------------------------------

class TestCommissionInvariantComplexSequence:
    """
    Regression: make sure commission invariant holds for a sequence that
    exercises mid-series reversal, flat gaps, and a last-index transition.

    positions = [0, 1, 1, -1, -1, 0, -1]  (n=6)
    Transitions:
      0→1   open LONG
      1→1   hold
      1→-1  reversal (mid-series)
      -1→-1 hold
      -1→0  close SHORT
      0→-1  open SHORT at last index  (pending)
    """

    POSITIONS = [0, 1, 1, -1, -1, 0, -1]
    PRICES    = [100.0, 101.0, 102.0, 103.0, 102.0, 101.0, 100.0]
    COMM      = 0.001

    def _run(self):
        args = _make_inputs(self.POSITIONS, self.PRICES, self.COMM)
        return extract_trades(*args)

    def test_commission_invariant(self):
        df = self._run()
        positions = np.array(self.POSITIONS, dtype=np.int8)
        expected = _total_commission_from_positions(positions, self.COMM) * 100.0
        actual = df['commission_pct'].sum()
        assert actual == pytest.approx(expected, abs=1e-9), \
            f"Commission invariant broken: expected={expected:.6f}%, actual={actual:.6f}%"

    def test_correct_number_of_trades(self):
        df = self._run()
        # LONG, SHORT (from reversal), SHORT (pending at last bar)
        assert len(df) == 3, f"Expected 3 trades, got {len(df)}:\n{df}"

    def test_last_trade_is_pending(self):
        df = self._run()
        last = df.iloc[-1]
        assert last['bars_held'] == 0
        assert last['direction'] == 'SHORT'
        assert last['entry_index'] == 6


# ---------------------------------------------------------------------------
# F — Symmetric reversal at last index: SHORT → LONG
# ---------------------------------------------------------------------------

class TestShortToLongReversalOnLastIndex:
    """
    positions = [0, -1, -1, 1]  SHORT running, then reversed to LONG.
    """

    POSITIONS = [0, -1, -1, 1]
    PRICES    = [100.0, 99.0, 98.0, 97.0]
    COMM      = 0.001

    def _run(self):
        args = _make_inputs(self.POSITIONS, self.PRICES, self.COMM)
        return extract_trades(*args)

    def test_two_trades_created(self):
        df = self._run()
        assert len(df) == 2, f"Expected 2 trades, got {len(df)}:\n{df}"

    def test_directions(self):
        df = self._run()
        assert df.iloc[0]['direction'] == 'SHORT'
        assert df.iloc[1]['direction'] == 'LONG'

    def test_commission_invariant(self):
        df = self._run()
        positions = np.array(self.POSITIONS, dtype=np.int8)
        expected = _total_commission_from_positions(positions, self.COMM) * 100.0
        actual = df['commission_pct'].sum()
        assert actual == pytest.approx(expected, abs=1e-9)

    def test_f16_split_on_last_reversal(self):
        df = self._run()
        half = self.COMM * 100.0
        # commission_per_bar[0] = 1*rate (0→-1 open)
        # commission_per_bar[1] = 0      (hold)
        # commission_per_bar[2] = 2*rate (reversal -1→1)
        # SHORT interval [1,3): bars 1,2  → raw = 0 + 2*rate = 2*rate
        # Opening comm attr: entry=1, positions[0]=0 → pulls in commission_per_bar[0]=1*rate
        # After attr: SHORT comm = 1*rate (opening) + 2*rate (in-trade) = 3*rate
        # After F-16 split: SHORT comm = 3*rate - 1*rate = 2*rate
        # LONG pending: 0 raw + F-16: +1*rate = 1*rate
        expected_short = (1 + 2 - 1) * self.COMM * 100.0
        expected_long  = 1 * self.COMM * 100.0
        assert df.iloc[0]['commission_pct'] == pytest.approx(expected_short, abs=1e-9)
        assert df.iloc[1]['commission_pct'] == pytest.approx(expected_long, abs=1e-9)


# ---------------------------------------------------------------------------
# G — Mid-series logic regression: reversal NOT on last index
# ---------------------------------------------------------------------------

class TestMidSeriesReversalRegressionUnchanged:
    """
    positions = [0, 1, -1, -1, 0]  reversal at index 2 (mid-series)
    Must behave identically to before the fix.
    """

    POSITIONS = [0, 1, -1, -1, 0]
    PRICES    = [100.0, 101.0, 100.0, 99.0, 98.0]
    COMM      = 0.001

    def _run(self):
        args = _make_inputs(self.POSITIONS, self.PRICES, self.COMM)
        return extract_trades(*args)

    def test_two_trades(self):
        df = self._run()
        assert len(df) == 2

    def test_long_then_short(self):
        df = self._run()
        assert df.iloc[0]['direction'] == 'LONG'
        assert df.iloc[1]['direction'] == 'SHORT'

    def test_commission_invariant(self):
        df = self._run()
        positions = np.array(self.POSITIONS, dtype=np.int8)
        expected = _total_commission_from_positions(positions, self.COMM) * 100.0
        actual = df['commission_pct'].sum()
        assert actual == pytest.approx(expected, abs=1e-9)

    def test_f16_split_mid_series(self):
        df = self._run()
        # commission_per_bar[0] = 1*rate (0→1)
        # commission_per_bar[1] = 2*rate (1→-1 reversal)
        # commission_per_bar[2] = 0      (hold)
        # commission_per_bar[3] = 1*rate (-1→0)
        # LONG interval [entry=1, exit=2): bar 1 → raw=2*rate
        # opening attr: entry=1, positions[0]=0 → pulls bar[0]=1*rate
        # After attr: LONG=3*rate
        # After F-16: LONG = 3*rate - 1*rate = 2*rate
        # SHORT interval [entry=2, exit=4): bars 2,3 → raw=0+1*rate=1*rate
        # opening attr: entry=2, positions[1]=1 != 0 → nothing
        # After F-16: SHORT = 1*rate + 1*rate = 2*rate
        expected_long  = 2 * self.COMM * 100.0
        expected_short = 2 * self.COMM * 100.0
        assert df.iloc[0]['commission_pct'] == pytest.approx(expected_long, abs=1e-9)
        assert df.iloc[1]['commission_pct'] == pytest.approx(expected_short, abs=1e-9)


# ---------------------------------------------------------------------------
# H — n=1 edge case (minimal non-trivial input)
# ---------------------------------------------------------------------------

class TestMinimalOnebar:
    """
    n=1: returns=[0.0], positions=[0,1], prices=[100,101]
    Single open-only trade: 0→1 at index 1 = last index.
    """

    def test_pending_trade_n1(self):
        positions = np.array([0, 1], dtype=np.int8)
        execution_prices = np.array([100.0, 101.0])
        returns = np.array([0.0])
        index = pd.RangeIndex(2)
        trend = np.zeros(2, dtype=np.int8)
        df = extract_trades(positions, returns, execution_prices, index, 0.001, trend)
        assert len(df) == 1
        assert df.iloc[0]['bars_held'] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
