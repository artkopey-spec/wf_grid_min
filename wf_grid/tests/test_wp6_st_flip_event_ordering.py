"""
WP6 unit tests — ST flip detection and event ordering.

Plan reference:
    docs/zigzag_st_implementation_plan_hybrid_v2_final.txt
        WP6, §5.5 (Event-order rule for ``OPEN_TO_OPEN``),
        §8.4 / §8.4.1 (Trade-level diagnostics indexing).

Spec reference:
    docs/zigzag_st_trade_filter_spec_v1_1.txt
        Appendix A v1.1 §3.3, §10, §15.4, §17.13–§17.14.

Scope
-----
1. Public ``detect_st_flip`` covers all 6 trend transitions:
   long flip, short flip, ``0 -> ±1`` (init), ``±1 -> 0`` (de-init),
   no-change, ``0 -> 0``.
2. ``OPEN_TO_OPEN`` event ordering is pinned through the WP5 ``apply``
   builder:
     - decision happens at ``close(t)``;
     - position change is materialised at ``open(t+1)`` (i.e. at
       ``filtered_positions[t+1]``);
     - same-bar trigger + allowed flip → entry at ``t+1``;
     - same-bar trigger + disallowed flip → flip silently skipped,
       FSM stays in ``WAIT_FIRST_ST_FLIP``.
3. ``0 -> ±1`` does not open a position in ``WAIT_FIRST_ST_FLIP`` and
   does not close a position in ``ST_STOPPING`` (non-tradable).
4. Donor ``extract_trades`` indexing is **read-only pinned** under
   ``OPEN_TO_OPEN`` — for both entry and exit:
       entry_signal_idx = max(entry_index - 1, 0)
       exit_signal_idx  = max(exit_index  - 1, 0)
   plus the edge cases ``entry_index == 0`` and
   ``pending_open_trade_at_end``.

Anti-drift (WP6)
----------------
- No mutation of ``calculate_returns`` / ``extract_trades`` / metrics.
- No post-filtering of ``trades_df``.
- No orchestrator / WF / backtest wiring.
- No ``RawBacktestArtifacts`` migration.
- No spec / plan changes.
- ``0 -> ±1`` MUST NOT be a tradable flip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core import zigzag_st_filter
from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagFSMState,
    ZigZagGlobalStats,
    ZigZagPerBar,
    ZigZagSTFilterResult,
    apply,
    detect_st_flip,
)
from supertrend_optimizer.core.trades import extract_trades


# ===========================================================================
# Light-weight ad-hoc config doubles (mirrors test_wp5_zigzag_fsm helpers).
# ===========================================================================

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
class _FilterCfgDouble:
    triggers: _TriggersDouble = field(default_factory=_TriggersDouble)
    lifecycle: _LifecycleDouble = field(default_factory=_LifecycleDouble)


def _make_filter_cfg(
    *,
    a_enabled: bool = True,
    b_enabled: bool = True,
    freeze_confirmed_legs: int = 5,
) -> _FilterCfgDouble:
    return _FilterCfgDouble(
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
    reversal_threshold: float = 0.03,
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
    candidate_height_pct: Optional[np.ndarray] = None,
    confirm_event: Optional[np.ndarray] = None,
    confirmed_leg_idx_at_t: Optional[np.ndarray] = None,
    last_confirmed_leg_height_pct: Optional[np.ndarray] = None,
    local_median_N: Optional[np.ndarray] = None,
    local_median_available: Optional[np.ndarray] = None,
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


def _positions_from_trend(trend: np.ndarray, trade_mode: str = "both") -> np.ndarray:
    """Synthesize a raw ST-style positions array (close-decision / next-open).

    ``positions[t]`` reflects ``trend[t-1]`` (open(t) is decided at
    close(t-1)).
    """
    n = len(trend)
    pos = np.zeros(n, dtype=np.int64)
    for t in range(1, n):
        prev = int(trend[t - 1])
        if prev == 0:
            pos[t] = 0
            continue
        if trade_mode == "long":
            pos[t] = max(prev, 0)
        elif trade_mode == "short":
            pos[t] = min(prev, 0)
        else:
            pos[t] = prev
    return pos


# ===========================================================================
# 1.  Public API surface — detect_st_flip is exported.
# ===========================================================================

class TestDetectStFlipPublicAPI:

    def test_detect_st_flip_in_dunder_all(self):
        assert "detect_st_flip" in zigzag_st_filter.__all__

    def test_detect_st_flip_is_module_attribute(self):
        assert hasattr(zigzag_st_filter, "detect_st_flip")
        assert callable(zigzag_st_filter.detect_st_flip)

    def test_detect_st_flip_signature_per_bar_pair(self):
        # Two-arg callable: (prev_trend, curr_trend) -> int.
        out = detect_st_flip(-1, +1)
        assert isinstance(out, int)


# ===========================================================================
# 2.  detect_st_flip — six required cases (plan §5.5 / spec §3.3, §17.14).
# ===========================================================================

class TestDetectStFlipCases:
    """The exhaustive WP6 transition matrix.

    Only ``+1 ↔ -1`` are tradable flips.  Everything else returns ``0``
    (non-tradable):
      - ``0 -> +1`` and ``0 -> -1`` are SuperTrend bootstrap / init.
      - ``+1 -> 0`` and ``-1 -> 0`` are de-init transitions.
      - Same-direction transitions are not flips.
    """

    def test_long_flip_minus_one_to_plus_one(self):
        assert detect_st_flip(-1, +1) == +1

    def test_short_flip_plus_one_to_minus_one(self):
        assert detect_st_flip(+1, -1) == -1

    def test_zero_to_plus_one_not_a_flip(self):
        assert detect_st_flip(0, +1) == 0

    def test_zero_to_minus_one_not_a_flip(self):
        assert detect_st_flip(0, -1) == 0

    def test_plus_one_to_zero_not_a_flip(self):
        assert detect_st_flip(+1, 0) == 0

    def test_minus_one_to_zero_not_a_flip(self):
        assert detect_st_flip(-1, 0) == 0

    def test_same_direction_no_flip(self):
        assert detect_st_flip(+1, +1) == 0
        assert detect_st_flip(-1, -1) == 0
        assert detect_st_flip(0, 0) == 0


# ===========================================================================
# 3.  ``apply`` writes ``st_flip_dir`` per bar from detect_st_flip — sanity.
# ===========================================================================

class TestApplyPopulatesStFlipDirArray:

    def test_per_bar_st_flip_dir_diagnostic_matches_detect_st_flip(self):
        # Cover all six transitions in one trend tape:
        #  t=0 prev=0    curr=0    -> 0
        #  t=1 prev=0    curr=-1   -> 0  (init, NOT a flip)
        #  t=2 prev=-1   curr=+1   -> +1 (long flip)
        #  t=3 prev=+1   curr=+1   -> 0  (no change)
        #  t=4 prev=+1   curr=-1   -> -1 (short flip)
        #  t=5 prev=-1   curr=0    -> 0  (de-init)
        #  t=6 prev=0    curr=+1   -> 0  (init)
        trend = np.array([0, -1, +1, +1, -1, 0, +1], dtype=np.int64)
        n = len(trend)
        per_bar = _make_per_bar(n=n)
        positions = _positions_from_trend(trend)
        cfg = _make_filter_cfg(a_enabled=False, b_enabled=False)
        result: ZigZagSTFilterResult = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="both",
        )
        st_flip_dir = result.filter_diagnostics["st_flip_dir"]
        assert list(st_flip_dir) == [0, 0, +1, 0, -1, 0, 0]


# ===========================================================================
# 4.  Event ordering — close(t) decides, open(t+1) executes.
# ===========================================================================

class TestEventOrderingDecisionAtCloseExecutionAtNextOpen:
    """Plan §5.5 / spec §17.13:

    - The FSM transitions on the events visible at ``close(t)``.
    - The resulting position change is materialised at index ``t + 1``
      (which is "open(t+1)" under ``OPEN_TO_OPEN``).
    - Same-bar trigger + allowed flip is the canonical
      "trigger first, then flip" path: lifecycle starts at close(t),
      and ``filtered_positions[t+1]`` reflects the flipped direction.
    - Same-bar trigger + disallowed flip silently skips the flip; FSM
      remains in ``WAIT_FIRST_ST_FLIP``.
    """

    def _build(self, *, trade_mode: str, trigger_bar: int, flip_t_prev: int,
               flip_t_curr: int, n: int = 8):
        """Construct: trigger A at ``trigger_bar``, ST flip at the same bar.

        We arrange it so that ``trend[trigger_bar - 1] = flip_t_prev`` and
        ``trend[trigger_bar] = flip_t_curr``.  The two events therefore
        co-occur at close(trigger_bar).
        """
        trend = np.zeros(n, dtype=np.int64)
        # Establish ``flip_t_prev`` before trigger_bar:
        trend[: trigger_bar] = flip_t_prev if flip_t_prev != 0 else 0
        trend[trigger_bar:] = flip_t_curr if flip_t_curr != 0 else 0
        # Bar 0 should be 0 to honor "0 -> ±1 is init" — irrelevant when
        # flip_t_prev != 0 because we only check trend[trigger_bar-1] vs
        # trend[trigger_bar].
        if trigger_bar > 0:
            trend[trigger_bar - 1] = flip_t_prev
        trend[trigger_bar] = flip_t_curr

        cand_h = np.full(n, np.nan, dtype=np.float64)
        cand_h[trigger_bar] = 0.10  # >= candidate_trigger_threshold

        per_bar = _make_per_bar(n=n, candidate_height_pct=cand_h)
        positions = _positions_from_trend(trend, trade_mode=trade_mode)
        cfg = _make_filter_cfg(
            a_enabled=True, b_enabled=False, freeze_confirmed_legs=5,
        )
        return trend, positions, per_bar, cfg

    def test_same_bar_trigger_plus_allowed_flip_changes_position_at_t_plus_one_long(self):
        # trade_mode=long, allowed flip dir = +1.
        trend, positions, per_bar, cfg = self._build(
            trade_mode="long", trigger_bar=2, flip_t_prev=-1, flip_t_curr=+1,
        )
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="long",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # Trigger and flip co-occur at close(t=2): WAIT_FIRST_ST_FLIP is
        # entered at the same bar but immediately resolved into
        # ST_ACTIVE_FREEZE.
        assert states[2] == "ST_ACTIVE_FREEZE"
        # Position is still flat at t=2 (the flip executes at OPEN of t=3).
        assert result.positions[2] == 0
        # Position changes at t+1 = 3 (open of next bar) -> LONG.
        assert result.positions[3] == +1

    def test_same_bar_trigger_plus_allowed_flip_changes_position_at_t_plus_one_short(self):
        trend, positions, per_bar, cfg = self._build(
            trade_mode="short", trigger_bar=2, flip_t_prev=+1, flip_t_curr=-1,
        )
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="short",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[2] == "ST_ACTIVE_FREEZE"
        assert result.positions[2] == 0
        assert result.positions[3] == -1

    def test_same_bar_trigger_plus_disallowed_flip_state_remains_wait(self):
        # trade_mode=long, but flip is short (disallowed).  State must
        # remain ``WAIT_FIRST_ST_FLIP``; no entry occurs.
        trend, positions, per_bar, cfg = self._build(
            trade_mode="long", trigger_bar=2, flip_t_prev=+1, flip_t_curr=-1,
            n=10,
        )
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="long",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[2] == "WAIT_FIRST_ST_FLIP"
        # State remains WAIT for all subsequent bars (no further trigger).
        assert all(s == "WAIT_FIRST_ST_FLIP" for s in states[2:])
        # Disallowed flip → no entry at t+1 (position stays flat).
        assert result.positions[3] == 0
        assert all(p == 0 for p in result.positions)

    def test_no_double_shift_in_filtered_positions(self):
        """``filtered_positions[t]`` reflects bar t, NOT t-1 / t+1.

        Concretely: the lifecycle starts at t=2 and an ST flip lands at
        t=3.  The position must change at t+1=4, not t=3 and not t=5.
        """
        n = 8
        trend = np.array([0, -1, -1, +1, +1, +1, +1, +1], dtype=np.int64)
        cand_h = np.full(n, np.nan)
        cand_h[1] = 0.10  # trigger A at t=1 → WAIT
        # Long flip at t=3 (trend -1 -> +1).
        per_bar = _make_per_bar(n=n, candidate_height_pct=cand_h)
        positions = _positions_from_trend(trend)
        cfg = _make_filter_cfg(
            a_enabled=True, b_enabled=False, freeze_confirmed_legs=5,
        )
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="long",
        )
        # Position must be 0 up to and including t=3 (decision bar).
        assert all(p == 0 for p in result.positions[:4])
        # Position must be +1 from t=4 onward (open(t+1) execution).
        assert result.positions[4] == +1


# ===========================================================================
# 5.  ``0 -> ±1`` is non-tradable in BOTH WAIT_FIRST_ST_FLIP and ST_STOPPING.
# ===========================================================================

class TestZeroToPmOneIsNonTradable:
    """Spec §17.14 — ``0 -> ±1`` is initialization, not a flip.

    Two FSM-level consequences are pinned here:
      - In ``WAIT_FIRST_ST_FLIP``, ``0 -> +1`` (or ``0 -> -1``) does
        not transition into ``ST_ACTIVE_FREEZE``.  The FSM keeps
        waiting for a real ``+1 ↔ -1`` flip.
      - In ``ST_STOPPING`` (with an open position), ``0 -> ±1`` does
        not close the position — only an opposite ``+1 ↔ -1`` flip
        can.
    """

    def test_zero_to_plus_one_does_not_open_in_wait(self):
        # Trigger A at t=1 → WAIT. Trend then goes 0 -> +1 at t=3.  Per
        # spec this is initialization, not a flip; FSM must stay in WAIT.
        n = 6
        trend = np.array([0, 0, 0, +1, +1, +1], dtype=np.int64)
        cand_h = np.full(n, np.nan)
        cand_h[1] = 0.10
        per_bar = _make_per_bar(n=n, candidate_height_pct=cand_h)
        positions = _positions_from_trend(trend)
        cfg = _make_filter_cfg(a_enabled=True, b_enabled=False)
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="long",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[1] == "WAIT_FIRST_ST_FLIP"
        # 0 -> +1 at t=3 must NOT open a position.
        assert states[3] == "WAIT_FIRST_ST_FLIP"
        assert all(p == 0 for p in result.positions)

    def test_zero_to_minus_one_does_not_open_in_wait(self):
        n = 6
        trend = np.array([0, 0, 0, -1, -1, -1], dtype=np.int64)
        cand_h = np.full(n, np.nan)
        cand_h[1] = 0.10
        per_bar = _make_per_bar(n=n, candidate_height_pct=cand_h)
        positions = _positions_from_trend(trend)
        cfg = _make_filter_cfg(a_enabled=True, b_enabled=False)
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="short",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[3] == "WAIT_FIRST_ST_FLIP"
        assert all(p == 0 for p in result.positions)

    def _stopping_with_long_position(self, *, post_stopping_trend: list[int]):
        """Construct an FSM trajectory:
            OFF -> WAIT (t=1) -> FREEZE (t=2) -> MONITORING (t=3)
            -> STOPPING (t=5) holding LONG.

        Then splice a custom trend tail starting at t=6.
        """
        head = [0, -1, +1, +1, +1, +1]
        n = len(head) + len(post_stopping_trend)
        trend = np.array(head + post_stopping_trend, dtype=np.int64)

        cand_h = np.full(n, np.nan, dtype=np.float64)
        cand_h[1] = 0.10  # trigger A at t=1 (during OFF) → WAIT

        confirm_event = np.zeros(n, dtype=np.int8)
        confirm_event[3] = 1  # FREEZE -> MONITORING (freeze_confirmed_legs=1)
        confirm_event[5] = 1  # MONITORING -> STOPPING (low local median)

        local_median_N = np.full(n, np.nan, dtype=np.float64)
        local_median_avail = np.zeros(n, dtype=bool)
        local_median_N[3] = 0.10
        local_median_avail[3] = True
        local_median_N[5] = 0.001  # below global_median 0.05 → STOPPING
        local_median_avail[5] = True

        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=cand_h,
            confirm_event=confirm_event,
            local_median_N=local_median_N,
            local_median_available=local_median_avail,
        )
        cfg = _make_filter_cfg(
            a_enabled=True, b_enabled=False, freeze_confirmed_legs=1,
        )
        positions = _positions_from_trend(trend)
        return n, trend, per_bar, cfg, positions

    def test_zero_to_plus_one_does_not_close_long_in_stopping(self):
        # After STOPPING at t=5 holding LONG, splice trend +1 -> 0 -> +1
        # over t=6,7,8.  Both ``+1 -> 0`` and ``0 -> +1`` are flip_dir = 0
        # (non-tradable).  Position must remain LONG, state STOPPING.
        n, trend, per_bar, cfg, positions = self._stopping_with_long_position(
            post_stopping_trend=[0, +1, +1, +1],
        )
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"
        # All bars from t=5 to end must remain in STOPPING (no opposite
        # flip), holding LONG.
        for t in range(5, n):
            assert states[t] == "ST_STOPPING", (
                f"State at t={t} expected STOPPING, got {states[t]}"
            )
        # Held position is LONG throughout.
        for t in range(5, n):
            assert result.positions[t] == +1, (
                f"positions[{t}] expected +1, got {result.positions[t]}"
            )

    def test_zero_to_minus_one_does_not_close_long_in_stopping(self):
        # ``+1 -> 0`` then ``0 -> -1`` — neither is a tradable flip.
        # Position stays LONG, state stays STOPPING.
        n, trend, per_bar, cfg, positions = self._stopping_with_long_position(
            post_stopping_trend=[0, -1, -1, -1],
        )
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"
        for t in range(5, n):
            assert states[t] == "ST_STOPPING"
            assert result.positions[t] == +1


# ===========================================================================
# 6.  Donor ``extract_trades`` indexing pin under OPEN_TO_OPEN.
#
#     READ-ONLY: this test does NOT modify ``extract_trades``; it pins
#     the existing donor behaviour so that any drift in donor's
#     entry_index / exit_index semantics (or in the close-decision rule
#     ``signal_idx = max(execution_idx - 1, 0)``) is caught before WP7
#     wires ``attach_trade_filter_diagnostics``.
#
#     Plan §8.4 / §8.4.1 — Acceptance gate (entry + exit, both pinned
#     in one place).
# ===========================================================================

class TestExtractTradesIndexingPinOpenToOpen:

    @staticmethod
    def _inputs(positions_list, prices_list=None, commission_rate=0.0):
        n = len(positions_list) - 1
        positions = np.array(positions_list, dtype=np.int64)
        if prices_list is None:
            prices_list = [100.0 + i for i in range(len(positions_list))]
        execution_prices = np.array(prices_list, dtype=np.float64)
        returns = np.zeros(n, dtype=np.float64)
        index = pd.RangeIndex(len(positions_list))
        trend = np.zeros(len(positions_list), dtype=np.int8)
        return positions, returns, execution_prices, index, commission_rate, trend

    def test_entry_index_is_execution_bar(self):
        # positions = [0, 0, 0, +1, +1, +1, 0, 0]
        # Trade opens at the open of bar 3 (positions[2]=0 -> positions[3]=+1).
        # Donor must record entry_index = 3 (execution bar, OPEN_TO_OPEN).
        positions = [0, 0, 0, +1, +1, +1, 0, 0]
        df = extract_trades(*self._inputs(positions))
        assert len(df) == 1
        assert int(df.iloc[0]["entry_index"]) == 3

    def test_exit_index_is_execution_bar(self):
        positions = [0, 0, 0, +1, +1, +1, 0, 0]
        df = extract_trades(*self._inputs(positions))
        # Trade closes at the open of bar 6 (positions[5]=+1 -> positions[6]=0).
        assert int(df.iloc[0]["exit_index"]) == 6

    def test_entry_signal_idx_is_close_decision_bar(self):
        # PIN: entry_signal_idx = max(entry_index - 1, 0) = 2.
        positions = [0, 0, 0, +1, +1, +1, 0, 0]
        df = extract_trades(*self._inputs(positions))
        entry_index = int(df.iloc[0]["entry_index"])
        entry_signal_idx = max(entry_index - 1, 0)
        assert entry_signal_idx == 2
        assert entry_signal_idx == entry_index - 1  # because entry_index > 0

    def test_exit_signal_idx_is_close_decision_bar(self):
        # PIN: exit_signal_idx = max(exit_index - 1, 0) = 5.
        positions = [0, 0, 0, +1, +1, +1, 0, 0]
        df = extract_trades(*self._inputs(positions))
        exit_index = int(df.iloc[0]["exit_index"])
        exit_signal_idx = max(exit_index - 1, 0)
        assert exit_signal_idx == 5
        assert exit_signal_idx == exit_index - 1

    def test_entry_index_zero_edge_case(self):
        # positions[0] = +1 (already in trade at bar 0).  Donor opens a
        # trade at entry_idx = 0 because prev_pos defaults to 0 for i==0.
        # entry_signal_idx = max(0 - 1, 0) = 0 — invariant defence.
        positions = [+1, +1, +1, 0]
        df = extract_trades(*self._inputs(positions))
        assert len(df) == 1
        entry_index = int(df.iloc[0]["entry_index"])
        assert entry_index == 0
        # PIN: max(0 - 1, 0) == 0 (saturating to 0 — never negative).
        assert max(entry_index - 1, 0) == 0

    def test_pending_open_trade_at_end_no_exit_signal_lookup(self):
        # positions = [0, +1, +1] — trade still open at the last index;
        # exit_index pins to n (= len(positions) - 1).  Per §8.4 this is
        # the ``pending_open_trade_at_end`` edge: exit_signal lookup is
        # skipped at the WP7 callsite, so we only pin the index here.
        positions = [0, +1, +1]
        df = extract_trades(*self._inputs(positions))
        assert len(df) == 1
        entry_index = int(df.iloc[0]["entry_index"])
        exit_index = int(df.iloc[0]["exit_index"])
        n = len(positions) - 1
        assert entry_index == 1
        # Pending: exit_index sits at the last position slot (n).
        assert exit_index == n
        # bars_held > 0 distinguishes a normal trade from a same-bar
        # open-and-close pending trade at the very end.
        assert int(df.iloc[0]["bars_held"]) == exit_index - entry_index

    def test_entry_and_exit_indexing_pinned_in_same_scenario(self):
        """Acceptance gate of §8.4.1 — entry AND exit pinned together.

        The signal-vs-execution distinction is verified for both sides
        in a single scenario so the OPEN_TO_OPEN convention cannot
        regress on one side without the other test catching it.
        """
        # SHORT trade: open at exec bar 2, close at exec bar 5.
        positions = [0, 0, -1, -1, -1, 0, 0]
        df = extract_trades(*self._inputs(positions))
        assert len(df) == 1
        row = df.iloc[0]
        entry_index = int(row["entry_index"])
        exit_index = int(row["exit_index"])
        assert entry_index == 2
        assert exit_index == 5
        # Both signal indices match the close-decision rule.
        assert max(entry_index - 1, 0) == 1
        assert max(exit_index - 1, 0) == 4
        assert int(row["bars_held"]) == exit_index - entry_index

    def test_reversal_keeps_entry_exit_signal_distance_per_trade(self):
        """Reversal at exec bar 3: closing trade exits at 3, opening
        trade enters at 3.  Each trade independently honours
        ``signal_idx = max(execution_idx - 1, 0)``.
        """
        # positions = [0, +1, +1, -1, -1, 0]
        positions = [0, +1, +1, -1, -1, 0]
        df = extract_trades(*self._inputs(positions))
        assert len(df) == 2
        long_trade = df.iloc[0]
        short_trade = df.iloc[1]
        # LONG: entry at 1, exit at 3.
        assert int(long_trade["entry_index"]) == 1
        assert int(long_trade["exit_index"]) == 3
        # SHORT (reversal): entry at 3, exit at 5.
        assert int(short_trade["entry_index"]) == 3
        assert int(short_trade["exit_index"]) == 5
        # Pin the close-decision rule per trade.
        for t in (long_trade, short_trade):
            assert max(int(t["entry_index"]) - 1, 0) == int(t["entry_index"]) - 1
            assert max(int(t["exit_index"]) - 1, 0) == int(t["exit_index"]) - 1


# ===========================================================================
# 7.  Anti-drift — WP6 must not creep into runtime / extract_trades.
# ===========================================================================

class TestWp6AntiDrift:
    """WP6 only adds ``detect_st_flip`` to the public API and pins
    behaviour through tests.  No new exports beyond that, no
    ``RawBacktestArtifacts``, and no orchestrator hooks.
    """

    def test_module_does_not_export_runtime_artifacts_yet(self):
        # RawBacktestArtifacts lives in core.backtest, not here — still forbidden.
        # attach_trade_filter_diagnostics was promoted in WP7 and is now allowed.
        forbidden = {
            "RawBacktestArtifacts",
            "compute_filter_diagnostics",
        }
        public = set(zigzag_st_filter.__all__)
        assert forbidden.isdisjoint(public), (
            f"Forbidden exports leaked into __all__: "
            f"{sorted(forbidden & public)}"
        )

    def test_extract_trades_signature_unchanged(self):
        """``extract_trades`` parameters are byte-identical to donor
        baseline — WP6 must not add filter-related kwargs.
        """
        import inspect
        sig = inspect.signature(extract_trades)
        params = list(sig.parameters.keys())
        assert params == [
            "positions",
            "returns",
            "execution_prices",
            "index",
            "commission_rate",
            "trend",
            "execution_model",
        ], f"extract_trades signature drifted: {params}"

    def test_apply_does_not_emit_filter_keys_reserved_for_wp7(self):
        """The diagnostics emitted by ``apply`` must not include integration-layer
        keys (``trade_filter_attached``, ``trade_filter_diagnostics_attached``).
        Note: ``median_stop_triggered`` is now a valid §13 key (WP9).
        """
        n = 4
        trend = np.zeros(n, dtype=np.int64)
        per_bar = _make_per_bar(n=n)
        cfg = _make_filter_cfg(a_enabled=False, b_enabled=False)
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(),
            trade_filter_config=cfg, trade_mode="both",
        )
        emitted = set(result.filter_diagnostics.keys())
        forbidden = {
            "trade_filter_diagnostics_attached",
            "trade_filter_attached",
        }
        assert forbidden.isdisjoint(emitted), (
            f"Forbidden integration-layer keys leaked into filter_diagnostics: "
            f"{sorted(forbidden & emitted)}"
        )
