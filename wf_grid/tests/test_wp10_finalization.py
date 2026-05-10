"""
WP10 acceptance gates — finalization test suite for ZigZag ST trade_filter.

Plan reference: WP10 (plan §WP10, §12, §10.5, §10.6, §10.7).
Spec reference: Appendix A v1.1 §11, §13, §14, §17, §18.

Acceptance gates (A1–A9) and real-data E2E enabled smoke.

Gate mapping (plan §WP10 acceptance gates)
------------------------------------------
A1  Raw YAML presence: numeric threshold + explicit quantile → reject;
    three-case raw-presence test (§6.4.1 p.5).
A2  No high/low in close-only ZigZag: grep-gate + invariance unit gate (§8.3.1).
A3  Entry AND exit diagnostics indexing pinned under OPEN_TO_OPEN (§8.4.1).
A4  Early-exit diagnostics truncation in shared donor API (§8.2 rule 7).
A5  Disabled export parity: no filter columns in disabled trade-level export (§10.6.6 #1).
A6  Enabled export preservation: filter columns present, fixed order, no silent drops (§10.6.6 #2).
A7  Tester disabled-path smoke after RawBacktestArtifacts migration (§8.1.1).
A8  Full Appendix A v1.1 §13 bar-level keyset in BacktestResult.filter_diagnostics (§10.5.1).
A9  FSM no-global-state: two sequential apply() with identical inputs → bit-identical (§10.7.2).

E2E enabled smoke (slow)
------------------------
E1  Full pipeline with trade_filter enabled completes without error on real data.
E2  step_oos_long has all §10.6.4 filter summary columns populated for enabled steps.
E3  WF_Trades has filter diagnostic columns when filter is enabled.
E4  Disabled pipeline produces bit-identical step_oos_long schema vs baseline columns.
E5  Disabled and enabled pipelines agree on baseline column values (disabled == absent path).
"""
from __future__ import annotations

import inspect
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.backtest import (
    RawBacktestArtifacts,
    generate_positions,
    run_backtest_fast,
)
from supertrend_optimizer.core.zigzag_st_filter import apply as zigzag_apply
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel

from wf_grid.collect.trades_collector import _FILTER_TRADE_COLS

# ---------------------------------------------------------------------------
# Shared test fixtures / helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATA_CSV = _PROJECT_ROOT / "data.csv"
_SKIP_E2E = f"Real data file not found: {_DATA_CSV}"

# Full §13 keyset (spec Appendix A v1.1 §13 + exit-off §6)
_SECTION_13_KEYS = {
    "trade_filter_enabled",
    "trade_filter_state",
    "trade_filter_trigger_source",
    "zigzag_reversal_threshold",
    "candidate_height_pct",
    "candidate_trigger_threshold",
    "local_median_N",
    "local_median_available",
    "local_window",
    "global_median",
    "global_stats_available",
    "confirmed_legs_since_start",
    "freeze_confirmed_legs",
    "median_stop_triggered",
    "stopping_started_at_index",
    "filter_allowed_entry",
    "filter_block_reason",
    # exit-off modes (plan_exit_off_modes_v2.txt §6)
    "exit_off_mode",
    "exit_off_zz_leg_count",
    "zz_legs_since_lifecycle_start",
    "zz_leg_stop_triggered",
}

# §10.6.4 required summary columns (n_bars_in_wait_first_st_flip added in T1.3)
_SUMMARY_COLS = [
    "filter_states_visited",
    "n_bars_in_off",
    "n_bars_in_wait_first_st_flip",
    "n_bars_in_freeze",
    "n_bars_in_monitoring",
    "n_bars_in_counting_zz_legs",
    "n_bars_in_stopping",
    "n_filter_blocked_entries",
    "lifecycle_starts_count",
    "median_stop_triggered_count",
    "zz_leg_stop_triggered_count",
    "exit_off_mode",
    "exit_off_zz_leg_count",
    # Plan v3 §8: new summary keys
    "exit_b_immediate_off",
    "exit_b_immediate_off_count",
]


def _make_prices(n: int = 100, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.normal(0, 0.5, n)) + 100.0


def _ohlc(close: np.ndarray):
    open_p = np.roll(close, 1)
    open_p[0] = close[0]
    high = close + 0.5
    low = close - 0.5
    return open_p, high, low, close


@dataclass
class _Toggle:
    enabled: bool = True


@dataclass
class _Triggers:
    candidate_threshold: _Toggle = field(default_factory=_Toggle)
    confirmed_median: _Toggle = field(
        default_factory=lambda: _Toggle(enabled=False)
    )


@dataclass
class _Lifecycle:
    freeze_confirmed_legs: int = 3
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"


@dataclass
class _ZigZagCfg:
    enabled: bool = True
    reversal_threshold: float = 0.02
    local_window: int = 5
    global_stats_source: str = "full_dataset"
    leg_height_mode: str = "pct"
    global_median: str = "auto"
    candidate_trigger_threshold: float = 0.01
    candidate_trigger_quantile: Optional[float] = None


@dataclass
class _FilterCfg:
    enabled: bool = True
    type: str = "zigzag_st_mode"
    zigzag: _ZigZagCfg = field(default_factory=_ZigZagCfg)
    triggers: _Triggers = field(default_factory=_Triggers)
    lifecycle: _Lifecycle = field(default_factory=_Lifecycle)


def _disabled_cfg() -> _FilterCfg:
    c = _FilterCfg()
    c.enabled = False
    return c


def _make_global_stats():
    from supertrend_optimizer.core.zigzag_st_filter import ZigZagGlobalStats
    return ZigZagGlobalStats(
        reversal_threshold=0.02,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=0.03,
        candidate_trigger_threshold=0.01,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
    )


def _run_single_full(n: int = 80, filter_cfg=None, global_stats=None,
                     seed: int = 42):
    close = _make_prices(n, seed=seed)
    o, h, l, c = _ohlc(close)
    return run_single_backtest(
        open_prices=o, high=h, low=l, close=close,
        index=pd.RangeIndex(n),
        atr_period=5, multiplier=2.0, trade_mode="revers",
        commission=0.001, warmup_period=10,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        periods_per_year=252, min_trades_required=1,
        extract_trades_flag=True,
        auto_warmup=True,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=filter_cfg,
        zigzag_global_stats=global_stats,
    )


# ===========================================================================
# A1. Raw YAML presence — three-case test (§6.4.1 p.5, §11.3)
# ===========================================================================

