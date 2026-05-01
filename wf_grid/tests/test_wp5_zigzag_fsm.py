"""
WP5 unit tests — FSM and ``filtered_positions`` builder for
``trade_filter.type=zigzag_st_mode``.

Plan reference:  WP5 (plan §5).
Spec reference:  Appendix A v1.1 §4..§9, §14, §15.2, §15.3, §15.7,
                 §17.7..§17.12.

WP5 anti-drift (enforced here):
- No backtest / orchestrator / WF integration.
- No ``RawBacktestArtifacts``.
- No mutation of ``calculate_returns`` / ``extract_trades`` / metrics.
- All heights / thresholds are fractions, not percents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ConfirmedLeg,
    ZigZagFSMState,
    ZigZagGlobalStats,
    ZigZagPerBar,
    ZigZagSTFilterResult,
    apply,
    build_zigzag_global_stats,
    compute_zigzag_per_bar,
    detect_st_flip,
)
from supertrend_optimizer.core.zigzag_st_filter import (
    _is_first_flip_allowed,
)
from supertrend_optimizer.utils.exceptions import ConfigError

from wf_grid.tests.zigzag_st_close_only_fixture import (
    SIMPLE_ZIGZAG,
    MANY_LEG_SAWTOOTH,
)


# ===========================================================================
# Light-weight ad-hoc config doubles — duck-typed, not dataclass-bound, so
# tests can construct invalid scenarios that the real WP2 schema would
# reject before reaching apply().
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
    reversal_threshold: float = 0.01,
) -> ZigZagGlobalStats:
    """Minimal, hand-crafted ZigZagGlobalStats for FSM tests."""
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
    """Hand-crafted ZigZagPerBar with NaN/zero defaults; per-array overrides."""
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


def _trend_from_signs(*signs: int) -> np.ndarray:
    return np.array(signs, dtype=np.int64)


def _positions_from_trend(trend: np.ndarray, trade_mode: str = "both") -> np.ndarray:
    """Synthesize a raw ST-style positions array.

    By the close-decision / next-open execution rule the position held at
    bar t reflects ``trend[t-1]``.  This is a light-weight stand-in — real
    callers will pass the actual ST-engine output.
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
        else:  # both / revers
            pos[t] = prev
    return pos


# ===========================================================================
# 1.  Module exports / dataclass shape.
# ===========================================================================

class TestZigZagFSMStateEnum:

    def test_five_canonical_states(self):
        names = {s.name for s in ZigZagFSMState}
        assert names == {
            "OFF",
            "WAIT_FIRST_ST_FLIP",
            "ST_ACTIVE_FREEZE",
            "ST_ACTIVE_MONITORING",
            "ST_STOPPING",
        }

    def test_state_codes_stable(self):
        assert int(ZigZagFSMState.OFF) == 0
        assert int(ZigZagFSMState.WAIT_FIRST_ST_FLIP) == 1
        assert int(ZigZagFSMState.ST_ACTIVE_FREEZE) == 2
        assert int(ZigZagFSMState.ST_ACTIVE_MONITORING) == 3
        assert int(ZigZagFSMState.ST_STOPPING) == 4


class TestZigZagSTFilterResultDataclass:

    def test_fields_present(self):
        result = ZigZagSTFilterResult(
            positions=np.zeros(3, dtype=np.int8),
            filter_diagnostics={"trade_filter_state": np.array(["OFF"] * 3)},
            internal_legs=None,
        )
        assert hasattr(result, "positions")
        assert hasattr(result, "filter_diagnostics")
        assert hasattr(result, "internal_legs")

    def test_dataclass_is_frozen(self):
        result = ZigZagSTFilterResult(
            positions=np.zeros(1, dtype=np.int8),
            filter_diagnostics={},
        )
        with pytest.raises((AttributeError, Exception)):
            result.positions = np.ones(1, dtype=np.int64)  # type: ignore[misc]


# ===========================================================================
# 2.  ST flip helpers.
# ===========================================================================

class TestComputeStFlipDir:

    def test_long_flip_minus_one_to_plus_one(self):
        assert detect_st_flip(-1, +1) == +1

    def test_short_flip_plus_one_to_minus_one(self):
        assert detect_st_flip(+1, -1) == -1

    def test_no_change_plus_one_to_plus_one(self):
        assert detect_st_flip(+1, +1) == 0

    def test_no_change_minus_one_to_minus_one(self):
        assert detect_st_flip(-1, -1) == 0

    def test_init_zero_to_plus_one_not_a_flip(self):
        """Spec §17.14 / plan §5.5 — ``0 -> ±1`` is initialization, not flip."""
        assert detect_st_flip(0, +1) == 0

    def test_init_zero_to_minus_one_not_a_flip(self):
        assert detect_st_flip(0, -1) == 0

    def test_to_zero_returns_zero(self):
        assert detect_st_flip(+1, 0) == 0
        assert detect_st_flip(-1, 0) == 0


class TestIsFirstFlipAllowed:

    def test_long_mode_only_long_flip_allowed(self):
        assert _is_first_flip_allowed(+1, "long") is True
        assert _is_first_flip_allowed(-1, "long") is False

    def test_short_mode_only_short_flip_allowed(self):
        assert _is_first_flip_allowed(-1, "short") is True
        assert _is_first_flip_allowed(+1, "short") is False

    def test_both_mode_any_flip_allowed(self):
        assert _is_first_flip_allowed(+1, "both") is True
        assert _is_first_flip_allowed(-1, "both") is True

    def test_revers_mode_any_flip_allowed(self):
        assert _is_first_flip_allowed(+1, "revers") is True
        assert _is_first_flip_allowed(-1, "revers") is True

    def test_zero_flip_dir_is_never_allowed(self):
        for mode in ("long", "short", "both", "revers"):
            assert _is_first_flip_allowed(0, mode) is False

    def test_unknown_trade_mode_raises(self):
        with pytest.raises(ConfigError, match="trade_mode"):
            _is_first_flip_allowed(+1, "weird")


