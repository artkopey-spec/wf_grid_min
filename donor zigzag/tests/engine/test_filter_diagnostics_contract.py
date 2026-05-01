"""
Phase 4 — diagnostics extension (TP-18 `filter_diagnostics_contract`).

RFC v3.1 §7.1, §11.5:
  Snapshot keyset of `filter_diagnostics` for mode=zigzag must be
  *superset* of the legacy keyset (no key removed / renamed) AND must
  include the Phase 4 additions:

    zz_ready_a, zz_ready_b, zz_readiness_on,
    zz_arm_source, zz_cand_leg_id,
    zz_readiness_block_reason, zz_disarm_event,
    structural_reset_event

The test is a shape / dtype / length contract.  It does NOT replicate
the semantic tests in tests/core/test_zigzag_readiness_phase3.py — those
already cover array semantics.  Here we verify only that engine.run
actually plumbs the arrays through to the public filter_diagnostics
dict used by Excel writers, signal_events, etc.

Scope bounds (Phase 4):
  - no Excel / signal_events changes
  - no new optimizer/walk-forward integration
  - no new xfails
"""
from __future__ import annotations

import numpy as np
import pytest

from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel
from tests.fixtures.data_generator import make_daily_ohlc


LEGACY_ZZ_KEYS: frozenset = frozenset({
    "zz_leg_direction", "zz_cand_height_pct", "zz_last_pivot_price",
    "zz_last_pivot_bar_idx", "zz_global_median", "zz_global_p80",
    "zz_local_median", "zz_n_legs_before", "zz_regime_state",
    "zz_n_legs_since_regime_open", "zz_armed", "zz_armed_side",
    "zz_n_bars_since_extreme", "zz_n_bars_since_arm",
    "zz_legs", "zz_index", "execution_model",
})

NEW_PHASE4_KEYS: frozenset = frozenset({
    "zz_ready_a",
    "zz_ready_b",
    "zz_readiness_on",
    "zz_arm_source",
    "zz_cand_leg_id",
    "zz_readiness_block_reason",
    "zz_disarm_event",
    "structural_reset_event",
})

EXPECTED_DTYPES: dict = {
    "zz_ready_a": np.bool_,
    "zz_ready_b": np.bool_,
    "zz_readiness_on": np.bool_,
    "zz_arm_source": np.int8,
    "zz_cand_leg_id": np.int64,
    "zz_disarm_event": np.int8,
    "structural_reset_event": np.bool_,
    # zz_readiness_block_reason is object dtype (strings) — checked separately.
}


def _zz_cfg() -> dict:
    return {
        "mode": "zigzag",
        "zigzag": {
            "reversal_threshold": 0.005,
            "min_legs_global": 5,
            "q_strong": 0.80,
            "k_local": 5,
            "entry_side": "counter_trend",
            "arm_timeout_bars_since_extreme": 24,
            "arm_timeout_bars_hard": 78,
        },
        "volatility": {"min_atr_pct": None, "max_atr_pct": None},
        "amplitude": {"n": 20, "min_separation": None, "lookback": 500,
                      "q": 0.60, "atr_period": 14, "atr_floor": 0.0},
        "volume": {"volume_column": "Volume", "volume_ma_column": "Volume MA",
                   "min_ratio": None, "max_ratio": None},
    }


def _run(df, filters_cfg):
    return run_single_backtest(
        open_prices=df["open"].values,
        high=df["high"].values,
        low=df["low"].values,
        close=df["close"].values,
        index=df.index,
        atr_period=14,
        multiplier=3.0,
        trade_mode="revers",
        commission=0.0,
        warmup_period=0,
        early_exit_enabled=False,
        periods_per_year=252.0,
        min_trades_required=0,
        extract_trades_flag=True,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        filters_cfg=filters_cfg,
        volume_ma=None,
        global_volume_ma_mean=None,
    )


class TestFilterDiagnosticsContractZigZag:
    """TP-18: snapshot keyset ⊇ legacy ∪ new."""

    def test_legacy_keys_still_present(self):
        # G-03 protection: no legacy key removed or renamed.
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        missing = LEGACY_ZZ_KEYS - set(fd.keys())
        assert not missing, f"legacy diagnostic keys missing: {sorted(missing)}"

    def test_phase4_new_keys_present(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        missing = NEW_PHASE4_KEYS - set(fd.keys())
        assert not missing, f"Phase 4 diagnostic keys missing: {sorted(missing)}"

    def test_new_keys_are_numpy_arrays_of_correct_length(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        n = res.positions.shape[0]
        for key in NEW_PHASE4_KEYS:
            arr = fd[key]
            assert isinstance(arr, np.ndarray), (
                f"{key}: expected np.ndarray, got {type(arr).__name__}"
            )
            assert arr.shape == (n,), (
                f"{key}: expected shape ({n},), got {arr.shape}"
            )

    def test_new_keys_have_expected_dtypes(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        for key, expected in EXPECTED_DTYPES.items():
            assert fd[key].dtype == expected, (
                f"{key}: expected dtype {expected}, got {fd[key].dtype}"
            )
        # readiness_block_reason is object dtype (strings).
        assert fd["zz_readiness_block_reason"].dtype == object

    def test_readiness_on_matches_ready_a_when_b_disabled(self):
        # Under default A=on/B=off, readiness_on == ready_a pointwise.
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        assert np.array_equal(fd["zz_readiness_on"], fd["zz_ready_a"])
        assert not fd["zz_ready_b"].any()

    def test_cand_leg_id_monotonic_on_known_bars(self):
        # §5.2 invariant mirrored into public diagnostics.
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        ids = fd["zz_cand_leg_id"]
        known = ids[ids != -1]
        if known.size > 1:
            assert (np.diff(known) >= 0).all(), (
                "zz_cand_leg_id must be monotonic non-decreasing on known bars"
            )


class TestFilterDiagnosticsContractModeNone:
    """Mode=none must NOT carry zz_* Phase 4 keys (no cross-leak)."""

    def test_mode_none_has_no_phase4_keys(self):
        df = make_daily_ohlc(n_bars=200, seed=1)
        res = _run(df, filters_cfg={"mode": "none"})
        fd = res.filter_diagnostics
        for key in NEW_PHASE4_KEYS:
            assert key not in fd, (
                f"mode=none leaked zigzag-specific key: {key!r}"
            )