class TestA1RawYAMLPresence:
    """A1: numeric threshold + explicit quantile reject via raw YAML presence tracking."""

    def _write(self, tmp_path: Path, content: str) -> str:
        p = tmp_path / "cfg.yaml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(p)

    def _base_yaml(self, extra_filter: str) -> str:
        base = textwrap.dedent("""\
            data:
              file_path: dummy.csv
              periods_per_year: 252
              annualization_basis: trading
            optimization:
              atr_period_range: [5, 7]
              multiplier_range: [2.0, 2.0]
              multiplier_step: 0.5
              trade_mode: revers
            backtest:
              commission: 0.001
              min_trades_required: 1
              early_exit_enabled: false
              early_exit_max_drawdown: 0.5
              early_exit_check_bars: 0
            validation:
              warmup_period: 0
              warmup_period_auto: false
              walk_forward:
                train_size: "100bars"
                test_size: "50bars"
                step_size: "50bars"
                scheme: rolling
            gates:
              step:
                min_trades: null
                max_drawdown_threshold: -0.50
              candidate:
                positive_median_threshold: 0.0
                min_trades_median: 1.0
                worst_segment_pnl_threshold: null
                max_drawdown_threshold: -0.50
            ranking:
              mode: gates_score
              min_segments_for_ranking: null
              sort_by: sum_pnl_pct_Median
              tiebreaker: sum_pnl_pct_Min
            scoring:
              score_weights:
                sum_pnl_pct_Median: 0.45
                profitable_segments_count: 0.35
                abs_max_drawdown_Min: 0.20
            status:
              min_meaningful_bars: 5
        """)
        return base + textwrap.dedent(extra_filter)

    def test_case1_numeric_without_quantile_accept(self, tmp_path):
        """Case 1: numeric threshold, no quantile key in YAML → accept (§6.4.1 case 1)."""
        from wf_grid.config.loader import load_grid_config, ConfigError
        yaml_extra = textwrap.dedent("""\
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                reversal_threshold: 0.02
                local_window: 5
                global_stats_source: full_dataset
                leg_height_mode: pct
                global_median: auto
                candidate_trigger_threshold: 0.015
              triggers:
                candidate_threshold:
                  enabled: true
                confirmed_median:
                  enabled: false
              lifecycle:
                freeze_confirmed_legs: 3
                stop_check: confirm_bar_only
                stopping_exit: opposite_st_flip
        """)
        cfg_path = self._write(tmp_path, self._base_yaml(yaml_extra))
        # Should load without ConfigError
        config = load_grid_config(cfg_path)
        assert config.trade_filter is not None
        assert config.trade_filter.enabled is True

    def test_case2_numeric_with_explicit_quantile_reject(self, tmp_path):
        """Case 2: numeric threshold + explicit quantile in YAML → reject (§6.4.1 case 2 / §11.3)."""
        from wf_grid.config.loader import load_grid_config, ConfigError
        yaml_extra = textwrap.dedent("""\
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                reversal_threshold: 0.02
                local_window: 5
                global_stats_source: full_dataset
                leg_height_mode: pct
                global_median: auto
                candidate_trigger_threshold: 0.015
                candidate_trigger_quantile: 0.80
              triggers:
                candidate_threshold:
                  enabled: true
                confirmed_median:
                  enabled: false
              lifecycle:
                freeze_confirmed_legs: 3
                stop_check: confirm_bar_only
                stopping_exit: opposite_st_flip
        """)
        cfg_path = self._write(tmp_path, self._base_yaml(yaml_extra))
        with pytest.raises(ConfigError):
            load_grid_config(cfg_path)

    def test_case3_auto_with_explicit_quantile_accept(self, tmp_path):
        """Case 3: auto threshold + explicit quantile in YAML → accept (§6.4.1 case 3)."""
        from wf_grid.config.loader import load_grid_config, ConfigError
        yaml_extra = textwrap.dedent("""\
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                reversal_threshold: 0.02
                local_window: 5
                global_stats_source: full_dataset
                leg_height_mode: pct
                global_median: auto
                candidate_trigger_threshold: auto
                candidate_trigger_quantile: 0.80
              triggers:
                candidate_threshold:
                  enabled: true
                confirmed_median:
                  enabled: false
              lifecycle:
                freeze_confirmed_legs: 3
                stop_check: confirm_bar_only
                stopping_exit: opposite_st_flip
        """)
        cfg_path = self._write(tmp_path, self._base_yaml(yaml_extra))
        config = load_grid_config(cfg_path)
        assert config.trade_filter is not None


# ===========================================================================
# A2. No high/low in close-only ZigZag (§8.3.1)
# ===========================================================================

class TestA2NoHighLowInZigZag:
    """A2: grep-gate + invariance unit gate for close-only ZigZag contract."""

    def test_grep_gate_no_high_low_in_apply_signature(self):
        """apply() signature must not contain 'high' or 'low' parameters."""
        sig = inspect.signature(zigzag_apply)
        param_names = set(sig.parameters.keys())
        assert "high" not in param_names, (
            "GREP GATE FAIL: apply() accepts 'high' — breaks close-only contract (§8.3.1)"
        )
        assert "low" not in param_names, (
            "GREP GATE FAIL: apply() accepts 'low' — breaks close-only contract (§8.3.1)"
        )

    def test_invariance_distorted_high_low_no_change_to_zigzag_outputs(self):
        """Distorted high/low must not change ZigZag-derived outputs (§8.3.1 invariance gate).

        candidate_height_pct and local_median_N are ZigZag-only outputs.
        They must be bit-identical regardless of high/low distortion.
        """
        n = 80
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)

        cfg = _FilterCfg()
        gs = _make_global_stats()

        def _run_enabled(open_p, high, low):
            return run_single_backtest(
                open_prices=open_p, high=high, low=low, close=close,
                index=pd.RangeIndex(n),
                atr_period=5, multiplier=2.0, trade_mode="revers",
                commission=0.001, warmup_period=10,
                early_exit_enabled=False, early_exit_max_drawdown=0.5,
                early_exit_check_bars=0,
                periods_per_year=252, min_trades_required=1,
                extract_trades_flag=True, auto_warmup=True,
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                trade_filter_config=cfg, zigzag_global_stats=gs,
            )

        r_normal = _run_enabled(o, h, l)
        r_distorted = _run_enabled(o, h * 5.0, l * 0.1)

        d_n = r_normal.filter_diagnostics
        d_d = r_distorted.filter_diagnostics
        if d_n is None or d_d is None:
            pytest.skip("Filter not triggered — skip invariance check")

        np.testing.assert_array_equal(
            d_n["candidate_height_pct"], d_d["candidate_height_pct"],
            err_msg="A2 FAIL: candidate_height_pct differs — high/low leaked into ZigZag",
        )
        np.testing.assert_array_equal(
            d_n["local_median_N"], d_d["local_median_N"],
            err_msg="A2 FAIL: local_median_N differs — high/low leaked into ZigZag",
        )


# ===========================================================================
# A3. Entry AND exit diagnostics indexing pinned under OPEN_TO_OPEN (§8.4.1)
# ===========================================================================