# ===========================================================================
# 3.  apply() input validation.
# ===========================================================================

class TestApplyInputValidation:

    def _baseline_inputs(self, n: int = 5):
        trend = np.zeros(n, dtype=np.int64)
        per_bar = _make_per_bar(n=n)
        global_stats = _make_global_stats()
        cfg = _make_filter_cfg()
        return trend, per_bar, global_stats, cfg

    def test_rejects_none_config(self):
        trend, per_bar, gs, _cfg = self._baseline_inputs()
        with pytest.raises(ConfigError, match="trade_filter_config"):
            apply(
                trend=trend, per_bar=per_bar,
                zigzag_global_stats=gs, trade_filter_config=None, trade_mode="both",
            )

    def test_rejects_none_global_stats(self):
        trend, per_bar, _gs, cfg = self._baseline_inputs()
        with pytest.raises(ConfigError, match="global_stats"):
            apply(
                trend=trend, per_bar=per_bar,
                zigzag_global_stats=None, trade_filter_config=cfg, trade_mode="both",
            )

    def test_rejects_unknown_trade_mode(self):
        trend, per_bar, gs, cfg = self._baseline_inputs()
        with pytest.raises(ConfigError, match="trade_mode"):
            apply(
                trend=trend, per_bar=per_bar,
                zigzag_global_stats=gs, trade_filter_config=cfg, trade_mode="weird",
            )

    def test_rejects_2d_trend(self):
        """apply() requires 1-D trend array."""
        trend_2d = np.zeros((5, 2), dtype=np.int64)
        trend, per_bar, gs, cfg = self._baseline_inputs(n=5)
        with pytest.raises(ConfigError, match="1-D"):
            apply(
                trend=trend_2d, per_bar=per_bar,
                zigzag_global_stats=gs, trade_filter_config=cfg, trade_mode="both",
            )

    def test_rejects_per_bar_length_mismatch(self):
        trend, _pb, gs, cfg = self._baseline_inputs(n=5)
        bad_pb = _make_per_bar(n=4)
        with pytest.raises(ConfigError, match="length mismatch"):
            apply(
                trend=trend, per_bar=bad_pb,
                zigzag_global_stats=gs, trade_filter_config=cfg, trade_mode="both",
            )

    def test_rejects_negative_freeze_confirmed_legs(self):
        trend, per_bar, gs, _cfg = self._baseline_inputs()
        bad_cfg = _make_filter_cfg(freeze_confirmed_legs=-1)
        with pytest.raises(ConfigError, match="freeze_confirmed_legs"):
            apply(
                trend=trend, per_bar=per_bar,
                zigzag_global_stats=gs, trade_filter_config=bad_cfg, trade_mode="both",
            )

    def test_rejects_non_finite_global_median(self):
        trend, per_bar, _gs, cfg = self._baseline_inputs()
        gs = _make_global_stats(global_median=float("nan"))
        with pytest.raises(ConfigError, match="finite"):
            apply(
                trend=trend, per_bar=per_bar,
                zigzag_global_stats=gs, trade_filter_config=cfg, trade_mode="both",
            )


# ===========================================================================
# 4.  Result / output shape.
# ===========================================================================

class TestApplyResultShape:

    def test_filtered_positions_dtype_and_length_match_input(self):
        n = 10
        trend = _trend_from_signs(*([0] * n))
        per_bar = _make_per_bar(n=n)
        gs = _make_global_stats()
        cfg = _make_filter_cfg()

        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=gs, trade_filter_config=cfg, trade_mode="both",
        )

        assert isinstance(result, ZigZagSTFilterResult)
        assert result.positions.shape == (n,)
        assert result.positions.dtype == np.dtype("int8")

    def test_diagnostics_keys_and_lengths(self):
        n = 6
        trend = _trend_from_signs(*([0] * n))
        per_bar = _make_per_bar(n=n)

        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(), trade_filter_config=_make_filter_cfg(),
            trade_mode="both",
        )

        for key in (
            "trade_filter_state",
            "trade_filter_state_code",
            "trade_filter_trigger_source",
            "confirmed_legs_since_start",
            "st_flip_dir",
        ):
            assert key in result.filter_diagnostics
            assert result.filter_diagnostics[key].shape == (n,)


# ===========================================================================
# 5.  State transitions: OFF stays OFF without triggers.
# ===========================================================================

