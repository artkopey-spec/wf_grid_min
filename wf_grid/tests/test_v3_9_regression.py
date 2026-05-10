"""
WP-V3-9 вЂ” Final regression / anti-drift tests.

Pin v3 backward-compat invariants explicitly:

  R1 (disabled parity)
  --------------------
  Disabled filter path produces no v3 diagnostic arrays and no v3 XLSX
  columns.  ``_compute_filter_diagnostics_summary(None)`` returns ``None``.
  ``_build_filters_summary_df`` returns ``None`` for all-disabled periods.

  R2 (Mode A / B / A+B parity: legacy triggers в†” explicit mode)
  --------------------------------------------------------------
  ``resolve_zigzag_mode(None, triggers_cfg)`` maps legacy triggers to the
  expected mode string (A / B / A+B).  Running ``apply()`` with the
  *resolved* mode (simulating post-loader state when no explicit mode key
  was present in YAML) produces bit-identical positions, state, trigger-
  source, and filter_block_reason to running ``apply()`` with the
  *explicit* mode string.  An extra sub-test proves that explicit mode
  overrides triggers (dispatcher uses stats.zigzag_mode, not triggers).

  R3 (Mode B + duration gate parity)
  -----------------------------------
  Pure Mode B with ``candidate_duration_gate.enabled = True`` is bit-
  identical (positions, state, trigger_source, filter_block_reason) to
  Mode B without gate, modulo allowed metadata / config_snapshot / one-
  shot INFO log differences.  РўР— v3 В§5.4: "pure Mode B ignores the gate
  in runtime decisions".

  R4 (FSM enum stability)
  -----------------------
  ``ZigZagFSMState`` enum values (codes 0-5) are unchanged after v3.
  Required because legacy diagnostics export ``trade_filter_state_code``.

  R5 (ZigZagPerBar backward-compat)
  ----------------------------------
  Legacy ``ZigZagPerBar`` construction without v3 fields still works;
  ``apply()`` supplies UNKNOWN defaults when the optional fields are None.

Anti-drift contract (do not change without a failing regression):
  - FSM logic, dispatcher, per-bar arrays, XLSX schema, config schema
    are all frozen unless an existing test breaks first.

Spec reference: РўР— v3 В§5.4 (pure B ignores gate), В§6 (per-bar state),
                В§9 (FSM ordering), В§11 (XLSX export).
"""
from __future__ import annotations

from typing import Dict, Optional
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagFSMState,
    ZigZagPerBar,
    apply,
)

# Reuse the established FSM test scaffolding so our parity comparisons
# share the exact same code path as the WP-V3-5 regression tests.
from wf_grid.tests.test_wp5_zigzag_fsm import (
    _make_filter_cfg,
    _make_global_stats,
    _make_per_bar,
    _run_v3,
    _FilterCfgDouble,
    _LifecycleDouble,
    _ToggleDouble,
    _TriggersDouble,
)


# ===========================================================================
# R1 вЂ” Disabled filter path: no v3 diagnostics, no v3 XLSX columns
# ===========================================================================

class TestV39DisabledPathParity:
    """R1: disabled filter path emits no v3 diagnostics / no v3 XLSX columns."""

    def test_r1_summary_none_when_diagnostics_none(self):
        """``_compute_filter_diagnostics_summary(None)`` в†’ ``None``."""
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        assert _compute_filter_diagnostics_summary(None) is None

    def test_r1_filters_summary_none_when_all_periods_disabled(self):
        """``_build_filters_summary_df`` returns ``None`` if every period has
        ``filter_diagnostics_summary is None``."""
        from supertrend_optimizer.io.excel_tester import _build_filters_summary_df

        prs = []
        for label in ("100%", "75%", "50%"):
            pr = MagicMock()
            pr.period_label = label
            pr.filter_diagnostics_summary = None
            prs.append(pr)
        assert _build_filters_summary_df(prs) is None

    def test_r1_trigger_events_empty_when_diagnostics_none(self):
        """``_build_zigzag_trigger_events_df(None)`` returns an empty
        DataFrame whose schema is the canonical column list вЂ” disabled
        runs do not introduce any new columns."""
        from supertrend_optimizer.io.excel_tester import (
            _build_zigzag_trigger_events_df,
            _TRIGGER_EVENTS_COLUMNS,
        )
        df = _build_zigzag_trigger_events_df(None)
        assert len(df) == 0
        assert list(df.columns) == list(_TRIGGER_EVENTS_COLUMNS)

    def test_r1_filter_diagnostics_100_skipped_when_disabled(self):
        """``_write_filter_diagnostics_100_sheet(writer, None)`` adds no sheet."""
        import io
        import openpyxl
        from supertrend_optimizer.io.excel_tester import (
            _write_filter_diagnostics_100_sheet,
        )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="sentinel", index=False)
            _write_filter_diagnostics_100_sheet(writer, None)
        buf.seek(0)
        wb = openpyxl.load_workbook(buf)
        assert "FilterDiagnostics_100" not in wb.sheetnames