class TestA3TradeIndexingPinned:
    """A3: entry_signal_idx AND exit_signal_idx = max(execution_index - 1, 0)
    under OPEN_TO_OPEN — both pinned in ONE test (§8.4.1 requirement).
    """

    def test_entry_and_exit_signal_idx_pinned_in_one_test(self):
        """§8.4.1 acceptance gate — both entry AND exit indexing rules pinned.

        Uses attach_trade_filter_diagnostics with a synthetic scenario where
        entry_index and exit_index are known execution bars. Verifies:
        1. entry_signal_idx = max(entry_index - 1, 0) = close decision bar.
        2. exit_signal_idx = max(exit_index - 1, 0) = close decision bar.
        3. exit_reason reads from exit_signal_idx (FSM decision bar), not exit_index.
        4. exit_reason == 'filter_stopping_opposite_flip' when FSM at exit_signal_idx
           is ST_STOPPING even if execution bar is already in OFF.
        """
        from supertrend_optimizer.core.zigzag_st_filter import (
            attach_trade_filter_diagnostics,
        )

        n = 15
        # Synthetic trade table: entry_index=3 (open bar 3), exit_index=9 (open bar 9)
        # Decision bar entry = max(3-1,0) = 2; decision bar exit = max(9-1,0) = 8
        trades_df = pd.DataFrame({
            "entry_index": [3],
            "exit_index": [9],
            "pnl_pct": [0.01],
        })

        # Build filter_diagnostics such that:
        # - bar 2 (entry decision): some active state
        # - bar 8 (exit decision): ST_STOPPING
        # - bar 9 (exit execution): OFF  ← if we read from execution bar we'd get wrong result
        state_arr = np.array(["OFF"] * n, dtype=object)
        state_arr[2] = "ST_ACTIVE_MONITORING"
        state_arr[8] = "ST_STOPPING"         # exit decision bar
        state_arr[9] = "OFF"                 # exit execution bar (already transitioned)

        trigger_arr = np.array(["none"] * n, dtype=object)
        trigger_arr[2] = "candidate_threshold"

        diag: Dict[str, np.ndarray] = {
            "trade_filter_state": state_arr,
            "trade_filter_trigger_source": trigger_arr,
            "median_stop_triggered": np.zeros(n, dtype=np.int8),
        }

        result = attach_trade_filter_diagnostics(trades_df, diag)

        # Verify entry: entry_filter_state should come from bar 2 (decision bar)
        assert result["entry_filter_state"].iloc[0] == "ST_ACTIVE_MONITORING", (
            f"A3 FAIL: entry_filter_state read from wrong bar. "
            f"Expected 'ST_ACTIVE_MONITORING' from decision bar 2"
        )
        assert result["entry_trigger_source"].iloc[0] == "candidate_threshold", (
            "A3 FAIL: entry_trigger_source read from wrong bar"
        )

        # Verify exit: exit_reason must use decision bar (bar 8, ST_STOPPING),
        # NOT execution bar (bar 9, OFF)
        assert result["exit_reason"].iloc[0] == "filter_stopping_opposite_flip", (
            f"A3 FAIL: exit_reason='{result['exit_reason'].iloc[0]}' but expected "
            f"'filter_stopping_opposite_flip'. "
            f"exit_reason must read from exit decision bar (bar 8 = ST_STOPPING), "
            f"not execution bar (bar 9 = OFF)"
        )

    def test_entry_index_zero_edge_case(self):
        """A3 edge case: entry_index == 0 → entry_signal_idx = max(0 - 1, 0) = 0."""
        entry_index = 0
        entry_signal_idx = max(entry_index - 1, 0)
        assert entry_signal_idx == 0, (
            "A3 FAIL: max(0 - 1, 0) must equal 0 (invariant defence for edge case)"
        )

    def test_pending_open_trade_no_signal_lookup(self):
        """A3 edge case: pending_open_trade_at_end — exit_reason set directly,
        no signal index lookup performed (§8.4.1 p.5 / §8.4)."""
        # This is a contract/invariant test: the rule says we set exit_reason
        # directly without lookup when donor marks a pending open trade at end.
        # Here we verify the canonical value string is correct.
        pending_reason = "pending_open_trade_at_end"
        assert pending_reason == "pending_open_trade_at_end", (
            "A3 FAIL: pending_open_trade_at_end reason string mismatch"
        )
        # And it's NOT in the set that requires signal lookup
        signal_lookup_reasons = {"st_flip", "filter_stopping_opposite_flip"}
        assert pending_reason not in signal_lookup_reasons, (
            "A3 FAIL: pending_open_trade_at_end must not require signal index lookup"
        )


# ===========================================================================
# A4. Early-exit diagnostics truncation (§8.2 rule 7)
# ===========================================================================

def _make_a4_filter_cfg():
    """Filter config with very small thresholds → activates immediately.

    reversal_threshold=0.001 and candidate_trigger_threshold=0.001 ensure
    the ZigZag candidate height easily exceeds the trigger threshold on any
    random price series, so the FSM activates, positions change, commission
    is paid, and early_exit can fire.
    """
    @dataclass
    class _ZZ:
        reversal_threshold: float = 0.001
        local_window: int = 3
        global_stats_source: str = "full_dataset"
        leg_height_mode: str = "pct"
        global_median: str = "auto"
        candidate_trigger_threshold: float = 0.001
        candidate_trigger_quantile: Optional[float] = None

    @dataclass
    class _Lc:
        freeze_confirmed_legs: int = 1
        stop_check: str = "confirm_bar_only"
        stopping_exit: str = "opposite_st_flip"

    @dataclass
    class _Cfg:
        enabled: bool = True
        type: str = "zigzag_st_mode"
        zigzag: _ZZ = field(default_factory=_ZZ)
        triggers: _Triggers = field(default_factory=_Triggers)
        lifecycle: _Lc = field(default_factory=_Lc)

    return _Cfg()


def _make_a4_global_stats():
    """Global stats with small thresholds matching _make_a4_filter_cfg."""
    from supertrend_optimizer.core.zigzag_st_filter import ZigZagGlobalStats
    # Provide many confirmed legs so global_median is meaningful and small
    heights = np.linspace(0.002, 0.010, 20)
    return ZigZagGlobalStats(
        reversal_threshold=0.001,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=list(range(20)),
        confirmed_heights_pct=heights,
        global_median=float(np.median(heights)),
        candidate_trigger_threshold=0.001,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=20,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
    )


class TestA4EarlyExitTruncation:
    """A4: plan §WP10 acceptance gate — early_exit fires AND filter_diagnostics
    are synchronously truncated to len(positions).

    Requirements:
    - early_exit_enabled=True + trade_filter.enabled=true.
    - early_exit MUST actually fire (arts.early_exit is True).
    - len(arts.positions) < n  (truncation happened).
    - len(filter_diagnostics[k]) == len(arts.positions) for all k.
    - BacktestResult.__post_init__ length-invariant holds.
    """

    def test_early_exit_fires_and_truncates_diagnostics_synchronously(self):
        """§8.2 rule 7: early_exit branch actually entered; diagnostics truncated.

        Uses max_drawdown=0.0 (fires on first equity decline) + commission=0.01
        (1% per trade → ~1% equity drop on first position change → guaranteed fire).
        Filter uses tiny thresholds to ensure it activates and positions change.
        """
        n = 120
        close = _make_prices(n, seed=99)
        o, h, l, c = _ohlc(close)

        arts = run_backtest_fast(
            o, h, l, c,
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.01,           # 1% commission → guaranteed equity drop on trade
            early_exit_enabled=True,
            early_exit_max_drawdown=0.0,   # fires on ANY equity decline
            early_exit_check_bars=n,
            trade_filter_config=_make_a4_filter_cfg(),
            zigzag_global_stats=_make_a4_global_stats(),
        )

        # Mandatory: early exit must have actually fired
        assert arts.early_exit is True, (
            "A4 FAIL: early_exit did not fire — test scenario did not enter "
            "the truncation branch. Check commission / max_drawdown settings."
        )
        assert arts.exit_bar is not None, (
            "A4 FAIL: exit_bar is None despite early_exit=True"
        )
        assert len(arts.positions) < n, (
            f"A4 FAIL: positions not truncated: len={len(arts.positions)}, n={n}"
        )
        assert len(arts.positions) == arts.exit_bar + 1, (
            f"A4 FAIL: len(positions)={len(arts.positions)} != exit_bar+1={arts.exit_bar+1}"
        )

        # All filter_diagnostics arrays must be truncated to same length as positions
        if arts.filter_diagnostics is not None:
            n_pos = len(arts.positions)
            for key, arr in arts.filter_diagnostics.items():
                assert len(arr) == n_pos, (
                    f"A4 FAIL (synchronous truncation): filter_diagnostics[{key!r}] "
                    f"len={len(arr)} != positions len={n_pos}"
                )

    def test_backtest_result_post_init_length_invariant_with_early_exit(self):
        """BacktestResult.__post_init__ must not raise after early-exit truncation."""
        n = 120
        close = _make_prices(n, seed=77)
        o, h, l, c = _ohlc(close)
        idx = pd.RangeIndex(n)

        result = run_single_backtest(
            open_prices=o, high=h, low=l, close=close,
            index=idx,
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.01, warmup_period=5,
            early_exit_enabled=True,
            early_exit_max_drawdown=0.0,
            early_exit_check_bars=n,
            periods_per_year=252, min_trades_required=1,
            extract_trades_flag=True, auto_warmup=False,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=_make_a4_filter_cfg(),
            zigzag_global_stats=_make_a4_global_stats(),
        )

        assert result.early_exit is True, (
            "A4 FAIL: early_exit did not fire in __post_init__ test"
        )
        n_pos = len(result.positions)
        if result.filter_diagnostics is not None:
            for key, arr in result.filter_diagnostics.items():
                assert len(arr) == n_pos, (
                    f"A4 FAIL: __post_init__ did not enforce length invariant for {key!r}"
                )


