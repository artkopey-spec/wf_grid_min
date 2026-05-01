"""
Engine-level integration tests for ZigZag filter in run_single_backtest
(plan v2.0.1 §6.2 — Stage 5A subset; test_zigzag_equal_blocks_oos_boundary
is opus-reserved for Stage 5B).

Covers:
  • filter_diagnostics populated for mode=zigzag (mode, thresholds,
    counters, zz_* arrays, zz_legs tuple).
  • mode=none / mode=amplitude / mode=zigzag → no cross-leak of keys.
  • zz reasons map into blocked_by_volatility bucket (plan §3.4.1).
  • mode=zigzag_and_volume: both-collapse of reasons (plan §1.6).
  • allow_entry invariance across execution_model (O2O vs C2C).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.constants import (
    FILTER_REASON_BOTH,
    FILTER_REASON_OK,
    FILTER_REASON_VOL_BELOW_MIN,
)
from supertrend_optimizer.utils.enums import ExecutionModel
from tests.fixtures.data_generator import make_daily_ohlc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zz_cfg(**overrides) -> dict:
    zz = {
        "reversal_threshold": 0.005,
        "min_legs_global": 5,
        "q_strong": 0.80,
        "k_local": 5,
        "entry_side": "counter_trend",
        "arm_timeout_bars_since_extreme": 24,
        "arm_timeout_bars_hard": 78,
    }
    zz.update(overrides)
    return {
        "mode": "zigzag",
        "zigzag": zz,
        "volatility": {"min_atr_pct": None, "max_atr_pct": None},
        "amplitude": {"n": 20, "min_separation": None, "lookback": 500,
                      "q": 0.60, "atr_period": 14, "atr_floor": 0.0},
        "volume": {"volume_column": "Volume", "volume_ma_column": "Volume MA",
                   "min_ratio": None, "max_ratio": None},
    }


def _zz_vol_cfg(**zz_overrides) -> dict:
    cfg = _zz_cfg(**zz_overrides)
    cfg["mode"] = "zigzag_and_volume"
    cfg["volume"] = {
        "volume_column": "Volume",
        "volume_ma_column": "Volume MA",
        "min_ratio": 2.0,
        "max_ratio": None,
    }
    return cfg


def _run(df, filters_cfg=None, execution_model=ExecutionModel.OPEN_TO_OPEN,
         volume_ma=None, global_volume_ma_mean=None, **over):
    defaults = dict(
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
        execution_model=execution_model,
        filters_cfg=filters_cfg,
        volume_ma=volume_ma,
        global_volume_ma_mean=global_volume_ma_mean,
    )
    defaults.update(over)
    return run_single_backtest(**defaults)


def _make_df_with_volume(n_bars=300, seed=42, vol_ma_val=1000.0):
    df = make_daily_ohlc(n_bars=n_bars, seed=seed)
    df["volume ma"] = vol_ma_val
    return df


def _volume_ma_arr(df, vol_ma_val=1000.0):
    n = len(df)
    arr = np.full(n, vol_ma_val, dtype=np.float64)
    return arr, float(vol_ma_val)


# ===========================================================================
# 1. filter_diagnostics: zigzag mode has all zz_* keys
# ===========================================================================


class TestZigZagDiagnosticsKeys:

    def test_mode_zigzag_diagnostics_has_zz_fields(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        assert fd is not None
        assert fd["mode"] == "zigzag"
        assert "thresholds" in fd and "counters" in fd

        # All zz_* per-bar keys (§3.4.4)
        required = [
            "zz_leg_direction", "zz_cand_height_pct", "zz_last_pivot_price",
            "zz_last_pivot_bar_idx", "zz_global_median", "zz_global_p80",
            "zz_local_median", "zz_n_legs_before", "zz_regime_state",
            "zz_n_legs_since_regime_open", "zz_armed", "zz_armed_side",
            "zz_n_bars_since_extreme", "zz_n_bars_since_arm", "zz_legs",
        ]
        for key in required:
            assert key in fd, f"missing zz diagnostic key: {key}"

        # Lengths of per-bar arrays match positions length
        n = res.positions.shape[0]
        for key in required:
            if key == "zz_legs":
                continue
            assert fd[key].shape[0] == n, f"{key}: length {fd[key].shape[0]} != {n}"

        # thresholds.zigzag block is present and contains our params
        assert "zigzag" in fd["thresholds"]
        assert fd["thresholds"]["zigzag"]["reversal_threshold"] == 0.005
        assert fd["thresholds"]["zigzag"]["min_legs_global"] == 5

    def test_mode_none_no_zz_fields(self):
        df = make_daily_ohlc(n_bars=200, seed=1)
        res = _run(df, filters_cfg={"mode": "none"})
        fd = res.filter_diagnostics
        # Core keys always present
        assert fd["mode"] == "none"
        assert "counters" in fd
        # No zz_* keys leak into mode=none
        zz_keys = [k for k in fd if k.startswith("zz_")]
        assert zz_keys == [], f"zz keys leaked into mode=none: {zz_keys}"

    def test_mode_amplitude_no_zz_fields_no_cross_leak(self):
        df = make_daily_ohlc(n_bars=400, seed=2)
        amp_cfg = {
            "mode": "amplitude",
            "amplitude": {"n": 10, "min_separation": None, "lookback": 100,
                          "q": 0.60, "atr_period": 14, "atr_floor": 0.0},
            "volatility": {"min_atr_pct": None, "max_atr_pct": None},
            "volume": {"volume_column": "Volume", "volume_ma_column": "Volume MA",
                       "min_ratio": None, "max_ratio": None},
        }
        res = _run(df, filters_cfg=amp_cfg)
        fd = res.filter_diagnostics
        assert fd["mode"] == "amplitude"
        # amp_* keys present, zz_* absent
        assert "amp_n" in fd
        zz_keys = [k for k in fd if k.startswith("zz_")]
        assert zz_keys == [], f"zz keys leaked into amp mode: {zz_keys}"
        # thresholds.zigzag should NOT be present in amp mode
        assert "zigzag" not in fd["thresholds"]

    def test_mode_zigzag_no_amp_fields_no_cross_leak(self):
        df = make_daily_ohlc(n_bars=400, seed=3)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        # amp_* keys must be absent
        assert "amp_n" not in fd
        assert "amp_threshold" not in fd
        assert "atr_amp" not in fd
        assert "separation" not in fd
        # thresholds.amplitude must also be absent (only zigzag stored)
        assert "amplitude" not in fd["thresholds"]


# ===========================================================================
# 2. Counters bucket — zz_* maps to blocked_by_volatility
# ===========================================================================


class TestZigZagCounters:

    def test_zz_reasons_map_into_blocked_by_volatility(self):
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        c = res.filter_diagnostics["counters"]
        # Counter invariant: raw = passed + blocked
        assert c["raw_entry_signals"] == c["passed_entry_signals"] + c["blocked_entry_signals"]
        # All zz blocks route into blocked_by_volatility bucket
        buckets_sum = (c["blocked_by_volatility"] + c["blocked_by_volume"]
                       + c["blocked_by_both"] + c["blocked_by_vol_ma_invalid"])
        assert buckets_sum <= c["blocked_entry_signals"]
        # For pure zigzag, no volume-related blocks possible.
        assert c["blocked_by_volume"] == 0
        assert c["blocked_by_both"] == 0
        assert c["blocked_by_vol_ma_invalid"] == 0


# ===========================================================================
# 3. zigzag_and_volume: both-collapse reason semantics
# ===========================================================================


class TestZigZagAndVolumeCollapse:

    def test_zigzag_and_volume_both_collapse_has_both_blocks(self):
        # Use constant volume_ma → ratio=1.0 < min_ratio=2.0 → volume fails
        # everywhere.  Combined with zigzag that fails (warmup) most bars,
        # we expect blocked_by_both > 0.
        df = _make_df_with_volume(n_bars=300, seed=42)
        vol_arr, vol_mean = _volume_ma_arr(df)
        res = _run(df, filters_cfg=_zz_vol_cfg(min_legs_global=50),
                   volume_ma=vol_arr, global_volume_ma_mean=vol_mean)
        c = res.filter_diagnostics["counters"]
        assert c["blocked_by_both"] > 0, (
            f"expected blocked_by_both > 0, got counters={c}"
        )

    def test_zigzag_and_volume_counters_consistency(self):
        df = _make_df_with_volume(n_bars=300, seed=42)
        vol_arr, vol_mean = _volume_ma_arr(df)
        res = _run(df, filters_cfg=_zz_vol_cfg(min_legs_global=5),
                   volume_ma=vol_arr, global_volume_ma_mean=vol_mean)
        c = res.filter_diagnostics["counters"]
        # Invariant: raw == passed + blocked
        assert c["raw_entry_signals"] == c["passed_entry_signals"] + c["blocked_entry_signals"]

    def test_zigzag_and_volume_both_collapse_per_bar(self):
        """
        FIX 8: покомпонентная проверка blocked_by_both.

        На warmup-барах (min_legs_global=50 → zz_warmup) AND volume fails
        (min_ratio=2.0, constant vol_ma → ratio=1.0 < 2.0) → reason должен
        быть FILTER_REASON_BOTH для тех баров, где оба фильтра блокируют.
        """
        df = _make_df_with_volume(n_bars=300, seed=42)
        vol_arr, vol_mean = _volume_ma_arr(df)
        res = _run(
            df,
            filters_cfg=_zz_vol_cfg(min_legs_global=50),
            volume_ma=vol_arr,
            global_volume_ma_mean=vol_mean,
        )
        fd = res.filter_diagnostics
        c = fd["counters"]

        # Базовая проверка: blocked_by_both > 0
        assert c["blocked_by_both"] > 0, (
            f"expected blocked_by_both > 0, got counters={c}"
        )

        # Покомпонентная: raw == passed + blocked
        assert c["raw_entry_signals"] == c["passed_entry_signals"] + c["blocked_entry_signals"], (
            f"counters inconsistency: raw={c['raw_entry_signals']}, "
            f"passed={c['passed_entry_signals']}, blocked={c['blocked_entry_signals']}"
        )

        # Counters-level инвариант: blocked_by_both <= blocked_total
        assert c["blocked_by_both"] <= c["blocked_entry_signals"]

        # blocked_by_volatility (zz) + blocked_by_volume + blocked_by_both <= blocked_total
        # (blocked_by_both уже учитывает случаи, когда оба блокируют)
        assert (c["blocked_by_volatility"] + c["blocked_by_volume"]
                + c["blocked_by_both"] + c["blocked_by_vol_ma_invalid"]
                ) <= c["blocked_entry_signals"]

        # filtered_reason array проверка (если ключ существует)
        if "filtered_reason" in fd:
            reason_arr = fd["filtered_reason"]
            # Должны существовать хотя бы некоторые бары с FILTER_REASON_BOTH
            both_count = int(np.sum(reason_arr == FILTER_REASON_BOTH))
            assert both_count > 0, (
                "filtered_reason не содержит ни одного FILTER_REASON_BOTH, "
                f"хотя blocked_by_both={c['blocked_by_both']} > 0"
            )


# ===========================================================================
# 4. allow_entry contract invariance across execution_model
# ===========================================================================


class TestExecutionModelInvariance:

    def test_zigzag_diagnostics_invariant_o2o_vs_c2c(self):
        # Plan §G.1.6: zz_* diagnostics are byte-equal across O2O and C2C
        # (only positions/returns differ due to apply_entry_filters shift).
        df = make_daily_ohlc(n_bars=300, seed=42)
        res_o2o = _run(df, filters_cfg=_zz_cfg(),
                        execution_model=ExecutionModel.OPEN_TO_OPEN)
        res_c2c = _run(df, filters_cfg=_zz_cfg(),
                        execution_model=ExecutionModel.CLOSE_TO_CLOSE)
        fd_o = res_o2o.filter_diagnostics
        fd_c = res_c2c.filter_diagnostics

        per_bar_keys = [
            "zz_leg_direction", "zz_cand_height_pct", "zz_last_pivot_price",
            "zz_last_pivot_bar_idx", "zz_global_median", "zz_global_p80",
            "zz_local_median", "zz_n_legs_before", "zz_regime_state",
            "zz_n_legs_since_regime_open", "zz_armed", "zz_armed_side",
            "zz_n_bars_since_extreme", "zz_n_bars_since_arm",
        ]
        for key in per_bar_keys:
            np.testing.assert_array_equal(
                fd_o[key], fd_c[key],
                err_msg=f"execution_model-invariance violated on {key}",
            )
        # Legs identical too
        assert fd_o["zz_legs"] == fd_c["zz_legs"]


# ===========================================================================
# 5. Unknown mode still raises; basic sanity
# ===========================================================================


class TestSanity:

    def test_unknown_mode_raises(self):
        df = make_daily_ohlc(n_bars=100, seed=1)
        bad_cfg = _zz_cfg()
        bad_cfg["mode"] = "not_a_real_mode"
        with pytest.raises(ValueError, match="unknown filter mode"):
            _run(df, filters_cfg=bad_cfg)

    def test_zigzag_and_volume_requires_volume_ma(self):
        # mode=zigzag_and_volume with volume_ma=None → ValueError inside engine.
        df = make_daily_ohlc(n_bars=100, seed=1)
        with pytest.raises(ValueError, match="volume_ma"):
            _run(df, filters_cfg=_zz_vol_cfg())   # no volume_ma provided

    def test_allow_entry_affects_positions(self):
        # Pure zigzag with min_legs_global large enough to block everything →
        # no positions should be entered.
        df = make_daily_ohlc(n_bars=200, seed=1)
        res = _run(df, filters_cfg=_zz_cfg(min_legs_global=500))
        # positions array exists; passed_entry_signals == 0
        assert res.filter_diagnostics["counters"]["passed_entry_signals"] == 0

    def test_compute_zigzag_filter_rejects_unknown_entry_side(self):
        """FIX 4: entry_side != 'counter_trend' → ValueError (plan §2, §9)."""
        from supertrend_optimizer.core.zigzag_filter import compute_zigzag_filter
        import numpy as np
        N = 10
        high = np.linspace(100, 110, N)
        low = high - 1.0
        close = (high + low) / 2
        open_p = close.copy()
        session_ids = np.zeros(N, dtype=np.int64)
        trend = np.ones(N, dtype=np.int8)
        cfg = dict(
            reversal_threshold=0.005, min_legs_global=5, q_strong=0.80,
            k_local=5, entry_side="pullback",
            arm_timeout_bars_since_extreme=24, arm_timeout_bars_hard=78,
        )
        with pytest.raises(ValueError, match="counter_trend"):
            compute_zigzag_filter(high, low, close, open_p, session_ids, trend, cfg)


# ===========================================================================
# 6. Not-proven invariants — Fix 4 (audit §tests section)
# ===========================================================================


class TestZZLegsEarlyExitTruncation:
    """
    Verifies that zz_legs in filter_diagnostics does not contain legs whose
    confirm_bar is >= len(positions) (§3.6 truncation after early_exit).

    This is a not-proven invariant from the audit (Fix 4, §4.4).
    """

    def test_zz_legs_confirm_bar_lt_positions_len(self):
        """All legs in filter_diagnostics["zz_legs"] must have confirm_bar < n."""
        df = make_daily_ohlc(n_bars=300, seed=42)
        res = _run(df, filters_cfg=_zz_cfg())
        fd = res.filter_diagnostics
        n = res.positions.shape[0]
        for lg in fd["zz_legs"]:
            assert lg.confirm_bar < n, (
                f"leg with confirm_bar={lg.confirm_bar} found in zz_legs "
                f"but positions length is {n} — truncation violated"
            )

    def test_zz_legs_truncated_on_early_exit(self):
        """
        With early_exit enabled, zz_legs must only contain legs whose
        confirm_bar < len(positions) (which is < full dataset length after exit).
        """
        df = make_daily_ohlc(n_bars=300, seed=7)
        res = _run(
            df,
            filters_cfg=_zz_cfg(),
            early_exit_enabled=True,
            early_exit_max_drawdown=0.01,   # very tight → likely triggers early
            early_exit_check_bars=20,
        )
        fd = res.filter_diagnostics
        n = res.positions.shape[0]
        for lg in fd["zz_legs"]:
            assert lg.confirm_bar < n, (
                f"early_exit truncation violated: leg confirm_bar={lg.confirm_bar} "
                f">= positions length {n}"
            )
        # zz_* per-bar arrays must also have correct length
        for key in ("zz_leg_direction", "zz_regime_state", "zz_armed"):
            if key in fd:
                assert fd[key].shape[0] == n, (
                    f"{key} length {fd[key].shape[0]} != positions length {n} "
                    "after early_exit"
                )

    def test_allow_entry_length_matches_positions_after_early_exit(self):
        """
        After early_exit, allow_entry in filter_diagnostics must have length
        equal to positions, not the full dataset (Fix 1 + Fix 4 combined).
        """
        df = make_daily_ohlc(n_bars=300, seed=7)
        res = _run(
            df,
            filters_cfg=_zz_cfg(),
            early_exit_enabled=True,
            early_exit_max_drawdown=0.01,
            early_exit_check_bars=20,
        )
        fd = res.filter_diagnostics
        n = res.positions.shape[0]
        if "allow_entry" in fd:
            assert fd["allow_entry"].shape[0] == n, (
                f"allow_entry length {fd['allow_entry'].shape[0]} != "
                f"positions length {n} after early_exit"
            )


class TestTesterOnlyEnforcement:
    """
    Verifies that zigzag / zigzag_and_volume modes are rejected by the
    optimizer/WFA validators (not-proven invariant §4.5).
    """

    def test_zigzag_is_tester_only(self):
        from supertrend_optimizer.cli.validators import _TESTER_ONLY_MODES
        assert "zigzag" in _TESTER_ONLY_MODES, (
            "'zigzag' must be in _TESTER_ONLY_MODES to be rejected by optimizer"
        )

    def test_zigzag_and_volume_is_tester_only(self):
        from supertrend_optimizer.cli.validators import _TESTER_ONLY_MODES
        assert "zigzag_and_volume" in _TESTER_ONLY_MODES, (
            "'zigzag_and_volume' must be in _TESTER_ONLY_MODES"
        )

    def test_optimizer_rejects_zigzag_mode(self):
        """
        validate_config (the optimizer validator) must raise ConfigError when
        filters.mode = 'zigzag' (tester-only mode, plan §tester-only).
        """
        from supertrend_optimizer.cli.validators import validate_config
        from supertrend_optimizer.utils.exceptions import ConfigError
        cfg = {"filters": {"mode": "zigzag"}}
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(cfg)

    def test_optimizer_rejects_zigzag_and_volume_mode(self):
        from supertrend_optimizer.cli.validators import validate_config
        from supertrend_optimizer.utils.exceptions import ConfigError
        cfg = {"filters": {"mode": "zigzag_and_volume"}}
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(cfg)

    def test_optimizer_allows_none_mode(self):
        """mode=none must NOT be rejected — it is not a tester-only mode."""
        from supertrend_optimizer.cli.validators import validate_config
        from supertrend_optimizer.utils.exceptions import ConfigError
        cfg = {"filters": {"mode": "none"}}
        try:
            validate_config(cfg)
        except ConfigError as exc:
            # Only fail if the error is specifically about tester-only
            if "tester-only" in str(exc):
                raise AssertionError(
                    f"mode='none' must not be rejected as tester-only: {exc}"
                ) from exc
            # Other ConfigError (missing required sections) is acceptable
        except Exception:
            pass  # Missing required sections etc. — not our concern here
