"""
Tests for the ``validate_trade_filter`` ``caller_pipeline`` whitelist
(WP-T3 step 6, plan §5.5).

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §5.5

Contract pinned by these tests:
    * caller_pipeline domain = {"wf_grid", "tester", "optimizer", "single"};
    * any other caller -> error (unknown caller_pipeline);
    * for type="zigzag_st_mode" enabled=true:
        - allowed callers = {"wf_grid", "tester"} -> validation proceeds;
        - rejected callers = {"optimizer", "single"} -> error
          "trade_filter.type='zigzag_st_mode' is not supported in pipeline ...";
    * disabled filter (enabled=false): caller_pipeline whitelist NOT applied
      (filter is a no-op regardless of pipeline);
    * absent trade_filter block / non-zigzag_st_mode types: domain check
      still applies (defensive) but whitelist check skipped.

These tests call ``validate_trade_filter`` directly (the shared validator
in donor/supertrend_optimizer/core/trade_filter_config.py) so they bypass
``load_tester_config`` (which hardcodes caller_pipeline="tester") and
``wf_grid.config.loader._validate_trade_filter`` (hardcodes "wf_grid").
"""

from __future__ import annotations

import pytest

from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    TradeFilterDiagnosticsConfig,
    TradeFilterLifecycleConfig,
    TradeFilterTriggerToggleConfig,
    TradeFilterTriggersConfig,
    TradeFilterZigZagConfig,
    _CALLER_PIPELINE_DOMAIN,
    _ZIGZAG_ST_MODE_ALLOWED_CALLERS,
    validate_trade_filter,
)


def _build_enabled_zigzag_cfg() -> tuple[TradeFilterConfig, frozenset]:
    """Build a fully valid ``enabled=true, type=zigzag_st_mode`` config.

    Mirrors the canonical test factory in
    ``wf_grid/tests/test_wp3_zigzag_global_stats.py`` so the schema is one
    source of truth.  Trigger thresholds live in ``zigzag``; ``triggers``
    only carries the on/off toggles per the WP-T2 schema.
    """
    cfg = TradeFilterConfig(
        enabled=True,
        type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            enabled=True,
            reversal_threshold=0.04,
            candidate_trigger_threshold=0.4,
            candidate_trigger_quantile=None,
            local_window=200,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=5,
            stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
        ),
        diagnostics=TradeFilterDiagnosticsConfig(
            export_state_columns=True,
            export_trigger_columns=True,
        ),
    )
    raw_keys: frozenset[tuple[str, ...]] = frozenset(
        {
            ("trade_filter", "enabled"),
            ("trade_filter", "type"),
            ("trade_filter", "zigzag"),
            ("trade_filter", "zigzag", "reversal_threshold"),
            ("trade_filter", "zigzag", "candidate_trigger_threshold"),
            ("trade_filter", "zigzag", "local_window"),
            ("trade_filter", "triggers"),
            ("trade_filter", "triggers", "candidate_threshold"),
            ("trade_filter", "triggers", "candidate_threshold", "enabled"),
            ("trade_filter", "triggers", "confirmed_median"),
            ("trade_filter", "triggers", "confirmed_median", "enabled"),
            ("trade_filter", "lifecycle"),
            ("trade_filter", "lifecycle", "freeze_confirmed_legs"),
            ("trade_filter", "lifecycle", "stop_check"),
            ("trade_filter", "lifecycle", "stopping_exit"),
            ("trade_filter", "diagnostics"),
            ("trade_filter", "diagnostics", "export_state_columns"),
            ("trade_filter", "diagnostics", "export_trigger_columns"),
        }
    )
    return cfg, raw_keys


def _build_disabled_cfg() -> tuple[TradeFilterConfig, frozenset]:
    """Build a minimal disabled config (enabled=false, no type)."""
    cfg = TradeFilterConfig(
        enabled=False,
        type=None,
        zigzag=None,
        triggers=None,
        lifecycle=None,
        diagnostics=None,
    )
    raw_keys: frozenset[tuple[str, ...]] = frozenset({("trade_filter", "enabled")})
    return cfg, raw_keys


# ---------------------------------------------------------------------------
# Group 1: domain constants
# ---------------------------------------------------------------------------

class TestDomainConstants:
    def test_domain_is_exactly_four(self) -> None:
        assert _CALLER_PIPELINE_DOMAIN == frozenset(
            {"wf_grid", "tester", "optimizer", "single"}
        )

    def test_zigzag_st_mode_whitelist_is_exactly_two(self) -> None:
        assert _ZIGZAG_ST_MODE_ALLOWED_CALLERS == frozenset(
            {"wf_grid", "tester"}
        )

    def test_whitelist_is_subset_of_domain(self) -> None:
        assert _ZIGZAG_ST_MODE_ALLOWED_CALLERS.issubset(
            _CALLER_PIPELINE_DOMAIN
        )