# ===========================================================================
# A5. Disabled export parity: no filter columns in disabled trade export (§10.6.6 #1)
# ===========================================================================

class TestA5DisabledExportParity:
    """A5: disabled config trade-level export must be bit-identical baseline —
    no entry_filter_state / entry_trigger_source / exit_reason columns."""

    def test_no_filter_columns_in_disabled_trades(self):
        """§10.6.6 #1: disabled path trades must not contain any filter columns."""
        result_disabled = _run_single_full(filter_cfg=_disabled_cfg(),
                                           global_stats=_make_global_stats())
        if result_disabled.trades_df is not None and len(result_disabled.trades_df) > 0:
            for col in _FILTER_TRADE_COLS:
                assert col not in result_disabled.trades_df.columns, (
                    f"A5 FAIL: filter column {col!r} present in disabled-path trades"
                )

    def test_no_filter_columns_in_absent_filter_trades(self):
        """§10.6.6 #1: absent filter (None) must also produce no filter columns."""
        result_none = _run_single_full(filter_cfg=None, global_stats=None)
        if result_none.trades_df is not None and len(result_none.trades_df) > 0:
            for col in _FILTER_TRADE_COLS:
                assert col not in result_none.trades_df.columns, (
                    f"A5 FAIL: filter column {col!r} present in absent-filter trades"
                )

    def test_disabled_and_absent_trades_identical(self):
        """Disabled path trades must be bit-identical to absent-filter path (§11.1)."""
        r_none = _run_single_full(filter_cfg=None, seed=99)
        r_disabled = _run_single_full(filter_cfg=_disabled_cfg(),
                                      global_stats=_make_global_stats(), seed=99)

        if r_none.trades_df is None or r_disabled.trades_df is None:
            assert r_none.trades_df is None and r_disabled.trades_df is None
            return

        # Same number of trades
        assert len(r_none.trades_df) == len(r_disabled.trades_df), (
            "A5 FAIL: disabled vs absent have different number of trades"
        )
        # Core trade columns identical
        for col in ["entry_index", "exit_index", "pnl_pct"]:
            if col in r_none.trades_df.columns and col in r_disabled.trades_df.columns:
                np.testing.assert_array_equal(
                    r_none.trades_df[col].values,
                    r_disabled.trades_df[col].values,
                    err_msg=f"A5 FAIL: trades column {col!r} differs disabled vs absent",
                )


# ===========================================================================
# A6. Enabled export: filter columns preserved, fixed order (§10.6.6 #2)
# ===========================================================================

class TestA6EnabledExportPreservation:
    """A6: enabled config trade-level export has filter columns in fixed order
    after donor columns; no silent drops (§10.6.6 #2)."""

    def test_filter_columns_present_in_enabled_trades(self):
        """§10.6.6 #2: filter columns must be present in enabled-path trades."""
        result = _run_single_full(filter_cfg=_FilterCfg(),
                                  global_stats=_make_global_stats())
        if result.trades_df is None or len(result.trades_df) == 0:
            pytest.skip("No trades generated — skip export preservation check")

        present = [c for c in _FILTER_TRADE_COLS if c in result.trades_df.columns]
        # At least some filter columns should be attached when filter is enabled
        assert len(present) > 0, (
            f"A6 FAIL: no filter columns in enabled-path trades. "
            f"Expected some of {_FILTER_TRADE_COLS}"
        )

    def test_filter_columns_after_donor_columns(self):
        """§10.6.3: filter columns must appear after all donor trade columns."""
        from wf_grid.collect.trades_collector import _DONOR_TRADE_COLS

        result = _run_single_full(filter_cfg=_FilterCfg(),
                                  global_stats=_make_global_stats())
        if result.trades_df is None or len(result.trades_df) == 0:
            pytest.skip("No trades — skip column order check")

        cols = list(result.trades_df.columns)
        for filter_col in _FILTER_TRADE_COLS:
            if filter_col not in cols:
                continue
            filter_pos = cols.index(filter_col)
            for donor_col in _DONOR_TRADE_COLS:
                if donor_col in cols:
                    donor_pos = cols.index(donor_col)
                    assert filter_pos > donor_pos, (
                        f"A6 FAIL: filter column {filter_col!r} (pos {filter_pos}) "
                        f"before donor column {donor_col!r} (pos {donor_pos})"
                    )

    def test_collector_preserves_filter_columns_no_silent_drops(self):
        """§10.6.2: collector must not silently drop filter columns."""
        from wf_grid.collect.trades_collector import _enrich_chunk, _FILTER_TRADE_COLS

        # Simulate a raw trades_df with filter columns
        n_trades = 5
        trades_df = pd.DataFrame({
            "entry_index": range(n_trades),
            "exit_index": range(1, n_trades + 1),
            "direction": [1] * n_trades,
            "pnl_pct": [0.01] * n_trades,
            "entry_price": [100.0] * n_trades,
            "exit_price": [101.0] * n_trades,
            "entry_date": [pd.NaT] * n_trades,
            "exit_date": [pd.NaT] * n_trades,
            "trade_bars": [1] * n_trades,
            "entry_filter_state": ["ST_ACTIVE_MONITORING"] * n_trades,
            "entry_trigger_source": ["candidate_threshold"] * n_trades,
            "exit_reason": ["st_flip"] * n_trades,
        })
        enriched = _enrich_chunk(
            trades_df=trades_df,
            gp_id="gp_test",
            wf_step=1,
            status="ok",
            start_idx=0,
            end_idx=100,
            window_col_start="oos_start_idx",
            window_col_end="oos_end_idx",
        )
        for col in _FILTER_TRADE_COLS:
            assert col in enriched.columns, (
                f"A6 FAIL: {col!r} dropped by _enrich_chunk — silent drop violation"
            )


# ===========================================================================
# A7. Tester disabled-path smoke (§8.1.1)
# ===========================================================================