# ===========================================================================
# R2 вЂ” Mode A / B / A+B parity: resolve_zigzag_mode(legacy triggers)
#       must give the same positions / FSM trace as explicit mode="X"
# ===========================================================================

class TestV39ModeABParity:
    """R2: legacy triggers and explicit mode are parity-equivalent inputs.

    This exercises two different config representations: a legacy config with
    ``zigzag.mode`` absent and ``triggers`` present, and a canonical v3 config
    with explicit ``zigzag.mode`` and no legacy triggers. The legacy config is
    normalized by the shared post-validation resolver before both paths enter
    the same downstream ``apply()`` pipeline.
    """
    def _make_inputs(self, a_enabled: bool, b_enabled: bool) -> dict:
        """12-bar scenario that exercises both A and B trigger paths."""
        n = 12
        cand_h = np.zeros(n, dtype=np.float64)
        cand_h[2] = 0.10
        cand_h[7] = 0.10
        confirm = np.zeros(n, dtype=np.int8)
        confirm[5] = 1
        confirm[10] = 1
        local_med = np.full(n, np.nan, dtype=np.float64)
        local_med[5] = 0.04
        local_med[10] = 0.04
        local_med_avail = np.zeros(n, dtype=bool)
        local_med_avail[5] = True
        local_med_avail[10] = True
        cand_age = np.array([1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3], dtype=np.int64)
        cand_dir = np.array([1, 1, 1, -1, -1, -1, 1, 1, 1, -1, -1, -1], dtype=np.int8)
        cfg = _FilterCfgDouble(
            triggers=_TriggersDouble(
                candidate_threshold=_ToggleDouble(enabled=a_enabled),
                confirmed_median=_ToggleDouble(enabled=b_enabled),
            ),
            lifecycle=_LifecycleDouble(freeze_confirmed_legs=2),
        )
        return dict(
            n=n,
            cand_height=cand_h,
            cand_age=cand_age,
            cand_dir=cand_dir,
            confirm_event=confirm,
            local_median_N=local_med,
            local_median_available=local_med_avail,
            cfg=cfg,
        )

    def _assert_parity(
        self,
        a_enabled: bool,
        b_enabled: bool,
        expected_mode: str,
    ) -> None:
        from supertrend_optimizer.core.trade_filter_config import (
            TradeFilterCandidateDurationGateConfig,
            TradeFilterConfig,
            TradeFilterLifecycleConfig,
            TradeFilterTriggerToggleConfig,
            TradeFilterTriggersConfig,
            TradeFilterZigZagConfig,
            resolve_trade_filter_mode_in_place,
        )

        inputs = self._make_inputs(a_enabled, b_enabled)
        legacy_cfg = TradeFilterConfig(
            enabled=True,
            type="zigzag_st_mode",
            zigzag=TradeFilterZigZagConfig(
                enabled=True,
                reversal_threshold=0.01,
                candidate_trigger_threshold=0.05,
                mode=None,
                candidate_duration_gate=TradeFilterCandidateDurationGateConfig(
                    enabled=False,
                    max_bars=None,
                ),
            ),
            triggers=TradeFilterTriggersConfig(
                candidate_threshold=TradeFilterTriggerToggleConfig(enabled=a_enabled),
                confirmed_median=TradeFilterTriggerToggleConfig(enabled=b_enabled),
            ),
            lifecycle=TradeFilterLifecycleConfig(freeze_confirmed_legs=2),
        )
        explicit_cfg = TradeFilterConfig(
            enabled=True,
            type="zigzag_st_mode",
            zigzag=TradeFilterZigZagConfig(
                enabled=True,
                reversal_threshold=0.01,
                candidate_trigger_threshold=0.05,
                mode=expected_mode,
                candidate_duration_gate=TradeFilterCandidateDurationGateConfig(
                    enabled=False,
                    max_bars=None,
                ),
            ),
            triggers=None,
            lifecycle=TradeFilterLifecycleConfig(freeze_confirmed_legs=2),
        )

        assert legacy_cfg.zigzag.mode is None
        assert explicit_cfg.zigzag.mode == expected_mode
        resolve_trade_filter_mode_in_place(
            legacy_cfg,
            frozenset({("trade_filter", "triggers")}),
        )
        assert legacy_cfg.zigzag.mode == expected_mode

        per_bar = _make_per_bar(
            n=inputs["n"],
            candidate_height_pct=inputs["cand_height"],
            candidate_age_bars=inputs["cand_age"],
            candidate_leg_direction=inputs["cand_dir"],
            confirm_event=inputs["confirm_event"],
            local_median_N=inputs["local_median_N"],
            local_median_available=inputs["local_median_available"],
        )
        trend = np.zeros(inputs["n"], dtype=np.int64)
        legacy_run = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=legacy_cfg,
            zigzag_global_stats=_make_global_stats(zigzag_mode=legacy_cfg.zigzag.mode),
            per_bar=per_bar,
        )
        explicit_run = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=explicit_cfg,
            zigzag_global_stats=_make_global_stats(zigzag_mode=explicit_cfg.zigzag.mode),
            per_bar=per_bar,
        )

        np.testing.assert_array_equal(
            legacy_run.positions,
            explicit_run.positions,
            err_msg=f"{expected_mode} parity: positions differ",
        )
        for key in (
            "trade_filter_state",
            "trade_filter_trigger_source",
            "filter_block_reason",
        ):
            np.testing.assert_array_equal(
                legacy_run.filter_diagnostics[key],
                explicit_run.filter_diagnostics[key],
                err_msg=f"{expected_mode} parity: {key} differs",
            )
    def test_r2_mode_a_legacy_to_explicit(self):
        """candidate_threshold-only triggers в†’ resolves to 'A' в†’ same as explicit mode='A'."""
        self._assert_parity(a_enabled=True, b_enabled=False, expected_mode="A")

    def test_r2_mode_b_legacy_to_explicit(self):
        """confirmed_median-only triggers в†’ resolves to 'B' в†’ same as explicit mode='B'."""
        self._assert_parity(a_enabled=False, b_enabled=True, expected_mode="B")

    def test_r2_mode_ab_legacy_to_explicit(self):
        """both triggers в†’ resolves to 'A+B' в†’ same as explicit mode='A+B'."""
        self._assert_parity(a_enabled=True, b_enabled=True, expected_mode="A+B")

    def test_r2_no_triggers_resolves_to_a(self):
        """No triggers_cfg (None) в†’ default 'A'."""
        from supertrend_optimizer.core.trade_filter_config import resolve_zigzag_mode
        assert resolve_zigzag_mode(None, None) == "A"

    def test_r2_runner_lazy_stats_normalizes_programmatic_no_triggers(self, monkeypatch):
        """run_all_periods lazy stats path must not infer A+B from default toggles."""
        from supertrend_optimizer.core.trade_filter_config import (
            TradeFilterConfig,
            TradeFilterLifecycleConfig,
            TradeFilterZigZagConfig,
        )
        from supertrend_optimizer.testing import runner

        cfg = TradeFilterConfig(
            enabled=True,
            type="zigzag_st_mode",
            zigzag=TradeFilterZigZagConfig(
                enabled=True,
                reversal_threshold=0.01,
                candidate_trigger_threshold=0.05,
                mode=None,
            ),
            lifecycle=TradeFilterLifecycleConfig(),
        )
        seen_modes = []

        def fake_build_zigzag_global_stats(close, trade_filter_config):
            seen_modes.append(trade_filter_config.zigzag.mode)
            return MagicMock()

        def fake_run_period(**kwargs):
            return runner.PeriodResult(
                period_label="",
                n_bars=len(kwargs["df"]),
                result=MagicMock(),
                filter_diagnostics=None,
                filter_diagnostics_summary=None,
            )

        monkeypatch.setattr(runner, "build_zigzag_global_stats", fake_build_zigzag_global_stats)
        monkeypatch.setattr(runner, "run_period", fake_run_period)
        df = pd.DataFrame({
            "open": np.arange(10, dtype=float),
            "high": np.arange(10, dtype=float) + 1,
            "low": np.arange(10, dtype=float) - 1,
            "close": np.arange(10, dtype=float),
        })

        runner.run_all_periods(
            df=df,
            atr_period=3,
            multiplier=2.0,
            trade_mode="both",
            commission=0.0,
            trade_filter_config=cfg,
            zigzag_global_stats=None,
        )

        assert seen_modes == ["A"]

    def test_r2_explicit_mode_wins_over_triggers(self):
        """Explicit mode='A' must override triggers that would resolve to A+B.

        This is the key dispatcher anti-drift proof: the FSM uses
        ``zigzag_global_stats.zigzag_mode`` (from the resolver / explicit
        config), NOT the raw triggers flags.  In Mode A, ``confirmed_median``
        must never appear in ``trade_filter_trigger_source``.

        Scenario design: we give Mode B a clear opportunity to fire while
        the FSM is in OFF (confirmed_median fires at bar 2, cand_h is 0 so
        no A component; FSM starts OFF with no prior lifecycle).  In Mode A,
        bar 2 must stay OFF.  In Mode A+B, bar 2 must emit trigger_source ==
        'confirmed_median'.
        """
        from supertrend_optimizer.core.trade_filter_config import resolve_zigzag_mode

        # Verify: explicit mode='A' overrides A+B triggers
        triggers_cfg = _FilterCfgDouble(
            triggers=_TriggersDouble(
                candidate_threshold=_ToggleDouble(enabled=True),
                confirmed_median=_ToggleDouble(enabled=True),
            ),
        ).triggers
        assert resolve_zigzag_mode("A", triggers_cfg) == "A", (
            "Explicit mode 'A' must override A+B triggers"
        )

        # Scenario: B fires at bar 2 from OFF (cand_h=0 в†’ no A component).
        # B trigger condition: local_median_N[t] >= global_median (0.05).
        # At bar 6: cand_h clears threshold в†’ A component true, but
        #           lifecycle already running from bar 2 in A+B; in Mode A:
        #           bar 6 fires the sole trigger.
        n = 10
        cand_h = np.zeros(n, dtype=np.float64)
        cand_h[6] = 0.10    # A component fires only here
        confirm = np.zeros(n, dtype=np.int8)
        confirm[2] = 1      # B component fires here (FSM in OFF)
        confirm[8] = 1
        local_med = np.full(n, np.nan, dtype=np.float64)
        local_med[2] = 0.07   # >= global_median(0.05) в†’ B confirms
        local_med[8] = 0.07
        local_med_avail = np.zeros(n, dtype=bool)
        local_med_avail[2] = True
        local_med_avail[8] = True
        cand_age = np.full(n, 3, dtype=np.int64)
        cand_dir = np.full(n, 1, dtype=np.int8)
        cfg = _FilterCfgDouble(
            triggers=_TriggersDouble(
                candidate_threshold=_ToggleDouble(enabled=True),
                confirmed_median=_ToggleDouble(enabled=True),
            ),
            lifecycle=_LifecycleDouble(freeze_confirmed_legs=2),
        )

        run_a = _run_v3(
            n=n, cand_height=cand_h, cand_age=cand_age, cand_dir=cand_dir,
            confirm_event=confirm, local_median_N=local_med,
            local_median_available=local_med_avail, cfg=cfg,
            zigzag_mode="A", gate_enabled=False,
        )
        run_ab = _run_v3(
            n=n, cand_height=cand_h, cand_age=cand_age, cand_dir=cand_dir,
            confirm_event=confirm, local_median_N=local_med,
            local_median_available=local_med_avail, cfg=cfg,
            zigzag_mode="A+B", gate_enabled=False,
        )

        ts_a = run_a.filter_diagnostics["trade_filter_trigger_source"]
        ts_ab = run_ab.filter_diagnostics["trade_filter_trigger_source"]

        # Mode A dispatcher: confirmed_median must NEVER appear
        assert "confirmed_median" not in ts_a, (
            "Mode A must not emit confirmed_median in trigger_source"
        )
        assert "both" not in ts_a, (
            "Mode A must not emit 'both' in trigger_source"
        )

        # Mode A+B: bar 2 must have triggered via confirmed_median (FSM was in OFF)
        assert str(ts_ab[2]) == "confirmed_median", (
            f"Mode A+B bar 2: expected 'confirmed_median', got {ts_ab[2]!r}"
        )