class TestOffStaysOff:

    def test_no_triggers_means_no_positions(self):
        n = 8
        trend = _trend_from_signs(0, +1, +1, -1, -1, +1, +1, -1)
        per_bar = _make_per_bar(n=n)  # all NaN, no triggers possible
        gs = _make_global_stats()
        cfg = _make_filter_cfg()

        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=gs, trade_filter_config=cfg, trade_mode="both",
        )

        assert np.all(result.positions == 0)
        assert all(s == "OFF" for s in result.filter_diagnostics["trade_filter_state"])
        assert all(
            t == "none" for t in result.filter_diagnostics["trade_filter_trigger_source"]
        )

    def test_unavailable_b_does_not_trigger(self):
        """A confirm bar with unavailable median cannot fire B (spec §15.7 OFF)."""
        n = 4
        trend = _trend_from_signs(+1, +1, -1, +1)
        per_bar = _make_per_bar(
            n=n,
            confirm_event=np.array([0, 1, 0, 0], dtype=np.int8),
            local_median_N=np.array([np.nan, np.nan, np.nan, np.nan]),
            local_median_available=np.zeros(n, dtype=bool),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(),
            trade_filter_config=_make_filter_cfg(a_enabled=False, b_enabled=True),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert all(s == "OFF" for s in states)


# ===========================================================================
# 6.  OFF -> WAIT_FIRST_ST_FLIP transitions: A / B / both.
# ===========================================================================

class TestOffToWaitTransitions:

    def test_a_trigger_alone_moves_to_wait(self):
        n = 4
        trend = _trend_from_signs(0, 0, 0, 0)
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([0.01, 0.10, 0.10, 0.10]),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(a_enabled=True, b_enabled=False),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        triggers = list(result.filter_diagnostics["trade_filter_trigger_source"])
        assert states[0] == "OFF"
        assert states[1] == "WAIT_FIRST_ST_FLIP"
        assert triggers[1] == "candidate_threshold"

    def test_b_trigger_alone_moves_to_wait(self):
        n = 4
        per_bar = _make_per_bar(
            n=n,
            confirm_event=np.array([0, 1, 0, 0], dtype=np.int8),
            local_median_N=np.array([np.nan, 0.10, np.nan, np.nan]),
            local_median_available=np.array([False, True, False, False]),
        )
        result = apply(
            trend=_trend_from_signs(0, 0, 0, 0),
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(global_median=0.05),
            trade_filter_config=_make_filter_cfg(a_enabled=False, b_enabled=True),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        triggers = list(result.filter_diagnostics["trade_filter_trigger_source"])
        assert states[1] == "WAIT_FIRST_ST_FLIP"
        assert triggers[1] == "confirmed_median"

    def test_both_triggers_simultaneously_label_both(self):
        n = 3
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.10, np.nan]),
            confirm_event=np.array([0, 1, 0], dtype=np.int8),
            local_median_N=np.array([np.nan, 0.10, np.nan]),
            local_median_available=np.array([False, True, False]),
        )
        result = apply(
            trend=_trend_from_signs(0, 0, 0),
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(global_median=0.05),
            trade_filter_config=_make_filter_cfg(a_enabled=True, b_enabled=True),
            trade_mode="both",
        )
        triggers = list(result.filter_diagnostics["trade_filter_trigger_source"])
        assert triggers[1] == "both"

    def test_disabled_a_circuit_does_not_fire(self):
        n = 3
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.99, np.nan]),
        )
        result = apply(
            trend=_trend_from_signs(0, 0, 0),
            per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(candidate_trigger_threshold=0.05),
            trade_filter_config=_make_filter_cfg(a_enabled=False, b_enabled=True),
            trade_mode="both",
        )
        triggers = list(result.filter_diagnostics["trade_filter_trigger_source"])
        assert all(t == "none" for t in triggers)

    def test_disabled_b_circuit_does_not_fire(self):
        n = 3
        per_bar = _make_per_bar(
            n=n,
            confirm_event=np.array([0, 1, 0], dtype=np.int8),
            local_median_N=np.array([np.nan, 0.99, np.nan]),
            local_median_available=np.array([False, True, False]),
        )
        result = apply(
            trend=_trend_from_signs(0, 0, 0),
            per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(global_median=0.05),
            trade_filter_config=_make_filter_cfg(a_enabled=True, b_enabled=False),
            trade_mode="both",
        )
        triggers = list(result.filter_diagnostics["trade_filter_trigger_source"])
        assert all(t == "none" for t in triggers)

    def test_b_requires_confirm_event(self):
        """Spec §6 step 1-2 — without confirm-bar, B circuit makes no decision."""
        n = 3
        per_bar = _make_per_bar(
            n=n,
            confirm_event=np.zeros(n, dtype=np.int8),
            local_median_N=np.array([np.nan, 0.10, np.nan]),
            local_median_available=np.array([False, True, False]),
        )
        result = apply(
            trend=_trend_from_signs(0, 0, 0),
            per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(global_median=0.05),
            trade_filter_config=_make_filter_cfg(a_enabled=False, b_enabled=True),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert all(s == "OFF" for s in states)


# ===========================================================================
# 7.  WAIT -> FREEZE: allowed flip; same-bar trigger+flip; disallowed flip.
# ===========================================================================

class TestWaitToFreezeTransitions:

    def test_allowed_flip_after_wait_starts_lifecycle(self):
        """A trigger at t=1, long flip at t=2 → FREEZE at t=2."""
        n = 5
        trend = _trend_from_signs(0, -1, +1, +1, -1)  # long flip at t=2
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.10, np.nan, np.nan, np.nan]),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=99,
            ),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[0] == "OFF"
        assert states[1] == "WAIT_FIRST_ST_FLIP"
        assert states[2] == "ST_ACTIVE_FREEZE"
        # Position opens at t+1 = 3, follows positions[3] passthrough.
        # positions are derived from trend with one-bar lag, so
        # positions[3] = trend[2] = +1.
        assert result.positions[3] == +1

    def test_same_bar_trigger_plus_allowed_flip_starts_lifecycle(self):
        """Spec §15.4: same-bar trigger → flip → entry on t+1."""
        n = 5
        # Trigger and long flip occur on the same bar t=2.
        trend = _trend_from_signs(0, -1, +1, +1, -1)
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, np.nan, 0.10, np.nan, np.nan]),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=99,
            ),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # At t=2: OFF→WAIT (trigger) →FREEZE (allowed flip).
        assert states[2] == "ST_ACTIVE_FREEZE"
        # No entry on close(t=2); entry at open(t=3).
        assert result.positions[2] == 0
        assert result.positions[3] == +1

    def test_disallowed_flip_in_wait_skipped_state_remains(self):
        """trade_mode=long, short flip is silently skipped in WAIT."""
        n = 6
        # Trigger at t=1; short flip at t=2 (disallowed); long flip at t=4.
        trend = _trend_from_signs(0, +1, -1, -1, +1, +1)
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.10, np.nan, np.nan, np.nan, np.nan]
            ),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=99,
            ),
            trade_mode="long",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[1] == "WAIT_FIRST_ST_FLIP"
        # Disallowed short flip at t=2 — state stays WAIT.
        assert states[2] == "WAIT_FIRST_ST_FLIP"
        # Allowed long flip at t=4 — FREEZE starts.
        assert states[4] == "ST_ACTIVE_FREEZE"
        # No position opened at t=3 (we ignored short flip).
        assert result.positions[3] == 0

    def test_zero_to_plus_one_in_wait_does_not_open_position(self):
        """Spec §17.14 — ``0 -> ±1`` is not a tradable flip."""
        n = 5
        trend = _trend_from_signs(0, 0, +1, +1, -1)  # 0->+1 at t=2 is init
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.10, np.nan, np.nan, np.nan]),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=99,
            ),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # 0 -> +1 at t=2 is init, not flip.  WAIT persists.
        assert states[2] == "WAIT_FIRST_ST_FLIP"
        # +1 -> -1 at t=4 is the first tradable flip.
        assert states[4] == "ST_ACTIVE_FREEZE"
        assert result.positions[3] == 0  # no entry on init transition

    def test_zero_to_minus_one_does_not_open(self):
        n = 4
        trend = _trend_from_signs(0, 0, -1, -1)
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.10, np.nan, np.nan]),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=99,
            ),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[2] == "WAIT_FIRST_ST_FLIP"
        # No legitimate flip occurs in the run; WAIT persists.
        assert states[3] == "WAIT_FIRST_ST_FLIP"
        assert np.all(result.positions == 0)

    def test_long_mode_short_flip_skipped_then_long_flip_accepted(self):
        """Spec §17.11 — ``long`` mode skips short flip; later long flip opens."""
        n = 6
        # Trigger at t=1.  Short flip at t=2 (disallowed).  Long flip at t=4.
        trend = _trend_from_signs(0, +1, -1, -1, +1, +1)
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.10, np.nan, np.nan, np.nan, np.nan]
            ),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=99,
            ),
            trade_mode="long",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[2] == "WAIT_FIRST_ST_FLIP"
        assert states[4] == "ST_ACTIVE_FREEZE"
        assert result.positions[5] == +1

    def test_short_mode_long_flip_skipped_then_short_flip_accepted(self):
        """Spec §17.12 — symmetric to the long-mode case."""
        n = 6
        trend = _trend_from_signs(0, -1, +1, +1, -1, -1)
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.10, np.nan, np.nan, np.nan, np.nan]
            ),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=99,
            ),
            trade_mode="short",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[2] == "WAIT_FIRST_ST_FLIP"
        assert states[4] == "ST_ACTIVE_FREEZE"
        assert result.positions[5] == -1