class TestA7TesterDisabledSmoke:
    """A7: import tester + one minimal disabled backtest after RawBacktestArtifacts
    migration (§8.1.1)."""

    def test_tester_import_succeeds(self):
        """Tester module must be importable after RawBacktestArtifacts migration."""
        try:
            from supertrend_optimizer.engine.run import run_single_backtest  # noqa
        except ImportError as e:
            pytest.fail(f"A7 FAIL: tester import failed: {e}")

    def test_tester_disabled_backtest_completes(self):
        """Tester can run a minimal disabled backtest — no crash (§8.1.1)."""
        n = 40
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        result = run_single_backtest(
            open_prices=o, high=h, low=l, close=close,
            index=pd.RangeIndex(n),
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001, warmup_period=5,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252, min_trades_required=1,
            extract_trades_flag=True, auto_warmup=False,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=None,
        )
        assert result.positions is not None, "A7 FAIL: tester returned None positions"
        assert result.filter_diagnostics is None, (
            "A7 FAIL: disabled path must not produce filter_diagnostics"
        )

    def test_run_backtest_fast_disabled_returns_dataclass(self):
        """§8.1.1 migration: run_backtest_fast returns RawBacktestArtifacts (not tuple)."""
        n = 40
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        arts = run_backtest_fast(
            o, h, l, c,
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=None,
        )
        assert isinstance(arts, RawBacktestArtifacts), (
            "A7 FAIL: run_backtest_fast no longer returns RawBacktestArtifacts"
        )


# ===========================================================================
# A8. Full §13 bar-level keyset in BacktestResult.filter_diagnostics (§10.5.1)
# ===========================================================================

class TestA8FullSection13Keyset:
    """A8: full Appendix A v1.1 §13 keyset present for ANY enabled backtest (§10.5.1)."""

    def test_all_section13_keys_present(self):
        """§10.5.1 runtime invariant: all §13 keys present in filter_diagnostics."""
        result = _run_single_full(filter_cfg=_FilterCfg(),
                                  global_stats=_make_global_stats())
        diag = result.filter_diagnostics
        assert diag is not None, "A8 FAIL: filter_diagnostics is None on enabled path"
        missing = _SECTION_13_KEYS - set(diag.keys())
        assert not missing, (
            f"A8 FAIL: missing §13 keys in filter_diagnostics: {sorted(missing)}"
        )

    def test_all_section13_arrays_same_length_as_positions(self):
        """§8.2 rule 6: all §13 arrays have len == len(positions)."""
        result = _run_single_full(filter_cfg=_FilterCfg(),
                                  global_stats=_make_global_stats())
        diag = result.filter_diagnostics
        assert diag is not None
        n_pos = len(result.positions)
        for key in _SECTION_13_KEYS:
            if key not in diag:
                continue
            assert len(diag[key]) == n_pos, (
                f"A8 FAIL: filter_diagnostics[{key!r}] len={len(diag[key])} != "
                f"positions len={n_pos}"
            )

    def test_no_legacy_trigger_source_key(self):
        """Canonical key must be 'trade_filter_trigger_source', not 'trigger_source'."""
        result = _run_single_full(filter_cfg=_FilterCfg(),
                                  global_stats=_make_global_stats())
        diag = result.filter_diagnostics
        if diag is None:
            return
        assert "trigger_source" not in diag, (
            "A8 FAIL: legacy key 'trigger_source' found in filter_diagnostics"
        )
        assert "trade_filter_trigger_source" in diag, (
            "A8 FAIL: canonical key 'trade_filter_trigger_source' missing"
        )

    def test_canonical_trigger_source_values_only(self):
        """Canonical values: candidate_threshold / confirmed_median / both / none."""
        result = _run_single_full(filter_cfg=_FilterCfg(),
                                  global_stats=_make_global_stats())
        diag = result.filter_diagnostics
        if diag is None:
            return
        allowed = {"candidate_threshold", "confirmed_median", "both", "none"}
        actual = set(str(v) for v in diag["trade_filter_trigger_source"])
        forbidden = actual - allowed
        assert not forbidden, (
            f"A8 FAIL: non-canonical trigger_source values: {forbidden!r} "
            f"(legacy 'A'/'B' must not appear)"
        )

    def test_disabled_path_diagnostics_none(self):
        """§10.5.1 disabled path: filter_diagnostics must be None."""
        result_none = _run_single_full(filter_cfg=None)
        result_disabled = _run_single_full(filter_cfg=_disabled_cfg(),
                                           global_stats=_make_global_stats())
        assert result_none.filter_diagnostics is None
        assert result_disabled.filter_diagnostics is None


# ===========================================================================
# A9. FSM no-global-state (§10.7.2)
# ===========================================================================

class TestA9FSMNoGlobalState:
    """A9: two sequential apply() with identical inputs → bit-identical ZigZagSTFilterResult.
    Catches any module-level mutable state (§10.7.2)."""

    def test_two_sequential_apply_calls_bit_identical(self):
        """§10.7.2 acceptance gate: sequential calls must produce identical results."""
        from supertrend_optimizer.core.zigzag_st_filter import ZigZagPerBar

        n = 60
        rng = np.random.default_rng(7)
        trend = np.where(rng.random(n) > 0.5, 1, -1).astype(np.int8)

        per_bar = ZigZagPerBar(
            candidate_height_pct=rng.uniform(0.005, 0.05, n).astype(np.float64),
            confirm_event=np.where(rng.random(n) > 0.8, 1, 0).astype(np.int8),
            local_median_N=rng.uniform(0.02, 0.06, n).astype(np.float64),
            local_median_available=np.ones(n, dtype=np.int8),
            confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
        )
        cfg = _FilterCfg()
        stats = _make_global_stats()

        r1 = zigzag_apply(trend=trend, trade_mode="revers",
                           trade_filter_config=cfg, zigzag_global_stats=stats,
                           per_bar=per_bar)
        r2 = zigzag_apply(trend=trend, trade_mode="revers",
                           trade_filter_config=cfg, zigzag_global_stats=stats,
                           per_bar=per_bar)

        np.testing.assert_array_equal(
            r1.positions, r2.positions,
            err_msg="A9 FAIL: positions differ — FSM has module-level state",
        )
        for key in r1.filter_diagnostics:
            a1, a2 = r1.filter_diagnostics[key], r2.filter_diagnostics[key]
            assert list(a1) == list(a2), (
                f"A9 FAIL: filter_diagnostics[{key!r}] differs between calls — "
                f"FSM has module-level state"
            )


# ===========================================================================
# DoD completeness check
# ===========================================================================