# ===========================================================================
# R3 вЂ” Pure Mode B with duration gate enabled = Mode B without gate
# ===========================================================================

class TestV39ModeBGateParity:
    """R3: pure Mode B with gate enabled = Mode B without gate (positions,
    state, trigger_source, filter_block_reason).  РўР— v3 В§5.4."""

    def _common(self) -> dict:
        n = 14
        cand_h = np.zeros(n, dtype=np.float64)
        # Heavy candidate height that WOULD trigger duration gate for Mode A,
        # but Mode B ignores the gate by spec.
        cand_h[3] = 0.10
        cand_h[8] = 0.10
        confirm = np.zeros(n, dtype=np.int8)
        confirm[3] = 1   # B trigger fires here
        confirm[8] = 1
        confirm[12] = 1
        local_med = np.full(n, np.nan, dtype=np.float64)
        local_med[3] = 0.03
        local_med[8] = 0.03
        local_med[12] = 0.03
        local_med_avail = np.zeros(n, dtype=bool)
        local_med_avail[3] = True
        local_med_avail[8] = True
        local_med_avail[12] = True
        # Candidate age beyond max_bars=2 в†’ would FAIL duration gate
        cand_age = np.full(n, 5, dtype=np.int64)
        cand_dir = np.full(n, 1, dtype=np.int8)
        cfg = _make_filter_cfg(a_enabled=False, b_enabled=True, freeze_confirmed_legs=2)
        return {
            "n": n,
            "cand_height": cand_h,
            "cand_age": cand_age,
            "cand_dir": cand_dir,
            "confirm_event": confirm,
            "local_median_N": local_med,
            "local_median_available": local_med_avail,
            "cfg": cfg,
            "zigzag_mode": "B",
        }

    def test_r3_positions_bit_identical(self):
        common = self._common()
        r_no = _run_v3(**common, gate_enabled=False)
        r_yes = _run_v3(**common, gate_enabled=True, gate_max_bars=2)
        np.testing.assert_array_equal(r_no.positions, r_yes.positions)

    def test_r3_state_trace_bit_identical(self):
        common = self._common()
        r_no = _run_v3(**common, gate_enabled=False)
        r_yes = _run_v3(**common, gate_enabled=True, gate_max_bars=2)
        np.testing.assert_array_equal(
            r_no.filter_diagnostics["trade_filter_state"],
            r_yes.filter_diagnostics["trade_filter_state"],
        )

    def test_r3_trigger_source_bit_identical(self):
        common = self._common()
        r_no = _run_v3(**common, gate_enabled=False)
        r_yes = _run_v3(**common, gate_enabled=True, gate_max_bars=2)
        np.testing.assert_array_equal(
            r_no.filter_diagnostics["trade_filter_trigger_source"],
            r_yes.filter_diagnostics["trade_filter_trigger_source"],
        )

    def test_r3_filter_block_reason_bit_identical(self):
        common = self._common()
        r_no = _run_v3(**common, gate_enabled=False)
        r_yes = _run_v3(**common, gate_enabled=True, gate_max_bars=2)
        np.testing.assert_array_equal(
            r_no.filter_diagnostics["filter_block_reason"],
            r_yes.filter_diagnostics["filter_block_reason"],
        )

    def test_r3_only_metadata_arrays_differ(self):
        """With pure Mode B + enabled gate, the only behaviour-relevant
        primitives (used by FSM dispatcher) must be identical to Mode B
        without gate.  Metadata arrays (gate_enabled / max_bars / passed)
        ARE expected to differ вЂ” verify they are excluded."""
        common = self._common()
        r_no = _run_v3(**common, gate_enabled=False)
        r_yes = _run_v3(**common, gate_enabled=True, gate_max_bars=2)

        # Gate metadata MUST differ (different config)
        assert int(r_no.filter_diagnostics["candidate_duration_gate_enabled"][0]) == 0
        assert int(r_yes.filter_diagnostics["candidate_duration_gate_enabled"][0]) == 1
        assert int(r_no.filter_diagnostics["candidate_duration_max_bars"][0]) == -1
        assert int(r_yes.filter_diagnostics["candidate_duration_max_bars"][0]) == 2

        # Pure Mode B: gate_passed must be 1 everywhere (spec В§5.4)
        assert np.all(r_no.filter_diagnostics["candidate_duration_gate_passed"] == 1)
        assert np.all(r_yes.filter_diagnostics["candidate_duration_gate_passed"] == 1)
        np.testing.assert_array_equal(
            r_no.filter_diagnostics["candidate_component_ok"],
            r_yes.filter_diagnostics["candidate_component_ok"],
        )