# ===========================================================================
# 8.  FREEZE -> MONITORING: counter independence and timing.
# ===========================================================================

class TestFreezeToMonitoring:

    def test_freeze_zero_immediately_monitors(self):
        n = 4
        trend = _trend_from_signs(0, -1, +1, +1)
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.10, np.nan, np.nan]),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=0,
            ),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # Lifecycle starts at t=2 (allowed flip); freeze=0 → MONITORING immediately.
        assert states[2] == "ST_ACTIVE_MONITORING"

    def test_freeze_counter_increments_only_in_active_states(self):
        """Confirm event before lifecycle start does NOT increment counter."""
        n = 8
        trend = _trend_from_signs(0, -1, +1, +1, -1, +1, +1, -1)
        # Confirm event on the same bar lifecycle starts (t=2): excluded.
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.10, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]
            ),
            confirm_event=np.array([0, 0, 1, 1, 1, 0, 0, 0], dtype=np.int8),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=2,
            ),
            trade_mode="both",
        )
        counter = result.filter_diagnostics["confirmed_legs_since_start"]
        states = list(result.filter_diagnostics["trade_filter_state"])
        # Lifecycle starts at t=2; counter resets to 0.
        assert counter[2] == 0  # confirm at t=2 NOT counted
        assert counter[3] == 1  # confirm at t=3 counted
        # At t=4 second confirm fires, counter -> 2 -> reaches freeze threshold.
        assert counter[4] == 2
        assert states[4] == "ST_ACTIVE_MONITORING"

    def test_freeze_counter_independent_of_local_window(self):
        """Plan §3.3 step 6 — freeze counter and local_window are independent.

        We pick freeze_confirmed_legs=3 and use a per_bar where local_window
        would have produced different median availability/values; the counter
        still reaches its target after exactly 3 confirms after lifecycle start.
        """
        n = 12
        trend = np.array([0, -1, +1, +1, +1, +1, +1, +1, +1, +1, +1, +1], dtype=np.int64)
        confirm_event = np.zeros(n, dtype=np.int8)
        confirm_event[3] = 1
        confirm_event[5] = 1
        confirm_event[7] = 1
        # The next confirm at t=9 should NOT extend freeze; we already left it.
        confirm_event[9] = 1
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.10] + [np.nan] * (n - 2)
            ),
            confirm_event=confirm_event,
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=3,
            ),
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # Lifecycle starts at t=2.  Confirms at t=3, 5, 7 increment counter.
        assert states[2] == "ST_ACTIVE_FREEZE"
        assert states[3] == "ST_ACTIVE_FREEZE"  # counter=1
        assert states[5] == "ST_ACTIVE_FREEZE"  # counter=2
        assert states[7] == "ST_ACTIVE_MONITORING"  # counter=3 → MONITORING