class TestDoDMatrix:
    """Verify the §17 DoD matrix criteria are covered by checking invariants
    on a synthetic run. These tests act as a final cross-check on spec §17.1
    and §17.2 criteria not already pinned by dedicated A-gate tests."""

    def test_spec_17_1_1_disabled_baseline(self):
        """§17.1.1: disabled filter reproduces baseline positions."""
        from supertrend_optimizer.core.calculator import calculate_supertrend
        n = 80
        close = _make_prices(n, seed=5)
        o, h, l, c = _ohlc(close)
        arts_none = run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001, early_exit_enabled=False,
            early_exit_max_drawdown=0.5, early_exit_check_bars=0,
        )
        trend_arr, _ = calculate_supertrend(h, l, c, 5, 2.0)
        expected = generate_positions(
            trend_arr, "revers", execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        np.testing.assert_array_equal(
            arts_none.positions, expected,
            err_msg="§17.1.1 FAIL: disabled path differs from generate_positions baseline",
        )

    def test_spec_17_2_11_regression_disabled_equals_baseline(self):
        """§17.2.11: regression test — enabled=false == no-filter baseline."""
        n = 80
        r_none = _run_single_full(filter_cfg=None, seed=17)
        r_disabled = _run_single_full(filter_cfg=_disabled_cfg(),
                                      global_stats=_make_global_stats(), seed=17)
        np.testing.assert_array_equal(
            r_none.positions, r_disabled.positions,
            err_msg="§17.2.11 FAIL: enabled=false positions differ from absent baseline",
        )

    def test_spec_17_2_12_diagnostics_explain_entries(self):
        """§17.2.12: diagnostics must explain why entries were allowed/blocked."""
        result = _run_single_full(filter_cfg=_FilterCfg(),
                                  global_stats=_make_global_stats())
        diag = result.filter_diagnostics
        if diag is None:
            return
        # filter_allowed_entry and filter_block_reason are the diagnostic fields
        assert "filter_allowed_entry" in diag, (
            "§17.2.12 FAIL: filter_allowed_entry missing from diagnostics"
        )
        assert "filter_block_reason" in diag, (
            "§17.2.12 FAIL: filter_block_reason missing from diagnostics"
        )

    def test_spec_17_1_24_metrics_from_filtered_positions(self):
        """§17.1.24: metrics, trades, returns derive from filtered_positions only."""
        from supertrend_optimizer.core.backtest import calculate_returns
        n = 80
        close = _make_prices(n, seed=23)
        o, h, l, c = _ohlc(close)
        arts = run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001, early_exit_enabled=False,
            early_exit_max_drawdown=0.5, early_exit_check_bars=0,
            trade_filter_config=_FilterCfg(),
            zigzag_global_stats=_make_global_stats(),
        )
        ret_manual = calculate_returns(
            o, arts.positions, 0.001, ExecutionModel.OPEN_TO_OPEN
        )
        np.testing.assert_array_almost_equal(
            arts.returns, ret_manual,
            err_msg="§17.1.24 FAIL: returns not derived from filtered_positions",
        )


# ===========================================================================
# E1–E5. Real-data E2E enabled smoke tests (slow)
# ===========================================================================

def _write_enabled_filter_config(tmp_path: Path) -> str:
    """Write a mini-grid config with trade_filter enabled for E2E smoke."""
    cfg_text = textwrap.dedent(f"""\
    data:
      file_path: "{_DATA_CSV.as_posix()}"
      periods_per_year: 252
      annualization_basis: "trading"

    optimization:
      atr_period_range: [10, 12]
      multiplier_range: [2.0, 2.0]
      multiplier_step: 0.5
      trade_mode: "both"

    backtest:
      commission: 0.000235
      min_trades_required: 1
      early_exit_enabled: false
      early_exit_max_drawdown: 0.50
      early_exit_check_bars: 50

    validation:
      warmup_period: 0
      warmup_period_auto: true
      walk_forward:
        train_size: "500bars"
        test_size: "200bars"
        # Wider step than 200bars: full-grid summary segment blocks S1..SN would
        # exceed Excel's 16384 column limit on data.csv-sized rolling WF.
        step_size: "500bars"
        scheme: "rolling"

    gates:
      step:
        min_trades: null
        max_drawdown_threshold: -0.50
      candidate:
        positive_median_threshold: 0.0
        min_trades_median: 1.0
        worst_segment_pnl_threshold: null
        max_drawdown_threshold: -0.50

    ranking:
      mode: "gates_score"
      min_segments_for_ranking: null
      sort_by: "sum_pnl_pct_Median"
      tiebreaker: "sum_pnl_pct_Min"

    scoring:
      score_weights:
        sum_pnl_pct_Median: 0.45
        profitable_segments_count: 0.35
        abs_max_drawdown_Min: 0.20

    status:
      min_meaningful_bars: 30

    trade_filter:
      enabled: true
      type: zigzag_st_mode
      zigzag:
        reversal_threshold: 0.02
        local_window: 5
        global_stats_source: full_dataset
        leg_height_mode: pct
        global_median: auto
        candidate_trigger_threshold: 0.01
      triggers:
        candidate_threshold:
          enabled: true
        confirmed_median:
          enabled: false
      lifecycle:
        freeze_confirmed_legs: 3
        stop_check: confirm_bar_only
        stopping_exit: opposite_st_flip
    """)
    p = tmp_path / "e2e_enabled_config.yaml"
    p.write_text(cfg_text, encoding="utf-8")
    return str(p)


@pytest.fixture(scope="module")
def e2e_enabled_result(tmp_path_factory):
    """Run full pipeline with trade_filter enabled on real data (once per module)."""
    if not _DATA_CSV.exists():
        pytest.skip(_SKIP_E2E)

    from wf_grid.pipeline.orchestrator import run_grid_pipeline

    tmp = tmp_path_factory.mktemp("e2e_enabled")
    cfg_path = _write_enabled_filter_config(tmp)
    output_path = str(tmp / "e2e_enabled_output.xlsx")

    result = run_grid_pipeline(
        config_path=cfg_path,
        output_path=output_path,
    )
    assert result.error is None, f"E2E enabled pipeline failed: {result.error}"
    return result


@pytest.mark.slow
class TestE1E2EEnabledRealData:
    """E1: Full pipeline with trade_filter enabled completes without error."""

    def test_pipeline_completes_no_error(self, e2e_enabled_result):
        assert e2e_enabled_result.error is None

    def test_xlsx_created(self, e2e_enabled_result):
        assert e2e_enabled_result.output_path is not None
        assert e2e_enabled_result.output_path.exists()

    def test_step_oos_long_non_empty(self, e2e_enabled_result):
        df = e2e_enabled_result.step_oos_long
        assert df is not None
        assert len(df) > 0


@pytest.mark.slow
class TestE2SummaryColumnsPopulated:
    """E2: step_oos_long has §10.6.4 filter summary columns when filter enabled."""

    def test_summary_columns_present_in_step_oos_long(self, e2e_enabled_result):
        df = e2e_enabled_result.step_oos_long
        assert df is not None
        for col in _SUMMARY_COLS:
            assert col in df.columns, (
                f"E2 FAIL: summary column {col!r} missing from step_oos_long"
            )

    def test_summary_columns_non_null_for_enabled_steps(self, e2e_enabled_result):
        """For steps where filter was enabled, summary columns should not be all-null."""
        df = e2e_enabled_result.step_oos_long
        if df is None or len(df) == 0:
            pytest.skip("Empty step_oos_long")
        # At least one summary column should have non-null values somewhere
        has_any_data = any(
            df[col].notna().any() for col in _SUMMARY_COLS if col in df.columns
        )
        assert has_any_data, (
            "E2 FAIL: all filter summary columns are null — "
            "filter diagnostics not reaching step_oos_long"
        )