# ===========================================================================
# R4 вЂ” FSM enum stability
# ===========================================================================

class TestV39FSMEnumStability:
    """R4: ZigZagFSMState enum codes are pinned across v3 (legacy
    diagnostics export ``trade_filter_state_code`` as int)."""

    def test_r4_off_code_is_zero(self):
        assert int(ZigZagFSMState.OFF) == 0

    def test_r4_wait_code_is_one(self):
        assert int(ZigZagFSMState.WAIT_FIRST_ST_FLIP) == 1

    def test_r4_freeze_code_is_two(self):
        assert int(ZigZagFSMState.ST_ACTIVE_FREEZE) == 2

    def test_r4_monitoring_code_is_three(self):
        assert int(ZigZagFSMState.ST_ACTIVE_MONITORING) == 3

    def test_r4_stopping_code_is_four(self):
        assert int(ZigZagFSMState.ST_STOPPING) == 4

    def test_r4_counting_zz_legs_code_is_five(self):
        assert int(ZigZagFSMState.ST_COUNTING_ZZ_LEGS) == 5

    def test_r4_no_extra_states(self):
        """FSM member set is pinned (anti-drift §9 / §10.7.2)."""
        members = {m.name for m in ZigZagFSMState}
        assert members == {
            "OFF",
            "WAIT_FIRST_ST_FLIP",
            "ST_ACTIVE_FREEZE",
            "ST_ACTIVE_MONITORING",
            "ST_STOPPING",
            "ST_COUNTING_ZZ_LEGS",
        }