# ---------------------------------------------------------------------------
# Group 2: allowed callers (wf_grid, tester) — enabled+zigzag_st_mode passes
# ---------------------------------------------------------------------------

class TestAllowedCallers:
    @pytest.mark.parametrize("caller", ["wf_grid", "tester"])
    def test_enabled_zigzag_st_mode_accepted(self, caller: str) -> None:
        cfg, raw_keys = _build_enabled_zigzag_cfg()
        errors: list[str] = []
        validate_trade_filter(cfg, errors, raw_keys, caller_pipeline=caller)
        assert errors == [], (
            f"caller_pipeline={caller!r} should accept enabled+zigzag_st_mode "
            f"(allowed by whitelist), got errors: {errors}"
        )


# ---------------------------------------------------------------------------
# Group 3: rejected callers (optimizer, single) — enabled+zigzag_st_mode reject
# ---------------------------------------------------------------------------

class TestRejectedCallers:
    @pytest.mark.parametrize("caller", ["optimizer", "single"])
    def test_enabled_zigzag_st_mode_rejected(self, caller: str) -> None:
        cfg, raw_keys = _build_enabled_zigzag_cfg()
        errors: list[str] = []
        validate_trade_filter(cfg, errors, raw_keys, caller_pipeline=caller)
        assert errors, (
            f"caller_pipeline={caller!r} should reject enabled+zigzag_st_mode "
            "(not in whitelist)"
        )
        joined = " | ".join(errors)
        assert "zigzag_st_mode" in joined
        assert caller in joined or repr(caller) in joined
        assert "wf_grid" in joined and "tester" in joined, (
            "Error message must reference the allowed pipelines"
        )

    def test_optimizer_rejected_short_circuits_downstream_rules(self) -> None:
        """Plan §5.5: pipeline rejection MUST be hard fail; downstream rules
        should not run (would only multiply errors)."""
        cfg, raw_keys = _build_enabled_zigzag_cfg()
        errors: list[str] = []
        validate_trade_filter(
            cfg, errors, raw_keys, caller_pipeline="optimizer"
        )
        # Exactly one error: the pipeline rejection.
        assert len(errors) == 1, (
            f"Expected exactly one error (pipeline rejection); got {errors!r}"
        )


# ---------------------------------------------------------------------------
# Group 4: unknown caller_pipeline — domain check
# ---------------------------------------------------------------------------

class TestUnknownCaller:
    @pytest.mark.parametrize(
        "caller", ["", "WF_GRID", "Tester", "wfgrid", "Optimizer", "future_pipeline"]
    )
    def test_unknown_caller_rejected(self, caller: str) -> None:
        cfg, raw_keys = _build_enabled_zigzag_cfg()
        errors: list[str] = []
        validate_trade_filter(cfg, errors, raw_keys, caller_pipeline=caller)
        assert errors
        joined = " | ".join(errors)
        assert "unknown caller_pipeline" in joined
        # Hard stop — only one error.
        assert len(errors) == 1, (
            "Domain check must short-circuit; got multiple errors"
        )


# ---------------------------------------------------------------------------
# Group 5: disabled filter — whitelist NOT applied
# ---------------------------------------------------------------------------

class TestDisabledNotGated:
    @pytest.mark.parametrize(
        "caller", ["wf_grid", "tester", "optimizer", "single"]
    )
    def test_disabled_filter_accepted_for_all_callers(
        self, caller: str
    ) -> None:
        """A disabled filter is a no-op for every pipeline — never rejected."""
        cfg, raw_keys = _build_disabled_cfg()
        errors: list[str] = []
        validate_trade_filter(cfg, errors, raw_keys, caller_pipeline=caller)
        assert errors == [], (
            f"Disabled filter should not be gated by caller_pipeline; "
            f"caller={caller!r}, errors={errors}"
        )

    def test_disabled_filter_unknown_caller_still_rejected(self) -> None:
        """Domain check applies before disabled-skip; unknown caller still rejects."""
        cfg, raw_keys = _build_disabled_cfg()
        errors: list[str] = []
        validate_trade_filter(
            cfg, errors, raw_keys, caller_pipeline="future_pipeline"
        )
        assert errors
        assert "unknown caller_pipeline" in errors[0]