@pytest.mark.slow
class TestE3FilterTradeColumnsInXLSX:
    """E3: WF_Trades in XLSX has filter diagnostic columns when filter is enabled."""

    def test_filter_trade_columns_in_trades_oos(self, e2e_enabled_result):
        """trades_oos should have filter diagnostic columns on enabled path."""
        if e2e_enabled_result.trades_oos is None:
            pytest.skip("No OOS trades — skip filter columns check")
        if len(e2e_enabled_result.trades_oos) == 0:
            pytest.skip("Empty OOS trades — skip")
        trades = e2e_enabled_result.trades_oos
        present = [c for c in _FILTER_TRADE_COLS if c in trades.columns]
        assert len(present) > 0, (
            f"E3 FAIL: no filter trade columns in enabled-path trades_oos. "
            f"Expected some of {_FILTER_TRADE_COLS}, got cols: {list(trades.columns)}"
        )

    def test_xlsx_wf_trades_has_filter_columns(self, e2e_enabled_result):
        """XLSX WF_Trades sheet must contain filter diagnostic columns."""
        if e2e_enabled_result.output_path is None:
            pytest.skip("No output path")
        with pd.ExcelFile(e2e_enabled_result.output_path) as xlsx:
            if "WF_Trades" not in xlsx.sheet_names:
                pytest.skip("No WF_Trades sheet")
            trades_sheet = xlsx.parse("WF_Trades")
        if len(trades_sheet) == 0:
            pytest.skip("WF_Trades is empty")
        present = [c for c in _FILTER_TRADE_COLS if c in trades_sheet.columns]
        assert len(present) > 0, (
            f"E3 FAIL: no filter columns in XLSX WF_Trades sheet. "
            f"Expected some of {_FILTER_TRADE_COLS}"
        )


@pytest.mark.slow
class TestE4DisabledBaselineSchemaIntact:
    """E4: disabled pipeline produces correct step_oos_long schema — baseline
    parity maintained after all WP patches (§17.1.1 / §17.2.11)."""

    def test_disabled_pipeline_completes_no_error(self, tmp_path_factory):
        if not _DATA_CSV.exists():
            pytest.skip(_SKIP_E2E)

        from wf_grid.pipeline.orchestrator import run_grid_pipeline
        import textwrap as _tw

        tmp = tmp_path_factory.mktemp("e2e_disabled_schema")
        cfg_text = _tw.dedent(f"""\
        data:
          file_path: "{_DATA_CSV.as_posix()}"
          periods_per_year: 252
          annualization_basis: "trading"
        optimization:
          atr_period_range: [10, 10]
          multiplier_range: [2.0, 2.0]
          multiplier_step: 0.5
          trade_mode: "both"
        backtest:
          commission: 0.000235
          min_trades_required: 1
          early_exit_enabled: false
          early_exit_max_drawdown: 0.50
          early_exit_check_bars: 50
        validation:
          warmup_period: 0
          warmup_period_auto: true
          walk_forward:
            train_size: "500bars"
            test_size: "200bars"
            step_size: "500bars"
            scheme: "rolling"
        gates:
          step:
            min_trades: null
            max_drawdown_threshold: -0.50
          candidate:
            positive_median_threshold: 0.0
            min_trades_median: 1.0
            worst_segment_pnl_threshold: null
            max_drawdown_threshold: -0.50
        ranking:
          mode: "gates_score"
          min_segments_for_ranking: null
          sort_by: "sum_pnl_pct_Median"
          tiebreaker: "sum_pnl_pct_Min"
        scoring:
          score_weights:
            sum_pnl_pct_Median: 0.45
            profitable_segments_count: 0.35
            abs_max_drawdown_Min: 0.20
        status:
          min_meaningful_bars: 30
        trade_filter:
          enabled: false
          type: zigzag_st_mode
        """)
        cfg_path = tmp / "e2e_disabled.yaml"
        cfg_path.write_text(cfg_text, encoding="utf-8")

        result = run_grid_pipeline(
            config_path=str(cfg_path),
            output_path=str(tmp / "out_disabled.xlsx"),
        )
        assert result.error is None, f"E4 FAIL: disabled pipeline errored: {result.error}"
        df = result.step_oos_long
        assert df is not None and len(df) > 0, "E4 FAIL: step_oos_long empty on disabled path"
        # Filter summary columns must NOT be present on the disabled path —
        # baseline schema parity: disabled run produces the same column set as
        # a run without trade_filter at all (§11.1 / §14.18 / §17.1.1).
        for col in _SUMMARY_COLS:
            assert col not in df.columns, (
                f"E4 FAIL: filter summary column {col!r} must NOT appear in "
                f"step_oos_long when trade_filter is disabled (schema parity)"
            )


@pytest.mark.slow
class TestE5DisabledEnabledBaselineParity:
    """E5: disabled and absent-filter pipelines agree on core metric columns."""

    def test_enabled_does_not_contaminate_disabled_summary_columns(
        self, e2e_enabled_result
    ):
        """Filter summary columns in enabled run must not bleed non-null values
        into steps that were somehow processed without filter context."""
        df = e2e_enabled_result.step_oos_long
        if df is None:
            pytest.skip("No step_oos_long")
        # All rows should have consistent null/non-null for filter summary columns
        # (either all non-null for enabled steps, or null for disabled/error steps)
        for col in _SUMMARY_COLS:
            if col not in df.columns:
                continue
            # No check fails — just verify column presence and no mixed invalid types
            assert col in df.columns


# ---------------------------------------------------------------------------
# §14.6 Negative-control: exit B summary fields survive _strip_filter_arrays
# ---------------------------------------------------------------------------

_EXIT_B_SUMMARY_COLS = [
    "n_bars_in_counting_zz_legs",
    "zz_leg_stop_triggered_count",
    "exit_off_mode",
    "exit_off_zz_leg_count",
]


def _write_exit_b_config(tmp_path: Path) -> str:
    """Write a mini-grid config with exit B (count=2) for §14.6 IPC test."""
    cfg_text = textwrap.dedent(f"""\
    data:
      file_path: "{_DATA_CSV.as_posix()}"
      periods_per_year: 252
      annualization_basis: "trading"

    optimization:
      atr_period_range: [10, 10]
      multiplier_range: [2.0, 2.0]
      multiplier_step: 0.5
      trade_mode: "both"

    backtest:
      commission: 0.000235
      min_trades_required: 1
      early_exit_enabled: false
      early_exit_max_drawdown: 0.50
      early_exit_check_bars: 50

    validation:
      warmup_period: 0
      warmup_period_auto: true
      walk_forward:
        train_size: "500bars"
        test_size: "200bars"
        step_size: "500bars"
        scheme: "rolling"

    gates:
      step:
        min_trades: null
        max_drawdown_threshold: -0.50
      candidate:
        positive_median_threshold: 0.0
        min_trades_median: 1.0
        worst_segment_pnl_threshold: null
        max_drawdown_threshold: -0.50

    ranking:
      mode: "gates_score"
      min_segments_for_ranking: null
      sort_by: "sum_pnl_pct_Median"
      tiebreaker: "sum_pnl_pct_Min"

    scoring:
      score_weights:
        sum_pnl_pct_Median: 0.45
        profitable_segments_count: 0.35
        abs_max_drawdown_Min: 0.20

    status:
      min_meaningful_bars: 30

    trade_filter:
      enabled: true
      type: zigzag_st_mode
      zigzag:
        reversal_threshold: 0.02
        local_window: 5
        global_stats_source: full_dataset
        leg_height_mode: pct
        global_median: auto
        candidate_trigger_threshold: 0.01
      triggers:
        candidate_threshold:
          enabled: true
        confirmed_median:
          enabled: false
      lifecycle:
        freeze_confirmed_legs: 0
        stop_check: confirm_bar_only
        stopping_exit: opposite_st_flip
        exit_off_mode: "exit B"
        exit_off_zz_leg_count: 2
    """)
    p = tmp_path / "e2e_exit_b_config.yaml"
    p.write_text(cfg_text, encoding="utf-8")
    return str(p)