# ===========================================================================
# R5 вЂ” ZigZagPerBar backward-compat (default v3 fields = UNKNOWN)
# ===========================================================================

class TestV39ZigZagPerBarBackwardCompat:
    """Pin: ``ZigZagPerBar`` legacy construction (without v3 fields) works
    via the ``Optional`` defaults introduced as a regression fix.  This
    matters for any downstream code that builds ZigZagPerBar directly
    (e.g. ``test_daily_reset.py`` fixtures)."""

    def test_r5_legacy_construction_no_v3_fields(self):
        n = 5
        per_bar = ZigZagPerBar(
            candidate_height_pct=np.full(n, np.nan, dtype=np.float64),
            confirm_event=np.zeros(n, dtype=np.int8),
            confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
            local_median_N=np.full(n, np.nan, dtype=np.float64),
            local_median_available=np.zeros(n, dtype=bool),
        )
        # Defaults must be None (not allocated arrays) вЂ” apply() handles
        # the None case by allocating UNKNOWN arrays at runtime.
        assert per_bar.candidate_age_bars is None
        assert per_bar.candidate_leg_direction is None

    def test_r5_apply_uses_unknown_defaults_when_v3_fields_none(self):
        """apply() with a legacy ZigZagPerBar (no v3 fields) must produce
        candidate_age_bars=-1 and candidate_leg_direction=0 in diagnostics."""
        n = 4
        per_bar = ZigZagPerBar(
            candidate_height_pct=np.full(n, np.nan, dtype=np.float64),
            confirm_event=np.zeros(n, dtype=np.int8),
            confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
            local_median_N=np.full(n, np.nan, dtype=np.float64),
            local_median_available=np.zeros(n, dtype=bool),
        )
        cfg = _make_filter_cfg(a_enabled=True, b_enabled=False, freeze_confirmed_legs=2)
        stats = _make_global_stats()
        result = apply(
            trend=np.zeros(n, dtype=np.int64),
            trade_mode="both",
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            per_bar=per_bar,
        )
        diag = result.filter_diagnostics
        np.testing.assert_array_equal(
            diag["candidate_age_bars"], np.full(n, -1, dtype=np.int64)
        )
        np.testing.assert_array_equal(
            diag["candidate_leg_direction"], np.zeros(n, dtype=np.int8)
        )