# ===========================================================================
# 9.  Repeated triggers do not reset lifecycle.
# ===========================================================================

class TestRepeatedTriggersDoNotReset:

    def test_repeated_a_trigger_in_wait_does_not_reset(self):
        n = 6
        trend = _trend_from_signs(0, 0, 0, 0, -1, +1)
        # Triggers at t=1, t=2, t=3 — only the first counts.
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.10, 0.10, 0.10, np.nan, np.nan]),
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=_make_global_stats(
                candidate_trigger_threshold=0.05
            ),
            trade_filter_config=_make_filter_cfg(
                a_enabled=True, b_enabled=False, freeze_confirmed_legs=99,
            ),
            trade_mode="both",
        )
        triggers = list(result.filter_diagnostics["trade_filter_trigger_source"])
        # Only the first WAIT-entry trigger is recorded as candidate_threshold.
        assert triggers[1] == "candidate_threshold"
        assert triggers[2] == "none"
        assert triggers[3] == "none"

    def test_repeated_b_trigger_in_freeze_does_not_reset_counter(self):
        n = 8
        trend = _trend_from_signs(0, -1, +1, +1, +1, +1, +1, +1)
        confirm_event = np.array([0, 0, 1, 1, 1, 1, 0, 0], dtype=np.int8)
        # Synthetic: high local_median means B would re-trigger if FSM allowed.
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.10] + [np.nan] * (n - 2)),
            confirm_event=confirm_event,
            local_median_N=np.array([np.nan, np.nan, 0.20, 0.20, 0.20, 0.20, np.nan, np.nan]),
            local_median_available=np.array(
                [False, False, True, True, True, True, False, False]
            ),
        )
        cfg = _make_filter_cfg(a_enabled=True, b_enabled=True, freeze_confirmed_legs=2)
        gs = _make_global_stats(global_median=0.05, candidate_trigger_threshold=0.05)
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=gs, trade_filter_config=cfg,
            trade_mode="both",
        )
        counter = result.filter_diagnostics["confirmed_legs_since_start"]
        states = list(result.filter_diagnostics["trade_filter_state"])
        # Lifecycle starts at t=2 (allowed -1->+1 flip after trigger at t=1).
        assert states[2] == "ST_ACTIVE_FREEZE"
        # confirm at t=2 NOT counted (state_at_bar_start=WAIT).
        assert counter[2] == 0
        assert counter[3] == 1
        # confirm at t=4 increments to 2 -> MONITORING.
        assert counter[4] == 2
        assert states[4] == "ST_ACTIVE_MONITORING"
        # Even though the B trigger condition is true repeatedly, no new
        # lifecycle is started, freeze counter is not reset, trade_filter_trigger_source
        # in active states stays "none".
        active_triggers = [
            result.filter_diagnostics["trade_filter_trigger_source"][t] for t in range(2, 6)
        ]
        assert all(s == "none" for s in active_triggers)


# ===========================================================================
# 10.  MONITORING -> STOPPING transitions (median <, unavailable, NaN, Inf).
# ===========================================================================