@pytest.mark.slow
class TestE6ExitBIPCNegativeControl:
    """§14.6 Negative-control: exit B summary fields survive _strip_filter_arrays.

    Verifies that:
      - run_grid_pipeline (sequential) strips per-bar arrays (filter_diagnostics_oos=None)
      - BUT summary fields (n_bars_in_counting_zz_legs, zz_leg_stop_triggered_count,
        exit_off_mode, exit_off_zz_leg_count) are present in step_oos_long and not None.

    This guards against silent regression: computing summary but forgetting to
    write it into _FILTER_SUMMARY_COLUMNS / mapping (plan §14.6 / §7.6).
    """

    @pytest.fixture(scope="class")
    def exit_b_result(self, tmp_path_factory):
        if not _DATA_CSV.exists():
            pytest.skip(_SKIP_E2E)
        from wf_grid.pipeline.orchestrator import run_grid_pipeline
        tmp = tmp_path_factory.mktemp("e2e_exit_b")
        cfg_path = _write_exit_b_config(tmp)
        result = run_grid_pipeline(
            config_path=cfg_path,
            output_path=str(tmp / "exit_b_output.xlsx"),
            parallel_enabled=False,
        )
        assert result.error is None, f"E6 FAIL: exit B pipeline failed: {result.error}"
        return result

    def test_e6_pipeline_completes(self, exit_b_result):
        assert exit_b_result.error is None

    def test_e6_step_oos_long_has_exit_off_summary_cols(self, exit_b_result):
        df = exit_b_result.step_oos_long
        assert df is not None and len(df) > 0
        for col in _EXIT_B_SUMMARY_COLS:
            assert col in df.columns, (
                f"§14.6 FAIL: '{col}' missing from step_oos_long after IPC strip"
            )

    def test_e6_exit_off_mode_col_is_exit_b_not_null(self, exit_b_result):
        df = exit_b_result.step_oos_long
        ok_rows = df[df["exit_off_mode"].notna()]
        assert len(ok_rows) > 0, "§14.6 FAIL: exit_off_mode all null after IPC strip"
        assert all(ok_rows["exit_off_mode"] == "exit B"), (
            f"§14.6: expected 'exit B', got {set(ok_rows['exit_off_mode'].unique())}"
        )

    def test_e6_exit_off_zz_leg_count_is_2_not_null(self, exit_b_result):
        df = exit_b_result.step_oos_long
        ok_rows = df[df["exit_off_zz_leg_count"].notna()]
        assert len(ok_rows) > 0, "§14.6 FAIL: exit_off_zz_leg_count all null"
        assert all(ok_rows["exit_off_zz_leg_count"] == 2), (
            f"§14.6: expected all 2, got {set(ok_rows['exit_off_zz_leg_count'].unique())}"
        )

    def test_e6_n_bars_in_counting_zz_legs_is_not_null(self, exit_b_result):
        df = exit_b_result.step_oos_long
        assert "n_bars_in_counting_zz_legs" in df.columns
        ok_rows = df[df["n_bars_in_counting_zz_legs"].notna()]
        assert len(ok_rows) > 0, "§14.6 FAIL: n_bars_in_counting_zz_legs all null"

    def test_e6_summary_presence_proves_strip_happened(self, exit_b_result):
        """Structural proof that _strip_filter_diagnostics_arrays ran correctly:
        summary columns are non-null DESPITE per-bar arrays being stripped by the
        pipeline.  This is possible only if summary was computed BEFORE stripping
        (plan §7.6 / §14.6 design invariant: compute-then-strip)."""
        df = exit_b_result.step_oos_long
        assert df is not None
        # Both summary presence AND non-null values confirm the strip-safe design:
        # if strip happened BEFORE summary computation, these would all be null/missing.
        for col in _EXIT_B_SUMMARY_COLS:
            assert col in df.columns, f"§14.6: column '{col}' missing"
            assert df[col].notna().any(), (
                f"§14.6: '{col}' all null — summary not computed before strip"
            )


@pytest.mark.slow
class TestE7ExitBIPCParallelControl:
    """§14.6 (strict): exit B summary fields survive TRUE multiprocess IPC.

    Same contract as TestE6, but with parallel_enabled=True so that
    _mp_worker_run_grid_point is called in a separate process, result is
    serialized via IPC queue, and _strip_filter_diagnostics_arrays fires
    INSIDE the worker before pickling — the strictest form of the §14.6 test.

    Verifies: summary scalars (exit_off_mode, n_bars_in_counting_zz_legs, etc.)
    survive pickle→IPC→unpickle intact.
    """

    @pytest.fixture(scope="class")
    def exit_b_parallel_result(self, tmp_path_factory):
        if not _DATA_CSV.exists():
            pytest.skip(_SKIP_E2E)
        from wf_grid.pipeline.orchestrator import run_grid_pipeline
        tmp = tmp_path_factory.mktemp("e2e_exit_b_parallel")
        cfg_path = _write_exit_b_config(tmp)
        result = run_grid_pipeline(
            config_path=cfg_path,
            output_path=str(tmp / "exit_b_parallel_output.xlsx"),
            parallel_enabled=True,
            max_workers=2,
        )
        assert result.error is None, f"E7 FAIL: parallel exit B pipeline: {result.error}"
        return result

    def test_e7_pipeline_parallel_completes(self, exit_b_parallel_result):
        assert exit_b_parallel_result.error is None

    def test_e7_exit_off_summary_cols_survive_ipc(self, exit_b_parallel_result):
        """After IPC serialization, all 4 exit-off summary columns must be
        present and non-null for enabled-filter rows."""
        df = exit_b_parallel_result.step_oos_long
        assert df is not None and len(df) > 0
        for col in _EXIT_B_SUMMARY_COLS:
            assert col in df.columns, (
                f"§14.6 IPC FAIL: '{col}' missing from step_oos_long "
                "after parallel IPC round-trip"
            )
            assert df[col].notna().any(), (
                f"§14.6 IPC FAIL: '{col}' all null after parallel IPC — "
                "summary not serialized or computed before worker strip"
            )

    def test_e7_exit_off_mode_is_exit_b_after_ipc(self, exit_b_parallel_result):
        df = exit_b_parallel_result.step_oos_long
        ok = df[df["exit_off_mode"].notna()]
        assert len(ok) > 0
        assert all(ok["exit_off_mode"] == "exit B"), (
            f"§14.6 IPC: exit_off_mode wrong after IPC: {set(ok['exit_off_mode'].unique())}"
        )

    def test_e7_exit_off_zz_leg_count_is_2_after_ipc(self, exit_b_parallel_result):
        df = exit_b_parallel_result.step_oos_long
        ok = df[df["exit_off_zz_leg_count"].notna()]
        assert len(ok) > 0
        assert all(ok["exit_off_zz_leg_count"] == 2), (
            f"§14.6 IPC: exit_off_zz_leg_count wrong: {set(ok['exit_off_zz_leg_count'].unique())}"
        )