class TestMonitoringToStopping:

    def _setup_in_monitoring(
        self,
        *,
        n: int = 12,
        median_at_bars: Dict[int, Optional[float]] = None,
        confirm_at_bars: Optional[List[int]] = None,
    ):
        """Walk through OFF→WAIT→FREEZE→MONITORING to bar (n-3); allow caller
        to plant a custom median value at bar (n-2) for stop-check evaluation.
        """
        median_at_bars = median_at_bars or {}
        confirm_at_bars = confirm_at_bars or []

        trend = np.full(n, +1, dtype=np.int64)
        trend[0] = 0
        trend[1] = -1  # forces -1->+1 long flip at t=2
        # Trigger at t=1 (A circuit), allowed flip at t=2.
        cand_h = np.full(n, np.nan, dtype=np.float64)
        cand_h[1] = 0.10
        confirm_event = np.zeros(n, dtype=np.int8)
        # Plant confirms to drive freeze→monitoring quickly with freeze=1.
        confirm_event[3] = 1  # freeze counter -> 1, MONITORING
        for t in confirm_at_bars:
            confirm_event[t] = 1

        local_median_N = np.full(n, np.nan, dtype=np.float64)
        local_median_avail = np.zeros(n, dtype=bool)
        for t, val in median_at_bars.items():
            if val is None:
                local_median_avail[t] = False
                local_median_N[t] = np.nan
            else:
                local_median_avail[t] = True
                local_median_N[t] = val

        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=cand_h,
            confirm_event=confirm_event,
            local_median_N=local_median_N,
            local_median_available=local_median_avail,
        )
        gs = _make_global_stats(global_median=0.05, candidate_trigger_threshold=0.05)
        cfg = _make_filter_cfg(
            a_enabled=True, b_enabled=False, freeze_confirmed_legs=1,
        )
        return trend, per_bar, gs, cfg

    def test_stays_in_monitoring_when_median_above_global(self):
        n = 8
        trend, per_bar, gs, cfg = self._setup_in_monitoring(
            n=n,
            confirm_at_bars=[5],
            median_at_bars={5: 0.10},  # > global_median=0.05
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=gs, trade_filter_config=cfg,
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_ACTIVE_MONITORING"
        assert states[7] == "ST_ACTIVE_MONITORING"

    def test_transitions_to_stopping_on_low_median(self):
        n = 8
        trend, per_bar, gs, cfg = self._setup_in_monitoring(
            n=n,
            confirm_at_bars=[5],
            median_at_bars={5: 0.01},  # < global_median=0.05
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=gs, trade_filter_config=cfg,
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"

    def test_transitions_to_stopping_on_unavailable_median(self):
        """Fail-closed when local_median_available=False (§15.7)."""
        n = 8
        trend, per_bar, gs, cfg = self._setup_in_monitoring(
            n=n,
            confirm_at_bars=[5],
            median_at_bars={5: None},  # unavailable
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=gs, trade_filter_config=cfg,
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"

    def test_transitions_to_stopping_on_nan_median(self):
        """Fail-closed when local_median_N is NaN even if 'available'."""
        n = 8
        trend, per_bar, gs, cfg = self._setup_in_monitoring(n=n)
        # Plant: confirm at t=5, available=True, value=NaN.
        confirm = per_bar.confirm_event.copy()
        confirm[5] = 1
        median_arr = per_bar.local_median_N.copy()
        median_arr[5] = np.nan
        avail = per_bar.local_median_available.copy()
        avail[5] = True
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=per_bar.candidate_height_pct,
            confirm_event=confirm,
            local_median_N=median_arr,
            local_median_available=avail,
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=gs, trade_filter_config=cfg,
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"

    def test_transitions_to_stopping_on_inf_median(self):
        """Fail-closed when local_median_N is +Inf or -Inf."""
        n = 8
        trend, per_bar, gs, cfg = self._setup_in_monitoring(n=n)
        confirm = per_bar.confirm_event.copy()
        confirm[5] = 1
        median_arr = per_bar.local_median_N.copy()
        median_arr[5] = np.inf
        avail = per_bar.local_median_available.copy()
        avail[5] = True
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=per_bar.candidate_height_pct,
            confirm_event=confirm,
            local_median_N=median_arr,
            local_median_available=avail,
        )
        result = apply(
            trend=trend,
            per_bar=per_bar, zigzag_global_stats=gs, trade_filter_config=cfg,
            trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"

    def test_freeze_does_not_run_stop_check(self):
        """Spec §17.16 — median-stop is intentionally disabled in FREEZE."""
        n = 10
        trend = np.full(n, +1, dtype=np.int64)
        trend[0] = 0
        trend[1] = -1
        cand_h = np.full(n, np.nan)
        cand_h[1] = 0.10
        # Plant a confirm with very low median during freeze — stop-check
        # must NOT fire because we are still in FREEZE.
        confirm_event = np.zeros(n, dtype=np.int8)
        confirm_event[3] = 1
        local_median_N = np.full(n, np.nan)
        local_median_N[3] = 0.0001  # absurdly low
        local_median_avail = np.zeros(n, dtype=bool)
        local_median_avail[3] = True
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=cand_h,
            confirm_event=confirm_event,
            local_median_N=local_median_N,
            local_median_available=local_median_avail,
        )
        cfg = _make_filter_cfg(
            a_enabled=True, b_enabled=False, freeze_confirmed_legs=5,
        )
        result = apply(
            trend=trend,
            per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(
                global_median=0.05, candidate_trigger_threshold=0.05
            ),
            trade_filter_config=cfg, trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # We are in FREEZE at t=3 with low median → must stay FREEZE.
        assert states[3] == "ST_ACTIVE_FREEZE"


# ===========================================================================
# 11.  ST_STOPPING behaviors: hold, opposite-flip exit, no-position-→-OFF.
# ===========================================================================

class TestStoppingBehavior:

    def _into_stopping_with_long_position(self) -> tuple:
        """Construct a scenario that ends in ST_STOPPING while holding LONG."""
        n = 10
        trend = np.full(n, +1, dtype=np.int64)
        trend[0] = 0
        trend[1] = -1  # forces -1->+1 long flip at t=2
        cand_h = np.full(n, np.nan, dtype=np.float64)
        cand_h[1] = 0.10
        confirm_event = np.zeros(n, dtype=np.int8)
        # Two confirms: one drives freeze→monitoring (t=3), one fires stop (t=5).
        confirm_event[3] = 1
        confirm_event[5] = 1
        local_median_N = np.full(n, np.nan, dtype=np.float64)
        local_median_avail = np.zeros(n, dtype=bool)
        # t=3 is FREEZE-end: median value irrelevant for stop-check.
        local_median_N[3] = 0.10
        local_median_avail[3] = True
        # t=5 fires stop-check in MONITORING with low median → STOPPING.
        local_median_N[5] = 0.001
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
        return n, trend, per_bar, cfg

    def test_stopping_holds_long_until_opposite_flip(self):
        n, trend, per_bar, cfg = self._into_stopping_with_long_position()
        # Default trend stays +1 throughout — no opposite flip.  Position holds.
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(
                global_median=0.05, candidate_trigger_threshold=0.05
            ),
            trade_filter_config=cfg, trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"
        # No opposite flip arrives → position stays LONG until end.
        assert all(s == "ST_STOPPING" for s in states[5:])
        assert all(p == +1 for p in result.positions[5:])

    def test_stopping_closes_on_opposite_flip_then_off(self):
        n, trend, per_bar, cfg = self._into_stopping_with_long_position()
        # Inject a short flip at t=7 (after we entered STOPPING at t=5).
        trend = trend.copy()
        trend[7] = -1
        trend[8] = -1
        trend[9] = -1
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(
                global_median=0.05, candidate_trigger_threshold=0.05
            ),
            trade_filter_config=cfg, trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"
        assert states[6] == "ST_STOPPING"
        # After short flip at close(t=7) the position closes at open(t=8) → OFF.
        assert states[7] == "OFF"
        # Position closed at t+1 = 8.
        assert result.positions[7] == +1  # held during bar 7
        assert result.positions[8] == 0   # closed at open(8)
        assert all(s == "OFF" for s in states[8:])
        # No new entries while OFF.
        assert all(p == 0 for p in result.positions[8:])

    def test_stopping_with_no_position_immediately_off(self):
        """If there is no open position when STOPPING is entered → OFF.

        In WP7 the FSM is the source of truth for positions; there is no
        external passthrough.  We obtain held_pos=0 at STOPPING-entry by
        using trade_mode="long" and injecting a short flip BEFORE the
        stop-check confirm bar:

          t=0  trend= 0  — init
          t=1  trend=-1  — trigger A (0→-1 non-tradable flip)  → OFF→WAIT
          t=2  trend=+1  — long flip (-1→+1)                   → WAIT→FREEZE,  held_pos=+1
          t=3  confirm   —                                      → FREEZE→MONITORING
          t=4  trend=-1  — short flip (+1→-1), trade_mode=long → held_pos=0
          t=5  confirm+low_median                              → MONITORING→STOPPING,
                                                                 cur_pos = filtered_positions[5]
                                                                        = held_pos from t=4 = 0
                                                               → immediate OFF
        """
        n = 8
        trend = np.array([0, -1, +1, +1, -1, -1, -1, -1], dtype=np.int64)
        cand_h = np.full(n, np.nan, dtype=np.float64)
        cand_h[1] = 0.10  # trigger A at t=1 (cand_h >= threshold 0.05)
        confirm_event = np.zeros(n, dtype=np.int8)
        confirm_event[3] = 1  # FREEZE → MONITORING (freeze_confirmed_legs=1)
        confirm_event[5] = 1  # stop-check fires here with low median
        local_median_N = np.full(n, np.nan, dtype=np.float64)
        local_median_avail = np.zeros(n, dtype=bool)
        local_median_N[3] = 0.10   # irrelevant (FREEZE bar)
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
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(
                global_median=0.05, candidate_trigger_threshold=0.05
            ),
            trade_filter_config=cfg, trade_mode="long",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # Short flip at t=4 sets held_pos=0 for trade_mode="long".
        # At t=5: MONITORING→STOPPING with cur_pos=0 → immediate OFF.
        assert states[5] == "OFF"

    def test_stopping_does_not_open_new_entries(self):
        """Same opposite ST flip path must NOT lead to a reverse entry."""
        n, trend, per_bar, cfg = self._into_stopping_with_long_position()
        trend = trend.copy()
        trend[7] = -1
        trend[8] = -1
        # In revers mode, raw ST positions[8] = -1; FSM must clamp to 0.
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(
                global_median=0.05, candidate_trigger_threshold=0.05
            ),
            trade_filter_config=cfg, trade_mode="revers",
        )
        # filtered_positions[8] must be 0 even though positions[8] = -1.
        assert result.positions[8] == 0


# ===========================================================================
# 12.  local_median_N is consumed slice-locally — the FSM does NOT recompute
#       lifecycle-only history; it uses per_bar.local_median_N as-is.
# ===========================================================================

class TestLocalMedianSliceLocal:

    def test_apply_uses_per_bar_local_median_directly(self):
        """The FSM evaluates local_median_N from per_bar at each confirm bar
        without re-windowing on lifecycle-relative legs.  Plan §3.3 step 5.

        We construct a scenario where per_bar.local_median_N is *higher* than
        global_median pre-lifecycle (so a lifecycle-only re-window would be
        empty / unavailable) and verify FSM uses it on confirm bars after
        lifecycle start.
        """
        n = 10
        trend = np.array([0, -1, +1, +1, +1, +1, +1, +1, +1, +1], dtype=np.int64)
        cand_h = np.full(n, np.nan)
        cand_h[1] = 0.10
        confirm_event = np.zeros(n, dtype=np.int8)
        confirm_event[3] = 1  # FREEZE→MONITORING
        confirm_event[5] = 1  # stop-check fires here
        # local_median_N at t=5 is high (>= global) — this comes from
        # slice-local history seeded BEFORE lifecycle start.  A lifecycle-only
        # re-window would have only one leg (insufficient for a window=5),
        # therefore unavailable, and the FSM would have wrongly fail-closed.
        local_median_N = np.full(n, np.nan)
        local_median_N[3] = 0.10
        local_median_N[5] = 0.10
        local_median_avail = np.zeros(n, dtype=bool)
        local_median_avail[3] = True
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
        result = apply(
            trend=trend,
            per_bar=per_bar,
            zigzag_global_stats=_make_global_stats(
                global_median=0.05, candidate_trigger_threshold=0.05
            ),
            trade_filter_config=cfg, trade_mode="both",
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # Slice-local median (>= global) keeps us in MONITORING.
        assert states[5] == "ST_ACTIVE_MONITORING"


# ===========================================================================
# 13.  Shared close-only fixture remains consistent WP3 / WP4 / WP5.
# ===========================================================================

class TestSharedFixtureConsistency:

    @staticmethod
    def _make_global_stats_cfg(
        *,
        reversal_threshold: float,
        candidate_trigger_threshold: float,
        local_window: int,
    ) -> Any:
        """Build a duck-typed config object for ``build_zigzag_global_stats``."""
        @dataclass
        class _ZigCfg:
            reversal_threshold: float
            candidate_trigger_threshold: float
            local_window: int
            global_stats_source: str = "full_dataset"
            leg_height_mode: str = "pct"
            global_median: str = "auto"

        @dataclass
        class _Wrap:
            zigzag: Any

        return _Wrap(
            zigzag=_ZigCfg(
                reversal_threshold=reversal_threshold,
                candidate_trigger_threshold=candidate_trigger_threshold,
                local_window=local_window,
            )
        )

    def test_apply_runs_end_to_end_on_simple_zigzag(self):
        """Smoke test using real WP3 + WP4 outputs from the shared fixture.

        We synthesize a trend that produces a long flip after the first
        candidate trigger and verify that a complete OFF→…→OFF cycle runs
        without raising and that filtered_positions has correct shape and
        dtype.
        """
        gs_cfg = self._make_global_stats_cfg(
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            candidate_trigger_threshold=0.02,
            local_window=3,
        )
        global_stats = build_zigzag_global_stats(
            close=SIMPLE_ZIGZAG.close,
            trade_filter_config=gs_cfg,
        )
        per_bar = compute_zigzag_per_bar(
            close=SIMPLE_ZIGZAG.close,
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            local_window=3,
        )
        n = len(SIMPLE_ZIGZAG.close)
        trend = np.array([0, -1, -1, +1, +1, -1, -1, +1, +1], dtype=np.int64)

        cfg = _make_filter_cfg(
            a_enabled=True, b_enabled=True, freeze_confirmed_legs=1,
        )
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=global_stats, trade_filter_config=cfg,
            trade_mode="both",
        )
        assert result.positions.shape == (n,)
        assert result.positions.dtype == np.dtype("int8")
        for key in (
            "trade_filter_state",
            "trade_filter_state_code",
            "trade_filter_trigger_source",
            "confirmed_legs_since_start",
            "st_flip_dir",
        ):
            assert result.filter_diagnostics[key].shape == (n,)

    def test_apply_consistent_with_per_bar_lengths_on_many_leg_sawtooth(self):
        gs_cfg = self._make_global_stats_cfg(
            reversal_threshold=MANY_LEG_SAWTOOTH.reversal_threshold,
            candidate_trigger_threshold=0.05,
            local_window=5,
        )
        global_stats = build_zigzag_global_stats(
            close=MANY_LEG_SAWTOOTH.close,
            trade_filter_config=gs_cfg,
        )
        per_bar = compute_zigzag_per_bar(
            close=MANY_LEG_SAWTOOTH.close,
            reversal_threshold=MANY_LEG_SAWTOOTH.reversal_threshold,
            local_window=5,
        )
        n = len(MANY_LEG_SAWTOOTH.close)
        # Fabricate a simple trend that flips on every confirm event so the
        # FSM has plenty of opportunities to evolve.  Specific values are not
        # asserted — this is a length / no-throw smoke check.
        trend = np.where(np.arange(n) % 2 == 0, +1, -1).astype(np.int64)
        trend[0] = 0

        cfg = _make_filter_cfg(
            a_enabled=True, b_enabled=True, freeze_confirmed_legs=2,
        )
        result = apply(
            trend=trend, per_bar=per_bar,
            zigzag_global_stats=global_stats, trade_filter_config=cfg,
            trade_mode="both",
        )
        assert result.positions.shape == (n,)
        for key, arr in result.filter_diagnostics.items():
            assert arr.shape == (n,), f"{key} length mismatch"


# ===========================================================================
# 14.  Anti-drift gates.
# ===========================================================================

class TestAntiDriftWp5:

    def test_no_raw_backtest_artifacts_export(self):
        import supertrend_optimizer.core.zigzag_st_filter as zzmod

        public = {name for name in dir(zzmod) if not name.startswith("_")}
        assert "RawBacktestArtifacts" not in public, (
            "RawBacktestArtifacts is WP7+; must not be exported in WP5."
        )

    def test_wp5_exports_legitimately_added(self):
        import supertrend_optimizer.core.zigzag_st_filter as zzmod

        for name in ("ZigZagFSMState", "ZigZagSTFilterResult", "apply"):
            assert hasattr(zzmod, name), f"WP5 missing export: {name}"
        public_all = set(getattr(zzmod, "__all__", []))
        for name in ("ZigZagFSMState", "ZigZagSTFilterResult", "apply"):
            assert name in public_all, f"__all__ missing WP5 export: {name}"

    def test_apply_signature_carries_no_runtime_artifacts(self):
        import inspect

        sig = inspect.signature(apply)
        forbidden = {
            "returns",
            "equity_curve",
            "trades_df",
            "metrics",
            "raw_artifacts",
        }
        present = set(sig.parameters.keys())
        assert present.isdisjoint(forbidden), (
            f"apply() must not accept runtime-integration kwargs: "
            f"{present & forbidden}"
        )
