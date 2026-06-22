"""
Shared trade-filter dataclasses + validator for Phase 1 (WF Grid) and Phase 2 (Tester).

This module is the SINGLE SOURCE OF TRUTH for the `trade_filter` config block
contract per Appendix A v1.1 §11–§11.3. Both `wf_grid.config` (Phase 1) and
`donor TESTER/.../cli/tester.py` (Phase 2) import dataclasses and the validator
from here.

Rationale (plan §5.1, owner decision v0.5.1 §15 #1, variant (a)):
- One canonical `trade_filter` schema avoids drift between WF Grid and Tester.
- Tester does NOT depend on `wf_grid.config.*` (no transitive cycle).
- `wf_grid.config.schema` and `wf_grid.config.loader` re-export these symbols
  via thin shims (zero-copy); Phase 1 surface is preserved.

Spec reference: Appendix A v1.1 §11, §11.1–§11.3, §15.6, §17.2
Plan reference: Phase 2 implementation plan §5.1, §14 WP-T2 step 0a
v3 spec reference: ТЗ v3 §3, §4, WP-V3-1
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from contextvars import ContextVar
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Lifecycle / type literal whitelists (Appendix A v1.1 §11)
# ---------------------------------------------------------------------------

_LIFECYCLE_STOP_CHECK_VALUES = {"confirm_bar_only"}
_LIFECYCLE_STOPPING_EXIT_VALUES = {"opposite_st_flip"}
_SUPPORTED_TRADE_FILTER_TYPES = {"zigzag_st_mode"}

# v3: valid mode literals (ТЗ v3 §4.1, WP-V3-1 A1/A2)
_VALID_ZIGZAG_MODES = {"A", "B", "C", "D", "A+B", "C+B"}

_V3_INIT_FAILURE_KEYS = frozenset({
    "mode_invalid_literal",
    "mode_conflicts_with_legacy_triggers",
    "candidate_entry_deprecated",
    "duration_gate_enabled_invalid_type",
    "duration_gate_max_bars_missing",
    "duration_gate_max_bars_present_when_disabled",
    "duration_gate_max_bars_invalid_type",
    "duration_gate_max_bars_below_one",
    # exit-off modes (docs/plan_exit_off_modes.txt)
    "exit_off_mode_invalid_literal",
    "exit_off_mode_invalid_type",
    "exit_off_zz_leg_count_missing",
    "exit_off_zz_leg_count_invalid_type",
    "exit_off_zz_leg_count_below_one",
    "exit_off_zz_leg_count_present_when_exit_a",
    # exit_b_immediate_off (docs/Plan exit_b_immediate_off v3.txt §3.3)
    "exit_b_immediate_off_present_when_not_exit_b",
    "exit_b_immediate_off_invalid_type",
    "exit_b_immediate_off_present_when_filter_disabled",
    # time_filter (docs/time_filter_plan_v1_final.txt §1.2)
    "time_filter_enabled_invalid_type",
    "time_filter_window_missing",
    "time_filter_window_invalid_format",
    "time_filter_window_invalid_hours",
    "time_filter_window_invalid_minutes",
    "time_filter_window_zero_length",
    "time_filter_window_cross_midnight",
    "cycle_direction_gate_requires_volume_only",
    # wakeup_regime / Phase 0 Mode D
    "mode_d_unsupported_pipeline",
    "exit_c_unsupported_pipeline",
    "wakeup_regime_unsupported_pipeline",
    "mode_d_requires_exit_c",
    "mode_d_requires_wakeup_enabled",
    "mode_d_candidate_threshold_auto_rejected",
    "mode_d_candidate_quantile_rejected",
    "exit_c_requires_mode_d",
    "exit_c_rejects_exit_off_zz_leg_count",
    "exit_c_rejects_exit_b_immediate_off",
    "exit_c_rejects_legacy_triggers",
    "wakeup_regime_requires_mode_d",
    "wakeup_enabled_requires_mode_d",
    "wakeup_direction_mode_invalid",
    "position_freeze_enabled_invalid_type",
    "position_freeze_enabled_requires_wakeup_enabled",
    "position_freeze_enabled_requires_mode_d",
    "position_freeze_min_hold_bars_invalid",
    "position_freeze_apply_to_invalid",
    "position_freeze_release_action_invalid",
})

# WP-T3 step 6 — caller_pipeline whitelist for type=zigzag_st_mode.
# Plan §5.5 (mode-rejection gate, owner-approved per audit-fix v0.5).
# Domain = {wf_grid, tester, optimizer, single}; whitelist = {wf_grid, tester}.
# Any caller outside the whitelist invoking enabled+zigzag_st_mode is rejected.
_CALLER_PIPELINE_DOMAIN = frozenset({"wf_grid", "tester", "optimizer", "single"})
_ZIGZAG_ST_MODE_ALLOWED_CALLERS = frozenset({"wf_grid", "tester"})
_TF_WINDOW_RE = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TradeFilterTriggerToggleConfig:
    """Single trigger on/off flag (candidate_threshold or confirmed_median)."""
    # Value is intentionally uncoerced from YAML so that non-bool values can
    # be detected by validate_trade_filter and reported as errors.
    enabled: object = True  # expected bool; validation enforces type


@dataclass
class TradeFilterCandidateDurationGateConfig:
    """Optional candidate-leg duration gate (ТЗ v3 §3, §4.2, WP-V3-1).

    When enabled, entry via candidate-component (modes A, C, A+B, C+B) is only
    allowed if candidate_age_bars <= max_bars. Mode B ignores this gate at runtime.

    enabled: expected bool; validation enforces type.
    max_bars: expected int >= 1 when enabled=True; must be absent when enabled=False.
    """
    enabled: object = False   # expected bool; validation enforces type
    max_bars: object = None   # expected int >= 1 when enabled; must be absent when disabled


@dataclass
class TradeFilterTimeFilterConfig:
    """Optional time-of-day trading window filter (docs/time_filter_plan_v1_final.txt).

    ARCHITECTURE NOTE: This block is placed at the root of ``trade_filter``
    (not inside ``zigzag``) because the time window applies to the entire
    trade lifecycle — entries, FSM wipe, ZigZag candidate-state reset — not
    just the ZigZag detection logic. Placing it at the zigzag level would
    create a misleading nesting that suggests ZigZag-only scope.

    When ``enabled=True``, the filter restricts new entries to a half-open
    ``[start, end)`` window defined by ``window`` (format ``HH:MM-HH:MM``).
    On exit from the window a full reset equivalent to ``daily_reset`` is
    performed (FSM wipe, lifecycle reset, ZigZag candidate-state reset).
    Wrap-around windows (``start >= end``) are forbidden in v1.

    ``enabled`` is stored as ``object`` (not coerced from YAML) so that
    non-bool values are detected by ``validate_trade_filter`` — same pattern
    as ``exit_b_immediate_off``.
    """
    enabled: object = False     # expected bool; validation enforces type
    window: Optional[str] = None
    # Populated by resolve_time_filter_in_place after validation.
    # Named with leading underscore to signal "internal / resolver-owned".
    _start_hour: Optional[int] = field(default=None, repr=False)
    _start_minute: Optional[int] = field(default=None, repr=False)
    _end_hour: Optional[int] = field(default=None, repr=False)
    _end_minute: Optional[int] = field(default=None, repr=False)


@dataclass
class TradeFilterZigZagConfig:
    """ZigZag parameters for the trade filter.

    IMPORTANT (plan §6.3): candidate_trigger_threshold and candidate_trigger_quantile
    MUST default to None.  The YAML template shows 0.80 only as an example.
    If either default were a concrete number, raw-key presence tracking (§6.4.1)
    would be unable to distinguish "user supplied the key" from "dataclass default"
    and the numeric-threshold + explicit-quantile reject rule (§11.3) would
    malfunction.

    v3 fields (WP-V3-1):
    - mode: canonical mode literal; None = absent (use legacy triggers or default A).
    - candidate_duration_gate: optional duration gate for candidate-component modes.
    """
    enabled: Optional[bool] = None
    global_stats_source: str = "full_dataset"
    leg_height_mode: str = "pct"
    reversal_threshold: Optional[float] = None          # required when enabled; numeric fraction
    candidate_trigger_threshold: Union[float, str, None] = None  # numeric fraction | "auto" | None
    candidate_trigger_quantile: Optional[float] = None  # MUST stay None; §6.3 invariant
    global_median: str = "auto"
    local_window: int = 5                               # integer >= 1
    daily_reset: bool = False                           # calendar-day reset of ZigZag+FSM (plan v3 §2.1)
    # v3 fields
    mode: Optional[str] = None                          # A | B | C | A+B | C+B; None = absent
    candidate_duration_gate: TradeFilterCandidateDurationGateConfig = field(
        default_factory=TradeFilterCandidateDurationGateConfig
    )


@dataclass
class TradeFilterTriggersConfig:
    """Trigger enable flags for the two activation circuits (A and B)."""
    candidate_threshold: TradeFilterTriggerToggleConfig = field(
        default_factory=TradeFilterTriggerToggleConfig
    )
    confirmed_median: TradeFilterTriggerToggleConfig = field(
        default_factory=lambda: TradeFilterTriggerToggleConfig(enabled=True)
    )


@dataclass
class TradeFilterLifecycleConfig:
    """Lifecycle parameters: freeze window, stop-check mode, stopping exit."""
    freeze_confirmed_legs: int = 5                      # integer >= 0
    stop_check: str = "confirm_bar_only"                # literal; §11 lifecycle enum
    stopping_exit: str = "opposite_st_flip"             # literal; §11 lifecycle enum
    # Exit-off modes: "exit A" (median stop) | "exit B" (ZZ leg count). Stored as object
    # so YAML type mismatches surface in validate_trade_filter.
    exit_off_mode: object = "exit A"
    exit_off_zz_leg_count: object = None                # int >= 1 when exit_off_mode == "exit B"
    # exit_b_immediate_off: immediate OFF on exit B threshold (§3.4).
    # Type object (not bool) — same pattern as exit_off_mode; validator enforces bool.
    exit_b_immediate_off: object = False


@dataclass
class TradeFilterDiagnosticsConfig:
    """Optional diagnostics export flags (§13)."""
    export_state_columns: bool = True
    export_trigger_columns: bool = True


@dataclass
class TradeFilterBaselineSessionConfig:
    enabled: object = False
    window: object = None
    _start_hour: int | None = None
    _start_minute: int | None = None
    _end_hour: int | None = None
    _end_minute: int | None = None


@dataclass
class TradeFilterVolumeConfig:
    enabled: object = None
    mode: object = None
    aggregation: object = "median"
    daily_reset: object = False
    cycle_direction_gate: object = False
    short_window: object = None
    baseline_window: object = None
    threshold_ratio: object = None
    exit_hysteresis_ratio: object = None
    exit_freeze_bars: object = None
    regime_low_ratio: object = None
    regime_high_ratio: object = None
    direction_lookback_bars: object = None
    baseline_session: TradeFilterBaselineSessionConfig = field(
        default_factory=TradeFilterBaselineSessionConfig
    )


@dataclass
class TradeFilterWakeupCandidateHeightConfig:
    enabled: object = False
    quantile: object = None


@dataclass
class TradeFilterWakeupCandidateAgeConfig:
    enabled: object = False
    max_bars: object = None


@dataclass
class TradeFilterWakeupAtrExpansionConfig:
    enabled: object = False
    short_window: object = None
    long_window: object = None
    min_ratio: object = None


@dataclass
class TradeFilterWakeupVolumeExpansionConfig:
    enabled: object = False
    short_window: object = None
    baseline_window: object = None
    min_ratio: object = None


@dataclass
class TradeFilterWakeupEntryConfig:
    candidate_height: TradeFilterWakeupCandidateHeightConfig = field(
        default_factory=TradeFilterWakeupCandidateHeightConfig
    )
    candidate_age: TradeFilterWakeupCandidateAgeConfig = field(
        default_factory=TradeFilterWakeupCandidateAgeConfig
    )
    atr_expansion: TradeFilterWakeupAtrExpansionConfig = field(
        default_factory=TradeFilterWakeupAtrExpansionConfig
    )
    volume_expansion: TradeFilterWakeupVolumeExpansionConfig = field(
        default_factory=TradeFilterWakeupVolumeExpansionConfig
    )
    # Stored under entry for config structure; applied post-FSM to the full position stream.
    direction_mode: object = "normal"


@dataclass
class TradeFilterWakeupTtlExitConfig:
    enabled: object = False
    bars: object = None


@dataclass
class TradeFilterWakeupNoFreshCandidateExitConfig:
    enabled: object = False
    quantile: object = None
    max_age_bars: object = None
    timeout_bars: object = None


@dataclass
class TradeFilterWakeupMaxTradesPerCycleConfig:
    enabled: object = False
    max_trades: object = None


@dataclass
class TradeFilterWakeupCycleTakeProfitExitConfig:
    enabled: object = False
    pnl_pct: object = None


@dataclass
class TradeFilterWakeupLocalMedianStopExitConfig:
    enabled: object = False


@dataclass
class TradeFilterWakeupExitActionConfig:
    mode: object = None


@dataclass
class TradeFilterWakeupExitConfig:
    ttl: TradeFilterWakeupTtlExitConfig = field(
        default_factory=TradeFilterWakeupTtlExitConfig
    )
    no_fresh_candidate: TradeFilterWakeupNoFreshCandidateExitConfig = field(
        default_factory=TradeFilterWakeupNoFreshCandidateExitConfig
    )
    max_trades_per_cycle: TradeFilterWakeupMaxTradesPerCycleConfig = field(
        default_factory=TradeFilterWakeupMaxTradesPerCycleConfig
    )
    cycle_take_profit: TradeFilterWakeupCycleTakeProfitExitConfig = field(
        default_factory=TradeFilterWakeupCycleTakeProfitExitConfig
    )
    local_median_stop: TradeFilterWakeupLocalMedianStopExitConfig = field(
        default_factory=TradeFilterWakeupLocalMedianStopExitConfig
    )
    action: TradeFilterWakeupExitActionConfig = field(
        default_factory=TradeFilterWakeupExitActionConfig
    )


@dataclass
class TradeFilterWakeupPositionFreezeConfig:
    enabled: object = False
    min_hold_bars: object = None
    apply_to: object = None
    release_action: object = None


@dataclass
class TradeFilterWakeupRegimeConfig:
    enabled: object = False
    lock_cycle_direction: object = False
    entry: TradeFilterWakeupEntryConfig = field(
        default_factory=TradeFilterWakeupEntryConfig
    )
    exit: TradeFilterWakeupExitConfig = field(
        default_factory=TradeFilterWakeupExitConfig
    )
    position_freeze: TradeFilterWakeupPositionFreezeConfig = field(
        default_factory=TradeFilterWakeupPositionFreezeConfig
    )


@dataclass
class TradeFilterConfig:
    """Root trade-filter block.

    enabled=None means the YAML key was absent (caught by validation).
    enabled=False means the filter is explicitly disabled (§11.1 baseline).
    enabled=True means the filter is active; all sub-blocks are required.
    """
    # Optional[bool] so that absent key is distinguishable from False via
    # raw_user_keys presence tracking in the loader.
    enabled: Optional[bool] = None
    type: Optional[str] = None
    zigzag: TradeFilterZigZagConfig = field(
        default_factory=TradeFilterZigZagConfig
    )
    triggers: TradeFilterTriggersConfig = field(
        default_factory=TradeFilterTriggersConfig
    )
    lifecycle: TradeFilterLifecycleConfig = field(
        default_factory=TradeFilterLifecycleConfig
    )
    diagnostics: TradeFilterDiagnosticsConfig = field(
        default_factory=TradeFilterDiagnosticsConfig
    )
    # time_filter block (docs/time_filter_plan_v1_final.txt §1.2).
    # Placed at the root of trade_filter — not inside zigzag — because the
    # window applies to the full trade lifecycle (FSM, ZigZag, entries).
    time_filter: TradeFilterTimeFilterConfig = field(
        default_factory=TradeFilterTimeFilterConfig
    )
    volume: Optional[TradeFilterVolumeConfig] = None
    wakeup_regime: Optional[TradeFilterWakeupRegimeConfig] = None
    # Raw YAML presence marker.  ``None`` means "unknown / hand-built config",
    # so runtime consumers fall back to the historical duck-typed behaviour.
    _raw_triggers_present: Optional[bool] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def is_trade_filter_enabled(tf: object) -> bool:
    """Return True only for a post-validation root trade-filter enable."""
    return tf is not None and getattr(tf, "enabled", None) is True


def is_zigzag_enabled(tf: object) -> bool:
    """Return True when the ZigZag subfilter is enabled.

    W2.A materializes the compatibility default in
    ``resolve_zigzag_enabled_in_place`` before validation.
    """
    if not is_trade_filter_enabled(tf):
        return False
    zigzag = getattr(tf, "zigzag", None)
    if zigzag is None:
        return False
    return getattr(zigzag, "enabled", None) is True


def is_volume_enabled(tf: object) -> bool:
    """Return True when the volume subfilter is explicitly enabled."""
    if not is_trade_filter_enabled(tf):
        return False
    volume = getattr(tf, "volume", None)
    return volume is not None and getattr(volume, "enabled", None) is True


def is_wakeup_volume_enabled(tf: object) -> bool:
    """Return True when wakeup volume expansion is explicitly enabled."""
    if not is_trade_filter_enabled(tf):
        return False
    wakeup = getattr(tf, "wakeup_regime", None)
    if wakeup is None or getattr(wakeup, "enabled", None) is not True:
        return False
    entry = getattr(wakeup, "entry", None)
    volume_expansion = getattr(entry, "volume_expansion", None)
    return (
        volume_expansion is not None
        and getattr(volume_expansion, "enabled", None) is True
    )


def resolve_zigzag_enabled_in_place(
    trade_filter_config: Optional["TradeFilterConfig"],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Resolve ``trade_filter.zigzag.enabled`` without breaking legacy configs."""
    if trade_filter_config is None:
        return
    zigzag = getattr(trade_filter_config, "zigzag", None)
    if zigzag is None:
        return
    if ("trade_filter", "zigzag", "enabled") in raw_user_keys:
        return

    legacy_type_marker = _has_legacy_zigzag_marker(trade_filter_config, raw_user_keys)
    volume = getattr(trade_filter_config, "volume", None)
    volume_enabled = volume is not None and getattr(volume, "enabled", None) is True

    if legacy_type_marker:
        zigzag.enabled = True
    elif volume_enabled:
        zigzag.enabled = False
    elif getattr(trade_filter_config, "enabled", None) is True:
        zigzag.enabled = False
    else:
        zigzag.enabled = False


def resolve_volume_enabled_in_place(
    trade_filter_config: Optional["TradeFilterConfig"],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Phase 3 hook: preserve raw ``trade_filter.volume.enabled`` as-is."""
    return


def resolve_volume_defaults_in_place(
    trade_filter_config: Optional["TradeFilterConfig"],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Materialize optional volume defaults after validation."""
    if not is_volume_enabled(trade_filter_config):
        return
    vol = trade_filter_config.volume
    if vol is None:
        return
    if vol.exit_hysteresis_ratio is None:
        vol.exit_hysteresis_ratio = vol.threshold_ratio
    if vol.exit_freeze_bars is None:
        vol.exit_freeze_bars = 0
    if vol.regime_low_ratio is None:
        vol.regime_low_ratio = 0.8
    if vol.regime_high_ratio is None:
        vol.regime_high_ratio = 1.2
    if vol.direction_lookback_bars is None:
        vol.direction_lookback_bars = 3
    if vol.cycle_direction_gate is None:
        vol.cycle_direction_gate = False


# ---------------------------------------------------------------------------
# Strict schema (allowed keys per dotted path) for the trade_filter subtree
#
# Spec reference: Appendix A v1.1 §11; plan Phase 2 §5.3.1
# ---------------------------------------------------------------------------

TRADE_FILTER_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "trade_filter": frozenset({
        "enabled", "type", "zigzag", "triggers", "lifecycle", "diagnostics",
        "time_filter", "volume", "wakeup_regime",
    }),
    "trade_filter.time_filter": frozenset({"enabled", "window"}),
    "trade_filter.zigzag": frozenset({
        "enabled", "global_stats_source", "leg_height_mode", "reversal_threshold",
        "candidate_trigger_threshold", "candidate_trigger_quantile",
        "global_median", "local_window", "daily_reset",
        # v3 fields (WP-V3-1)
        "mode", "candidate_duration_gate",
        # candidate_entry is in the whitelist solely to allow the validator
        # to emit a specific deprecated error instead of a generic unknown-key
        # message (ТЗ v3 §3.1, §4.5 candidate_entry_deprecated, WP-V3-1)
        "candidate_entry",
    }),
    "trade_filter.zigzag.candidate_duration_gate": frozenset({
        "enabled", "max_bars",
    }),
    "trade_filter.triggers": frozenset({"candidate_threshold", "confirmed_median"}),
    "trade_filter.triggers.candidate_threshold": frozenset({"enabled"}),
    "trade_filter.triggers.confirmed_median": frozenset({"enabled"}),
    "trade_filter.lifecycle": frozenset({
        "freeze_confirmed_legs", "stop_check", "stopping_exit",
        "exit_off_mode", "exit_off_zz_leg_count",
        "exit_b_immediate_off",
    }),
    "trade_filter.diagnostics": frozenset({
        "export_state_columns", "export_trigger_columns",
    }),
    "trade_filter.volume": frozenset({
        "enabled", "mode", "short_window", "baseline_window",
        "threshold_ratio", "regime_low_ratio", "regime_high_ratio",
        "direction_lookback_bars", "aggregation", "daily_reset",
        "cycle_direction_gate", "exit_hysteresis_ratio", "exit_freeze_bars",
        "baseline_session",
    }),
    "trade_filter.volume.baseline_session": frozenset({
        "enabled", "window",
    }),
    "trade_filter.wakeup_regime": frozenset({
        "enabled", "lock_cycle_direction", "entry", "exit", "position_freeze",
    }),
    "trade_filter.wakeup_regime.entry": frozenset({
        "candidate_height", "candidate_age", "atr_expansion",
        "volume_expansion", "direction_mode",
    }),
    "trade_filter.wakeup_regime.entry.candidate_height": frozenset({
        "enabled", "quantile",
    }),
    "trade_filter.wakeup_regime.entry.candidate_age": frozenset({
        "enabled", "max_bars",
    }),
    "trade_filter.wakeup_regime.entry.atr_expansion": frozenset({
        "enabled", "short_window", "long_window", "min_ratio",
    }),
    "trade_filter.wakeup_regime.entry.volume_expansion": frozenset({
        "enabled", "short_window", "baseline_window", "min_ratio",
    }),
    "trade_filter.wakeup_regime.exit": frozenset({
        "ttl", "no_fresh_candidate", "max_trades_per_cycle",
        "cycle_take_profit", "local_median_stop", "action",
    }),
    "trade_filter.wakeup_regime.exit.ttl": frozenset({
        "enabled", "bars",
    }),
    "trade_filter.wakeup_regime.exit.no_fresh_candidate": frozenset({
        "enabled", "quantile", "max_age_bars", "timeout_bars",
    }),
    "trade_filter.wakeup_regime.exit.max_trades_per_cycle": frozenset({
        "enabled", "max_trades",
    }),
    "trade_filter.wakeup_regime.exit.cycle_take_profit": frozenset({
        "enabled", "pnl_pct",
    }),
    "trade_filter.wakeup_regime.exit.local_median_stop": frozenset({
        "enabled",
    }),
    "trade_filter.wakeup_regime.exit.action": frozenset({
        "mode",
    }),
    "trade_filter.wakeup_regime.position_freeze": frozenset({
        "enabled", "min_hold_bars", "apply_to", "release_action",
    }),
}


def collect_raw_user_keys(
    raw: dict,
    prefix: tuple[str, ...] = (),
) -> frozenset[tuple[str, ...]]:
    """Return the set of all key-paths (tuples) explicitly present in *raw*.

    Used by callers (Phase 1 loader, Phase 2 tester) to distinguish
    "key absent from YAML" from "key present with None / dataclass-default".
    Required by Appendix A v1.1 §11.3 / plan §6.4.1 (numeric-threshold +
    explicit-quantile reject rule).

    Example::

        raw = {"trade_filter": {"enabled": False, "zigzag": {"local_window": 5}}}
        keys = collect_raw_user_keys(raw)
        # ("trade_filter",)                          in keys -> True
        # ("trade_filter", "enabled")                in keys -> True
        # ("trade_filter", "zigzag", "local_window") in keys -> True
        # ("trade_filter", "zigzag", "candidate_trigger_quantile") in keys -> False
    """
    out: set[tuple[str, ...]] = set()
    for k, v in raw.items():
        path = prefix + (k,)
        out.add(path)
        if isinstance(v, dict):
            out.update(collect_raw_user_keys(v, path))
    return frozenset(out)


def collect_trade_filter_unknown_keys(
    raw: dict,
    path: str = "trade_filter",
) -> list[str]:
    """Return error messages for every unknown key found inside the trade_filter
    subtree of *raw* (recursively).

    Caller wraps with ConfigError if the returned list is non-empty.
    Whitelists are the dotted paths in :data:`TRADE_FILTER_ALLOWED_KEYS`.

    Spec reference: Appendix A v1.1 §11; plan Phase 2 §5.3.1
    """
    allowed = TRADE_FILTER_ALLOWED_KEYS.get(path)
    if allowed is None:
        return []
    errors: list[str] = []
    for key in raw:
        display = f"{path}.{key}"
        if key not in allowed:
            errors.append(f"unknown config key: '{display}'")
        else:
            child = raw[key]
            child_path = f"{path}.{key}"
            if child_path in TRADE_FILTER_ALLOWED_KEYS:
                if not isinstance(child, dict):
                    errors.append(
                        f"{child_path} must be a YAML mapping, "
                        f"got {type(child).__name__!r}"
                    )
                else:
                    errors.extend(
                        collect_trade_filter_unknown_keys(child, child_path)
                    )
    return errors


def build_trade_filter_config_from_raw(tf_raw: dict) -> TradeFilterConfig:
    """Materialise a TradeFilterConfig from the raw YAML sub-dict.

    Values are stored *as-is* from the YAML (no coercion for fields that have
    explicit type-validation rules) so that ``validate_trade_filter`` can
    detect and report the exact type mismatch found in the YAML.

    Sub-blocks that are absent in the YAML are replaced by empty dicts here,
    which causes default-valued sub-config instances to be created. When
    ``enabled=true``, ``validate_trade_filter`` rejects absent sub-blocks via
    raw_user_keys; defaults are fine when ``enabled=false``.
    """
    enabled_raw = tf_raw.get("enabled")  # None for absent
    tf_type = tf_raw.get("type")         # None for absent

    zigzag_raw: dict = tf_raw.get("zigzag") or {}
    cdg_raw = zigzag_raw.get("candidate_duration_gate")
    cdg_raw = cdg_raw if isinstance(cdg_raw, dict) else {}
    candidate_duration_gate = TradeFilterCandidateDurationGateConfig(
        enabled=cdg_raw.get("enabled", False),
        max_bars=cdg_raw.get("max_bars", None),
    )
    zigzag = TradeFilterZigZagConfig(
        enabled=zigzag_raw.get("enabled", None),
        global_stats_source=zigzag_raw.get("global_stats_source", "full_dataset"),
        leg_height_mode=zigzag_raw.get("leg_height_mode", "pct"),
        reversal_threshold=zigzag_raw.get("reversal_threshold", None),
        candidate_trigger_threshold=zigzag_raw.get("candidate_trigger_threshold", None),
        candidate_trigger_quantile=zigzag_raw.get("candidate_trigger_quantile", None),
        global_median=zigzag_raw.get("global_median", "auto"),
        local_window=zigzag_raw.get("local_window", 5),
        daily_reset=zigzag_raw.get("daily_reset", False),
        mode=zigzag_raw.get("mode", None),
        candidate_duration_gate=candidate_duration_gate,
    )

    triggers_raw: dict = tf_raw.get("triggers") or {}
    ct_raw: dict = triggers_raw.get("candidate_threshold") or {}
    cm_raw: dict = triggers_raw.get("confirmed_median") or {}
    triggers = TradeFilterTriggersConfig(
        candidate_threshold=TradeFilterTriggerToggleConfig(
            enabled=ct_raw.get("enabled", True),
        ),
        confirmed_median=TradeFilterTriggerToggleConfig(
            enabled=cm_raw.get("enabled", True),
        ),
    )

    lc_raw: dict = tf_raw.get("lifecycle") or {}
    lifecycle = TradeFilterLifecycleConfig(
        freeze_confirmed_legs=lc_raw.get("freeze_confirmed_legs", 5),
        stop_check=lc_raw.get("stop_check", "confirm_bar_only"),
        stopping_exit=lc_raw.get("stopping_exit", "opposite_st_flip"),
        exit_off_mode=lc_raw.get("exit_off_mode", "exit A"),
        exit_off_zz_leg_count=lc_raw.get("exit_off_zz_leg_count", None),
        exit_b_immediate_off=lc_raw.get("exit_b_immediate_off", False),
    )

    diag_raw: dict = tf_raw.get("diagnostics") or {}
    diagnostics = TradeFilterDiagnosticsConfig(
        export_state_columns=bool(diag_raw.get("export_state_columns", True)),
        export_trigger_columns=bool(diag_raw.get("export_trigger_columns", True)),
    )

    # time_filter block (docs/time_filter_plan_v1_final.txt §1.2).
    # Raw values are preserved (no coercion) so validate_trade_filter can detect
    # and report type mismatches for enabled/window.
    tflt_raw: dict = tf_raw.get("time_filter") or {}
    time_filter = TradeFilterTimeFilterConfig(
        enabled=tflt_raw.get("enabled", False),
        window=tflt_raw.get("window", None),
    )

    vol_raw: dict = tf_raw.get("volume") or {}
    baseline_session_raw = vol_raw.get("baseline_session") or {}
    baseline_session = TradeFilterBaselineSessionConfig(
        enabled=baseline_session_raw.get("enabled", False),
        window=baseline_session_raw.get("window", None),
    )
    volume = TradeFilterVolumeConfig(
        enabled=vol_raw.get("enabled", None),
        mode=vol_raw.get("mode", None),
        aggregation=vol_raw.get("aggregation", "median"),
        daily_reset=vol_raw.get("daily_reset", False),
        cycle_direction_gate=vol_raw.get("cycle_direction_gate", False),
        short_window=vol_raw.get("short_window", None),
        baseline_window=vol_raw.get("baseline_window", None),
        threshold_ratio=vol_raw.get("threshold_ratio", None),
        exit_hysteresis_ratio=vol_raw.get("exit_hysteresis_ratio", None),
        exit_freeze_bars=vol_raw.get("exit_freeze_bars", None),
        regime_low_ratio=vol_raw.get("regime_low_ratio", None),
        regime_high_ratio=vol_raw.get("regime_high_ratio", None),
        direction_lookback_bars=vol_raw.get("direction_lookback_bars", None),
        baseline_session=baseline_session,
    ) if "volume" in tf_raw else None

    wakeup_regime = None
    if "wakeup_regime" in tf_raw:
        wakeup_raw: dict = tf_raw.get("wakeup_regime") or {}
        entry_raw: dict = wakeup_raw.get("entry") or {}
        candidate_height_raw: dict = entry_raw.get("candidate_height") or {}
        candidate_age_raw: dict = entry_raw.get("candidate_age") or {}
        atr_expansion_raw: dict = entry_raw.get("atr_expansion") or {}
        volume_expansion_raw: dict = entry_raw.get("volume_expansion") or {}

        exit_raw: dict = wakeup_raw.get("exit") or {}
        ttl_raw: dict = exit_raw.get("ttl") or {}
        no_fresh_raw: dict = exit_raw.get("no_fresh_candidate") or {}
        max_trades_raw: dict = exit_raw.get("max_trades_per_cycle") or {}
        cycle_take_profit_raw: dict = exit_raw.get("cycle_take_profit") or {}
        local_median_stop_raw: dict = exit_raw.get("local_median_stop") or {}
        action_raw: dict = exit_raw.get("action") or {}
        position_freeze_raw = wakeup_raw.get("position_freeze") or {}
        position_freeze_raw_is_mapping = isinstance(position_freeze_raw, dict)

        wakeup_regime = TradeFilterWakeupRegimeConfig(
            enabled=wakeup_raw.get("enabled", False),
            lock_cycle_direction=wakeup_raw.get("lock_cycle_direction", False),
            entry=TradeFilterWakeupEntryConfig(
                candidate_height=TradeFilterWakeupCandidateHeightConfig(
                    enabled=candidate_height_raw.get("enabled", False),
                    quantile=candidate_height_raw.get("quantile", None),
                ),
                candidate_age=TradeFilterWakeupCandidateAgeConfig(
                    enabled=candidate_age_raw.get("enabled", False),
                    max_bars=candidate_age_raw.get("max_bars", None),
                ),
                atr_expansion=TradeFilterWakeupAtrExpansionConfig(
                    enabled=atr_expansion_raw.get("enabled", False),
                    short_window=atr_expansion_raw.get("short_window", None),
                    long_window=atr_expansion_raw.get("long_window", None),
                    min_ratio=atr_expansion_raw.get("min_ratio", None),
                ),
                volume_expansion=TradeFilterWakeupVolumeExpansionConfig(
                    enabled=volume_expansion_raw.get("enabled", False),
                    short_window=volume_expansion_raw.get("short_window", None),
                    baseline_window=volume_expansion_raw.get(
                        "baseline_window", None
                    ),
                    min_ratio=volume_expansion_raw.get("min_ratio", None),
                ),
                direction_mode=entry_raw.get("direction_mode", "normal"),
            ),
            exit=TradeFilterWakeupExitConfig(
                ttl=TradeFilterWakeupTtlExitConfig(
                    enabled=ttl_raw.get("enabled", False),
                    bars=ttl_raw.get("bars", None),
                ),
                no_fresh_candidate=TradeFilterWakeupNoFreshCandidateExitConfig(
                    enabled=no_fresh_raw.get("enabled", False),
                    quantile=no_fresh_raw.get("quantile", None),
                    max_age_bars=no_fresh_raw.get("max_age_bars", None),
                    timeout_bars=no_fresh_raw.get("timeout_bars", None),
                ),
                max_trades_per_cycle=TradeFilterWakeupMaxTradesPerCycleConfig(
                    enabled=max_trades_raw.get("enabled", False),
                    max_trades=max_trades_raw.get("max_trades", None),
                ),
                cycle_take_profit=TradeFilterWakeupCycleTakeProfitExitConfig(
                    enabled=cycle_take_profit_raw.get("enabled", False),
                    pnl_pct=cycle_take_profit_raw.get("pnl_pct", None),
                ),
                local_median_stop=TradeFilterWakeupLocalMedianStopExitConfig(
                    enabled=local_median_stop_raw.get("enabled", False),
                ),
                action=TradeFilterWakeupExitActionConfig(
                    mode=action_raw.get("mode", None),
                ),
            ),
            position_freeze=TradeFilterWakeupPositionFreezeConfig(
                enabled=(
                    position_freeze_raw.get("enabled", False)
                    if position_freeze_raw_is_mapping else position_freeze_raw
                ),
                min_hold_bars=(
                    position_freeze_raw.get("min_hold_bars", None)
                    if position_freeze_raw_is_mapping else None
                ),
                apply_to=(
                    position_freeze_raw.get("apply_to", None)
                    if position_freeze_raw_is_mapping else None
                ),
                release_action=(
                    position_freeze_raw.get("release_action", None)
                    if position_freeze_raw_is_mapping else None
                ),
            ),
        )

    return TradeFilterConfig(
        enabled=enabled_raw,
        type=tf_type,
        zigzag=zigzag,
        triggers=triggers,
        lifecycle=lifecycle,
        diagnostics=diagnostics,
        time_filter=time_filter,
        volume=volume,
        wakeup_regime=wakeup_regime,
        _raw_triggers_present=("triggers" in tf_raw),
    )


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def _append_validation_error(
    errors: list[str],
    message: str,
    error_keys: Optional[list[str]] = None,
    key: Optional[str] = None,
) -> None:
    errors.append(message)
    if error_keys is not None and key is not None:
        error_keys.append(key)


def _validate_int_ge_one(value: object, field_path: str, errors: list[str]) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{field_path} must be int >= 1, got {value!r}")
        return None
    if value < 1:
        errors.append(f"{field_path} must be int >= 1, got {value!r}")
        return None
    return value


def _validate_int_ge_zero(value: object, field_path: str, errors: list[str]) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{field_path} must be int >= 0, got {value!r}")
        return None
    if value < 0:
        errors.append(f"{field_path} must be int >= 0, got {value!r}")
        return None
    return value


def _validate_positive_finite(
    value: object,
    field_path: str,
    errors: list[str],
) -> Optional[float]:
    if isinstance(value, bool):
        errors.append(f"{field_path} must be finite > 0, got {value!r}")
        return None
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        errors.append(f"{field_path} must be finite > 0, got {value!r}")
        return None
    if not math.isfinite(value_f) or value_f <= 0:
        errors.append(f"{field_path} must be finite > 0, got {value!r}")
        return None
    return value_f


def _validate_quantile_open_interval(
    value: object,
    field_path: str,
    errors: list[str],
) -> Optional[float]:
    if isinstance(value, bool):
        errors.append(f"{field_path} must be finite numeric in (0, 1), got {value!r}")
        return None
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        errors.append(f"{field_path} must be finite numeric in (0, 1), got {value!r}")
        return None
    if not math.isfinite(value_f) or not (0.0 < value_f < 1.0):
        errors.append(f"{field_path} must be finite numeric in (0, 1), got {value!r}")
        return None
    return value_f


def _validate_bool_field(
    value: object,
    field_path: str,
    errors: list[str],
) -> bool:
    if not isinstance(value, bool):
        errors.append(
            f"{field_path} must be bool (true/false), "
            f"got {type(value).__name__!r} ({value!r})"
        )
        return False
    return True


def _is_mode_d(tf: TradeFilterConfig, raw_user_keys: frozenset[tuple[str, ...]]) -> bool:
    return (
        ("trade_filter", "zigzag", "mode") in raw_user_keys
        and getattr(getattr(tf, "zigzag", None), "mode", None) == "D"
    )


def _is_exit_c(tf: TradeFilterConfig, raw_user_keys: frozenset[tuple[str, ...]]) -> bool:
    return (
        ("trade_filter", "lifecycle", "exit_off_mode") in raw_user_keys
        and getattr(getattr(tf, "lifecycle", None), "exit_off_mode", None) == "exit C"
    )


def _validate_volume_baseline_session(
    vol: TradeFilterVolumeConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    block_key = ("trade_filter", "volume", "baseline_session")
    if block_key not in raw_user_keys:
        return

    baseline_session = getattr(vol, "baseline_session", None)
    enabled = getattr(baseline_session, "enabled", False)
    if not isinstance(enabled, bool):
        errors.append(
            "trade_filter.volume.baseline_session.enabled must be bool "
            f"(true/false), got {type(enabled).__name__!r} ({enabled!r})"
        )
        return
    if not enabled:
        return

    window_key = ("trade_filter", "volume", "baseline_session", "window")
    window = getattr(baseline_session, "window", None)
    if window_key not in raw_user_keys or window is None:
        errors.append(
            "trade_filter.volume.baseline_session.window is required when "
            "trade_filter.volume.baseline_session.enabled is true"
        )
        return
    if not isinstance(window, str) or not _TF_WINDOW_RE.match(window):
        errors.append(
            "trade_filter.volume.baseline_session.window must be in "
            f"HH:MM-HH:MM format, got {window!r}"
        )
        return

    start_str, end_str = window[:5], window[6:]
    sh, sm = int(start_str[:2]), int(start_str[3:])
    eh, em = int(end_str[:2]), int(end_str[3:])
    hours_ok = (0 <= sh <= 23) and (0 <= eh <= 23)
    mins_ok = (0 <= sm <= 59) and (0 <= em <= 59)
    if not hours_ok:
        errors.append(
            "trade_filter.volume.baseline_session.window hours must be in "
            f"0-23, got {window!r}"
        )
        return
    if not mins_ok:
        errors.append(
            "trade_filter.volume.baseline_session.window minutes must be in "
            f"0-59, got {window!r}"
        )
        return

    start_total = sh * 60 + sm
    end_total = eh * 60 + em
    if start_total == end_total:
        errors.append(
            "trade_filter.volume.baseline_session.window must have non-zero "
            f"length (start == end is not allowed), got {window!r}"
        )
    elif start_total > end_total:
        errors.append(
            "trade_filter.volume.baseline_session.window wrap-around "
            f"(start > end) is not supported in v1, got {window!r}"
        )


def _validate_volume_block(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    volume_block_present = ("trade_filter", "volume") in raw_user_keys
    if not volume_block_present:
        return
    vol = getattr(tf, "volume", None)
    if vol is None:
        return

    enabled_present = ("trade_filter", "volume", "enabled") in raw_user_keys
    if not enabled_present:
        return
    if vol.enabled is None:
        errors.append(
            "trade_filter.volume.enabled must be bool (true/false), got null"
        )
        return
    if not isinstance(vol.enabled, bool):
        errors.append(
            "trade_filter.volume.enabled must be bool (true/false), "
            f"got {type(vol.enabled).__name__!r} ({vol.enabled!r})"
        )
        return
    if not vol.enabled:
        return

    aggregation_key = ("trade_filter", "volume", "aggregation")
    if aggregation_key in raw_user_keys:
        if vol.aggregation is None:
            errors.append(
                "trade_filter.volume.aggregation must be 'median' or 'mean', got null"
            )
        elif not isinstance(vol.aggregation, str):
            errors.append(
                "trade_filter.volume.aggregation must be 'median' or 'mean', "
                f"got {type(vol.aggregation).__name__!r} ({vol.aggregation!r})"
            )
        elif vol.aggregation not in ("median", "mean"):
            errors.append(
                "trade_filter.volume.aggregation must be 'median' or 'mean', "
                f"got {vol.aggregation!r}"
            )

    _validate_volume_baseline_session(vol, errors, raw_user_keys)

    daily_reset_key = ("trade_filter", "volume", "daily_reset")
    if daily_reset_key in raw_user_keys and not isinstance(vol.daily_reset, bool):
        errors.append(
            "trade_filter.volume.daily_reset must be bool (true/false), "
            f"got {type(vol.daily_reset).__name__!r} ({vol.daily_reset!r})"
        )

    cycle_direction_gate_key = ("trade_filter", "volume", "cycle_direction_gate")
    if cycle_direction_gate_key in raw_user_keys:
        if not isinstance(vol.cycle_direction_gate, bool):
            errors.append(
                "trade_filter.volume.cycle_direction_gate must be bool "
                f"(true/false), got {type(vol.cycle_direction_gate).__name__!r} "
                f"({vol.cycle_direction_gate!r})"
            )
        elif (
            vol.cycle_direction_gate
            and _is_zigzag_enabled_for_validation(tf, raw_user_keys)
        ):
            _append_validation_error(
                errors,
                "trade_filter.volume.cycle_direction_gate=true requires "
                "trade_filter.zigzag.enabled=false in v1",
                _VALIDATION_ERROR_KEYS.get(),
                "cycle_direction_gate_requires_volume_only",
            )

    mode_key = ("trade_filter", "volume", "mode")
    if mode_key not in raw_user_keys or vol.mode is None:
        errors.append(
            "trade_filter.volume.mode is required when "
            "trade_filter.volume.enabled is true"
        )
    elif vol.mode not in ("volume_A", "volume_B"):
        errors.append(
            "trade_filter.volume.mode must be 'volume_A' or 'volume_B', "
            f"got {vol.mode!r}"
        )

    short_key = ("trade_filter", "volume", "short_window")
    if short_key not in raw_user_keys or vol.short_window is None:
        errors.append(
            "trade_filter.volume.short_window is required when "
            "trade_filter.volume.enabled is true"
        )
        short_window = None
    else:
        short_window = _validate_int_ge_one(
            vol.short_window,
            "trade_filter.volume.short_window",
            errors,
        )

    baseline_key = ("trade_filter", "volume", "baseline_window")
    if baseline_key not in raw_user_keys or vol.baseline_window is None:
        errors.append(
            "trade_filter.volume.baseline_window is required when "
            "trade_filter.volume.enabled is true"
        )
        baseline_window = None
    else:
        baseline_window = _validate_int_ge_one(
            vol.baseline_window,
            "trade_filter.volume.baseline_window",
            errors,
        )

    if (
        short_window is not None
        and baseline_window is not None
        and baseline_window < short_window
    ):
        errors.append(
            "trade_filter.volume.baseline_window must be >= "
            "trade_filter.volume.short_window"
        )

    threshold_key = ("trade_filter", "volume", "threshold_ratio")
    if threshold_key not in raw_user_keys or vol.threshold_ratio is None:
        errors.append(
            "trade_filter.volume.threshold_ratio is required when "
            "trade_filter.volume.enabled is true"
        )
    else:
        _validate_positive_finite(
            vol.threshold_ratio,
            "trade_filter.volume.threshold_ratio",
            errors,
        )

    exit_hysteresis_key = ("trade_filter", "volume", "exit_hysteresis_ratio")
    if exit_hysteresis_key in raw_user_keys:
        _validate_positive_finite(
            vol.exit_hysteresis_ratio,
            "trade_filter.volume.exit_hysteresis_ratio",
            errors,
        )

    exit_freeze_key = ("trade_filter", "volume", "exit_freeze_bars")
    if exit_freeze_key in raw_user_keys:
        _validate_int_ge_zero(
            vol.exit_freeze_bars,
            "trade_filter.volume.exit_freeze_bars",
            errors,
        )

    low_key = ("trade_filter", "volume", "regime_low_ratio")
    high_key = ("trade_filter", "volume", "regime_high_ratio")
    low_ratio = 0.8
    high_ratio = 1.2
    if low_key in raw_user_keys:
        low_ratio = _validate_positive_finite(
            vol.regime_low_ratio,
            "trade_filter.volume.regime_low_ratio",
            errors,
        )
    if high_key in raw_user_keys:
        high_ratio = _validate_positive_finite(
            vol.regime_high_ratio,
            "trade_filter.volume.regime_high_ratio",
            errors,
        )
    if low_ratio is not None and high_ratio is not None and high_ratio <= low_ratio:
        errors.append(
            "trade_filter.volume.regime_high_ratio must be > "
            "trade_filter.volume.regime_low_ratio"
        )

    lookback_key = ("trade_filter", "volume", "direction_lookback_bars")
    if lookback_key in raw_user_keys:
        _validate_int_ge_one(
            vol.direction_lookback_bars,
            "trade_filter.volume.direction_lookback_bars",
            errors,
        )


_VALIDATION_ERROR_KEYS: ContextVar[Optional[list[str]]] = ContextVar(
    "_VALIDATION_ERROR_KEYS", default=None
)

def _validate_root_enabled_and_type(tf: TradeFilterConfig, errors: list[str], raw_user_keys: frozenset[tuple[str, ...]]) -> bool:
    # ------------------------------------------------------------------
    # Rule: enabled key presence and type
    # ------------------------------------------------------------------
    enabled_key = ("trade_filter", "enabled")
    if enabled_key not in raw_user_keys:
        errors.append(
            "trade_filter.enabled is required when trade_filter block is present"
        )
        return False  # cannot infer intent without enabled — stop here

    if not isinstance(tf.enabled, bool):
        errors.append(
            f"trade_filter.enabled must be bool (true/false), "
            f"got {type(tf.enabled).__name__!r} ({tf.enabled!r})"
        )
        return False

    # ------------------------------------------------------------------
    # Rule: type validation
    # ------------------------------------------------------------------
    type_present = ("trade_filter", "type") in raw_user_keys

    if tf.enabled:
        if type_present and tf.type is not None and tf.type not in _SUPPORTED_TRADE_FILTER_TYPES:
            errors.append(
                f"trade_filter.type {tf.type!r} is not supported; "
                f"supported: {sorted(_SUPPORTED_TRADE_FILTER_TYPES)}"
            )
    else:
        # enabled=false: type optional; if present, must be zigzag_st_mode (§11.1)
        if type_present and tf.type is not None and tf.type not in _SUPPORTED_TRADE_FILTER_TYPES:
            errors.append(
                f"trade_filter.type {tf.type!r} is not supported for disabled filter; "
                f"use zigzag_st_mode or omit type"
            )
        # БЛОК А: проверяем присутствие imm-ключа до раннего return (§3.4)
        imm_present_disabled = (
            ("trade_filter", "lifecycle", "exit_b_immediate_off") in raw_user_keys
        )
        if imm_present_disabled:
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_b_immediate_off must be absent when "
                "trade_filter.enabled is false",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_b_immediate_off_present_when_filter_disabled",
            )
        # disabled filter: skip all further validation (§11.1)
        return False

    return True

def _validate_zigzag_block(tf: TradeFilterConfig, errors: list[str], raw_user_keys: frozenset[tuple[str, ...]]) -> None:
    # ------------------------------------------------------------------
    # v3: Mode validation (WP-V3-1 §4.1, A1/A2/A7/A8)
    # ------------------------------------------------------------------
    mode_present = ("trade_filter", "zigzag", "mode") in raw_user_keys
    triggers_present = ("trade_filter", "triggers") in raw_user_keys
    mode_value = tf.zigzag.mode

    # A8: candidate_entry deprecated (ТЗ v3 §3.1, §4.5 candidate_entry_deprecated)
    # candidate_entry is allowed through strict schema so this specific message
    # can be emitted here instead of a generic "unknown config key".
    if ("trade_filter", "zigzag", "candidate_entry") in raw_user_keys:
        _append_validation_error(
            errors,
            "trade_filter.zigzag.candidate_entry is deprecated in v3 and not "
            "supported; use trade_filter.zigzag.mode instead",
            _VALIDATION_ERROR_KEYS.get(),
            "candidate_entry_deprecated",
        )

    # A7: explicit mode + legacy triggers -> ConfigError (mixed schema)
    if mode_present and triggers_present:
        _append_validation_error(
            errors,
            "trade_filter.zigzag.mode and trade_filter.triggers cannot be used together; "
            "use canonical mode (zigzag.mode) or legacy triggers, not both",
            _VALIDATION_ERROR_KEYS.get(),
            "mode_conflicts_with_legacy_triggers",
        )

    # A1/A2: validate mode literal when explicitly set
    if mode_present:
        if mode_value not in _VALID_ZIGZAG_MODES:
            _append_validation_error(
                errors,
                f"trade_filter.zigzag.mode must be one of "
                f"{sorted(_VALID_ZIGZAG_MODES)}; got {mode_value!r}",
                _VALIDATION_ERROR_KEYS.get(),
                "mode_invalid_literal",
            )

    # ------------------------------------------------------------------
    # ZigZag block
    # ------------------------------------------------------------------
    zz = tf.zigzag
    zigzag_enabled_present = ("trade_filter", "zigzag", "enabled") in raw_user_keys
    if zigzag_enabled_present and not isinstance(zz.enabled, bool):
        errors.append(
            "trade_filter.zigzag.enabled must be bool (true/false), "
            f"got {type(zz.enabled).__name__!r} ({zz.enabled!r})"
        )

    # global_stats_source
    if zz.global_stats_source != "full_dataset":
        errors.append(
            f"trade_filter.zigzag.global_stats_source must be 'full_dataset', "
            f"got {zz.global_stats_source!r} (only full_dataset supported in v1)"
        )

    # leg_height_mode
    if zz.leg_height_mode != "pct":
        errors.append(
            f"trade_filter.zigzag.leg_height_mode must be 'pct', "
            f"got {zz.leg_height_mode!r} (only pct supported in v1)"
        )

    # global_median
    if zz.global_median != "auto":
        errors.append(
            f"trade_filter.zigzag.global_median must be 'auto', "
            f"got {zz.global_median!r} (only auto full_dataset median supported)"
        )

    # reversal_threshold — required, numeric fraction in (0, 1) (§15.6)
    rt = zz.reversal_threshold
    if rt is None:
        errors.append(
            "trade_filter.zigzag.reversal_threshold is required when "
            "trade_filter.enabled is true"
        )
    elif isinstance(rt, str):
        errors.append(
            f"trade_filter.zigzag.reversal_threshold must be a numeric fraction "
            f"(e.g. 0.005 for 0.5%), not a string {rt!r}; percent strings are not allowed"
        )
    else:
        try:
            rt_f = float(rt)
            if not math.isfinite(rt_f):
                errors.append(
                    f"trade_filter.zigzag.reversal_threshold must be finite, got {rt_f}"
                )
            elif rt_f <= 0 or rt_f >= 1:
                errors.append(
                    f"trade_filter.zigzag.reversal_threshold must be in (0, 1) "
                    f"as a numeric fraction of price, got {rt_f}"
                )
        except (TypeError, ValueError):
            errors.append(
                f"trade_filter.zigzag.reversal_threshold must be numeric, got {rt!r}"
            )

    # local_window — integer >= 1
    lw = zz.local_window
    if isinstance(lw, bool) or not isinstance(lw, int) or lw < 1:
        errors.append(
            f"trade_filter.zigzag.local_window must be integer >= 1, got {lw!r}"
        )

    # daily_reset — bool only (plan v3 §2.4); validated only inside enabled=true branch
    dr = zz.daily_reset
    if not isinstance(dr, bool):
        errors.append(
            f"trade_filter.zigzag.daily_reset must be bool (true/false), "
            f"got {type(dr).__name__!r}"
        )

    # candidate_trigger_threshold and candidate_trigger_quantile (§11.3 / §6.4.1)
    ctt = zz.candidate_trigger_threshold
    quantile_explicit = (
        ("trade_filter", "zigzag", "candidate_trigger_quantile") in raw_user_keys
    )
    ctq = zz.candidate_trigger_quantile

    if ctt is None:
        errors.append(
            "trade_filter.zigzag.candidate_trigger_threshold is required when "
            "trade_filter.enabled is true — set to a numeric fraction (e.g. 0.012) "
            "or 'auto' (requires candidate_trigger_quantile); see §11.2 / §11.3"
        )
    else:
        if isinstance(ctt, str):
            if ctt == "auto":
                # auto: candidate_trigger_quantile is required
                if not quantile_explicit:
                    errors.append(
                        "trade_filter.zigzag.candidate_trigger_quantile is required "
                        "when candidate_trigger_threshold is 'auto'"
                    )
                # quantile validity is checked in the standalone section below
            elif "%" in ctt:
                errors.append(
                    f"trade_filter.zigzag.candidate_trigger_threshold must be a "
                    f"numeric fraction (e.g. 0.012) or 'auto', not a percent string {ctt!r}"
                )
            else:
                errors.append(
                    f"trade_filter.zigzag.candidate_trigger_threshold must be a "
                    f"numeric fraction or 'auto', got {ctt!r}"
                )
        else:
            # Numeric candidate_trigger_threshold
            try:
                ctt_f = float(ctt)
                if not math.isfinite(ctt_f):
                    errors.append(
                        f"trade_filter.zigzag.candidate_trigger_threshold must be "
                        f"finite, got {ctt_f}"
                    )
                elif ctt_f <= 0 or ctt_f >= 1:
                    errors.append(
                        f"trade_filter.zigzag.candidate_trigger_threshold must be "
                        f"in (0, 1) as a numeric fraction, got {ctt_f}"
                    )
                # Numeric threshold + explicit quantile -> reject (§11.3 / §6.4.1)
                # This check uses raw-key presence, NOT the dataclass value, so it
                # remains correct even if the dataclass default is ever changed.
                if quantile_explicit:
                    errors.append(
                        "trade_filter.zigzag.candidate_trigger_quantile must not be "
                        "specified when candidate_trigger_threshold is numeric (§11.3); "
                        "remove candidate_trigger_quantile or set threshold to 'auto'"
                    )
            except (TypeError, ValueError):
                errors.append(
                    f"trade_filter.zigzag.candidate_trigger_threshold must be "
                    f"numeric or 'auto', got {ctt!r}"
                )

    # Standalone: validate quantile range whenever it is explicitly present
    if quantile_explicit:
        if ctq is None:
            errors.append(
                "trade_filter.zigzag.candidate_trigger_quantile must be "
                "a numeric value in (0, 1), got null"
            )
        else:
            try:
                ctq_f = float(ctq)
                if not (0.0 < ctq_f < 1.0):
                    errors.append(
                        f"trade_filter.zigzag.candidate_trigger_quantile must be "
                        f"in (0, 1), got {ctq_f}"
                    )
            except (TypeError, ValueError):
                errors.append(
                    f"trade_filter.zigzag.candidate_trigger_quantile must be "
                    f"numeric in (0, 1), got {ctq!r}"
                )

    # ------------------------------------------------------------------
    # v3: candidate_duration_gate validation (WP-V3-1 §4.2, A9-A14)
    # ------------------------------------------------------------------
    gate = tf.zigzag.candidate_duration_gate
    gate_block_present = (
        ("trade_filter", "zigzag", "candidate_duration_gate") in raw_user_keys
    )
    gate_max_bars_present = (
        ("trade_filter", "zigzag", "candidate_duration_gate", "max_bars") in raw_user_keys
    )

    if gate_block_present:
        gate_enabled = gate.enabled
        # A9: enabled must be bool
        if not isinstance(gate_enabled, bool):
            _append_validation_error(
                errors,
                "candidate_duration_gate.enabled must be bool (true/false), "
                f"got {type(gate_enabled).__name__!r}",
                _VALIDATION_ERROR_KEYS.get(),
                "duration_gate_enabled_invalid_type",
            )
        elif gate_enabled:
            # A10: max_bars required when enabled=true
            if not gate_max_bars_present:
                _append_validation_error(
                    errors,
                    "candidate_duration_gate.max_bars is required when enabled is true",
                    _VALIDATION_ERROR_KEYS.get(),
                    "duration_gate_max_bars_missing",
                )
            else:
                # A11: max_bars must be int >= 1; bool/float/string/null rejected
                mb = gate.max_bars
                if isinstance(mb, bool) or not isinstance(mb, int):
                    _append_validation_error(
                        errors,
                        f"candidate_duration_gate.max_bars must be int >= 1; got {mb!r}",
                        _VALIDATION_ERROR_KEYS.get(),
                        "duration_gate_max_bars_invalid_type",
                    )
                elif mb < 1:
                    _append_validation_error(
                        errors,
                        f"candidate_duration_gate.max_bars must be int >= 1; got {mb!r}",
                        _VALIDATION_ERROR_KEYS.get(),
                        "duration_gate_max_bars_below_one",
                    )
        else:
            # enabled=false: A12: max_bars must be absent
            if gate_max_bars_present:
                _append_validation_error(
                    errors,
                    "candidate_duration_gate.max_bars must be absent when enabled is false; "
                    "remove the key",
                    _VALIDATION_ERROR_KEYS.get(),
                    "duration_gate_max_bars_present_when_disabled",
                )
    # A13: absent gate block -> disabled gate (handled by default values; no action needed)


def _validate_lifecycle_block(tf: TradeFilterConfig, errors: list[str], raw_user_keys: frozenset[tuple[str, ...]]) -> None:
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    lc = tf.lifecycle

    # freeze_confirmed_legs — integer >= 0
    fcl = lc.freeze_confirmed_legs
    if isinstance(fcl, bool) or not isinstance(fcl, int) or fcl < 0:
        errors.append(
            f"trade_filter.lifecycle.freeze_confirmed_legs must be integer >= 0, "
            f"got {fcl!r}"
        )

    # stop_check — must be "confirm_bar_only" (§11; lifecycle literal)
    if lc.stop_check not in _LIFECYCLE_STOP_CHECK_VALUES:
        errors.append(
            f"trade_filter.lifecycle.stop_check must be one of "
            f"{sorted(_LIFECYCLE_STOP_CHECK_VALUES)}, got {lc.stop_check!r}"
        )

    # stopping_exit — must be "opposite_st_flip" (§11; lifecycle literal)
    if lc.stopping_exit not in _LIFECYCLE_STOPPING_EXIT_VALUES:
        errors.append(
            f"trade_filter.lifecycle.stopping_exit must be one of "
            f"{sorted(_LIFECYCLE_STOPPING_EXIT_VALUES)}, got {lc.stopping_exit!r}"
        )

    # ------------------------------------------------------------------
    # exit_off_mode / exit_off_zz_leg_count (docs/plan_exit_off_modes.txt)
    # ------------------------------------------------------------------
    eom_key = ("trade_filter", "lifecycle", "exit_off_mode")
    eoc_key = ("trade_filter", "lifecycle", "exit_off_zz_leg_count")
    exit_mode_key_present = eom_key in raw_user_keys
    exit_count_key_present = eoc_key in raw_user_keys

    if exit_mode_key_present:
        eom_raw = lc.exit_off_mode
        if not isinstance(eom_raw, str):
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_off_mode must be str literal "
                "\"exit A\", \"exit B\", or \"exit C\", got "
                f"{type(eom_raw).__name__!r} ({eom_raw!r})",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_off_mode_invalid_type",
            )
        elif eom_raw not in ("exit A", "exit B", "exit C"):
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_off_mode must be \"exit A\", "
                f"\"exit B\", or \"exit C\"; got {eom_raw!r}",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_off_mode_invalid_literal",
            )

    if exit_mode_key_present and isinstance(lc.exit_off_mode, str) and lc.exit_off_mode in (
        "exit A",
        "exit B",
        "exit C",
    ):
        _effective_exit_off = lc.exit_off_mode
    else:
        _effective_exit_off = "exit A"

    # БЛОК Б: валидация exit_b_immediate_off (§3.4)
    imm_present = (
        ("trade_filter", "lifecycle", "exit_b_immediate_off") in raw_user_keys
    )
    if imm_present and _effective_exit_off != "exit B":
        _append_validation_error(
            errors,
            "trade_filter.lifecycle.exit_b_immediate_off must be absent when "
            "exit_off_mode is not 'exit B'",
            _VALIDATION_ERROR_KEYS.get(),
            "exit_b_immediate_off_present_when_not_exit_b",
        )
    elif imm_present and _effective_exit_off == "exit B":
        if not isinstance(lc.exit_b_immediate_off, bool):
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_b_immediate_off must be bool "
                f"(true/false), got {type(lc.exit_b_immediate_off).__name__!r} "
                f"({lc.exit_b_immediate_off!r})",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_b_immediate_off_invalid_type",
            )

    if exit_count_key_present and _effective_exit_off != "exit B":
        _append_validation_error(
            errors,
            "trade_filter.lifecycle.exit_off_zz_leg_count must be absent when "
            "trade_filter.lifecycle.exit_off_mode is not \"exit B\"",
            _VALIDATION_ERROR_KEYS.get(),
            "exit_off_zz_leg_count_present_when_exit_a",
        )

    if _effective_exit_off == "exit B":
        if not exit_count_key_present:
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_off_zz_leg_count is required when "
                "trade_filter.lifecycle.exit_off_mode is \"exit B\"",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_off_zz_leg_count_missing",
            )
        else:
            c_raw = lc.exit_off_zz_leg_count
            if isinstance(c_raw, bool) or not isinstance(c_raw, int):
                _append_validation_error(
                    errors,
                    "trade_filter.lifecycle.exit_off_zz_leg_count must be int >= 1, "
                    f"got {c_raw!r}",
                    _VALIDATION_ERROR_KEYS.get(),
                    "exit_off_zz_leg_count_invalid_type",
                )
            elif c_raw < 1:
                _append_validation_error(
                    errors,
                    "trade_filter.lifecycle.exit_off_zz_leg_count must be int >= 1, "
                    f"got {c_raw!r}",
                    _VALIDATION_ERROR_KEYS.get(),
                    "exit_off_zz_leg_count_below_one",
                )

    # NOTE: freeze_confirmed_legs < local_window is VALID — not a warning, not a
    # reject (Appendix A v1.1 §3.2, §17.20; plan §6.5 Note 1).  No check here.


def _validate_triggers_block(tf: TradeFilterConfig, errors: list[str], raw_user_keys: frozenset[tuple[str, ...]]) -> None:
    # ------------------------------------------------------------------
    # Triggers (legacy path: only when mode is absent)
    # ------------------------------------------------------------------
    mode_present = ("trade_filter", "zigzag", "mode") in raw_user_keys
    triggers_present = ("trade_filter", "triggers") in raw_user_keys
    if not mode_present and triggers_present:
        ct_enabled = tf.triggers.candidate_threshold.enabled
        cm_enabled = tf.triggers.confirmed_median.enabled

        if not isinstance(ct_enabled, bool):
            errors.append(
                f"trade_filter.triggers.candidate_threshold.enabled must be bool, "
                f"got {type(ct_enabled).__name__!r}"
            )
        if not isinstance(cm_enabled, bool):
            errors.append(
                f"trade_filter.triggers.confirmed_median.enabled must be bool, "
                f"got {type(cm_enabled).__name__!r}"
            )
        if isinstance(ct_enabled, bool) and isinstance(cm_enabled, bool):
            if not ct_enabled and not cm_enabled:
                errors.append(
                    "at least one trigger must be enabled "
                    "(trade_filter.triggers.candidate_threshold or confirmed_median)"
                )


def _validate_time_filter_block(tf: TradeFilterConfig, errors: list[str], raw_user_keys: frozenset[tuple[str, ...]]) -> None:
    # ------------------------------------------------------------------
    # time_filter validation (docs/time_filter_plan_v1_final.txt §2.1)
    # Only validated when trade_filter.enabled=true (already inside that branch).
    # ------------------------------------------------------------------
    tf_block_present = ("trade_filter", "time_filter") in raw_user_keys
    tf_enabled_present = ("trade_filter", "time_filter", "enabled") in raw_user_keys
    tf_window_present = ("trade_filter", "time_filter", "window") in raw_user_keys

    if tf_block_present:
        tfl = tf.time_filter
        # Rule 1: enabled must be bool when block is present
        if not isinstance(tfl.enabled, bool):
            _append_validation_error(
                errors,
                "trade_filter.time_filter.enabled must be bool (true/false), "
                f"got {type(tfl.enabled).__name__!r} ({tfl.enabled!r})",
                _VALIDATION_ERROR_KEYS.get(),
                "time_filter_enabled_invalid_type",
            )
        elif tfl.enabled:
            # Rule 2: time_filter.enabled=true — validate window
            if not tf_window_present or tfl.window is None:
                _append_validation_error(
                    errors,
                    "trade_filter.time_filter.window is required when "
                    "trade_filter.time_filter.enabled is true",
                    _VALIDATION_ERROR_KEYS.get(),
                    "time_filter_window_missing",
                )
            else:
                w = tfl.window
                if not isinstance(w, str) or not _TF_WINDOW_RE.match(w):
                    _append_validation_error(
                        errors,
                        f"trade_filter.time_filter.window must be in HH:MM-HH:MM format, "
                        f"got {w!r}",
                        _VALIDATION_ERROR_KEYS.get(),
                        "time_filter_window_invalid_format",
                    )
                else:
                    # Format valid — check numeric ranges
                    start_str, end_str = w[:5], w[6:]
                    sh, sm = int(start_str[:2]), int(start_str[3:])
                    eh, em = int(end_str[:2]), int(end_str[3:])
                    hours_ok = (0 <= sh <= 23) and (0 <= eh <= 23)
                    mins_ok = (0 <= sm <= 59) and (0 <= em <= 59)
                    if not hours_ok:
                        _append_validation_error(
                            errors,
                            f"trade_filter.time_filter.window hours must be in 0–23, "
                            f"got {w!r}",
                            _VALIDATION_ERROR_KEYS.get(),
                            "time_filter_window_invalid_hours",
                        )
                    elif not mins_ok:
                        _append_validation_error(
                            errors,
                            f"trade_filter.time_filter.window minutes must be in 0–59, "
                            f"got {w!r}",
                            _VALIDATION_ERROR_KEYS.get(),
                            "time_filter_window_invalid_minutes",
                        )
                    else:
                        start_total = sh * 60 + sm
                        end_total = eh * 60 + em
                        if start_total == end_total:
                            _append_validation_error(
                                errors,
                                f"trade_filter.time_filter.window must have non-zero length "
                                f"(start == end is not allowed), got {w!r}",
                                _VALIDATION_ERROR_KEYS.get(),
                                "time_filter_window_zero_length",
                            )
                        elif start_total > end_total:
                            _append_validation_error(
                                errors,
                                f"trade_filter.time_filter.window wrap-around (start > end) "
                                f"is not supported in v1, got {w!r}",
                                _VALIDATION_ERROR_KEYS.get(),
                                "time_filter_window_cross_midnight",
                            )
        # Rule 3: time_filter.enabled=false -> no deep validation (disabled-path)


def _validate_required_int_ge_one(
    value: object,
    value_present: bool,
    field_path: str,
    errors: list[str],
    *,
    required: bool,
) -> Optional[int]:
    if not value_present or value is None:
        if required:
            errors.append(f"{field_path} is required when component is enabled")
        return None
    return _validate_int_ge_one(value, field_path, errors)


def _validate_required_positive_finite(
    value: object,
    value_present: bool,
    field_path: str,
    errors: list[str],
    *,
    required: bool,
) -> Optional[float]:
    if not value_present or value is None:
        if required:
            errors.append(f"{field_path} is required when component is enabled")
        return None
    return _validate_positive_finite(value, field_path, errors)


def _validate_required_quantile(
    value: object,
    value_present: bool,
    field_path: str,
    errors: list[str],
    *,
    required: bool,
) -> Optional[float]:
    if not value_present or value is None:
        if required:
            errors.append(f"{field_path} is required when component is enabled")
        return None
    return _validate_quantile_open_interval(value, field_path, errors)


def _validate_wakeup_component_enabled(
    component: object,
    path: tuple[str, ...],
    raw_user_keys: frozenset[tuple[str, ...]],
    errors: list[str],
) -> bool:
    block_present = path in raw_user_keys
    enabled = getattr(component, "enabled", False)
    if block_present and not _validate_bool_field(enabled, ".".join(path + ("enabled",)), errors):
        return False
    return enabled is True


def _validate_wakeup_direction_mode(value: object, errors: list[str]) -> None:
    if not isinstance(value, str):
        _append_validation_error(
            errors,
            "trade_filter.wakeup_regime.entry.direction_mode must be a string, "
            f"got {type(value).__name__} ({value!r})",
            _VALIDATION_ERROR_KEYS.get(),
            "wakeup_direction_mode_invalid",
        )
        return

    if value not in ("normal", "inverse"):
        _append_validation_error(
            errors,
            "trade_filter.wakeup_regime.entry.direction_mode must be "
            f"'normal' or 'inverse', got {value!r}",
            _VALIDATION_ERROR_KEYS.get(),
            "wakeup_direction_mode_invalid",
        )


def _validate_wakeup_regime_block(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    wakeup_key = ("trade_filter", "wakeup_regime")
    if wakeup_key not in raw_user_keys:
        return

    wakeup = getattr(tf, "wakeup_regime", None)
    if wakeup is None:
        return

    lock_key = wakeup_key + ("lock_cycle_direction",)
    if lock_key in raw_user_keys:
        _validate_bool_field(
            getattr(wakeup, "lock_cycle_direction", False),
            "trade_filter.wakeup_regime.lock_cycle_direction",
            errors,
        )

    entry_key = wakeup_key + ("entry",)
    entry = wakeup.entry
    direction_mode_key = entry_key + ("direction_mode",)
    if direction_mode_key in raw_user_keys:
        _validate_wakeup_direction_mode(
            getattr(entry, "direction_mode", "normal"),
            errors,
        )

    if not _validate_bool_field(
        getattr(wakeup, "enabled", False),
        "trade_filter.wakeup_regime.enabled",
        errors,
    ):
        return

    wakeup_enabled = wakeup.enabled is True
    exit_key = wakeup_key + ("exit",)
    if wakeup_enabled:
        if entry_key not in raw_user_keys:
            errors.append(
                "trade_filter.wakeup_regime.entry is required when "
                "wakeup_regime.enabled is true"
            )
        if exit_key not in raw_user_keys:
            errors.append(
                "trade_filter.wakeup_regime.exit is required when "
                "wakeup_regime.enabled is true"
            )

    ch_path = entry_key + ("candidate_height",)
    ca_path = entry_key + ("candidate_age",)
    atr_path = entry_key + ("atr_expansion",)
    vol_path = entry_key + ("volume_expansion",)

    ch_enabled = _validate_wakeup_component_enabled(
        entry.candidate_height, ch_path, raw_user_keys, errors
    )
    ca_enabled = _validate_wakeup_component_enabled(
        entry.candidate_age, ca_path, raw_user_keys, errors
    )
    atr_enabled = _validate_wakeup_component_enabled(
        entry.atr_expansion, atr_path, raw_user_keys, errors
    )
    vol_enabled = _validate_wakeup_component_enabled(
        entry.volume_expansion, vol_path, raw_user_keys, errors
    )

    _validate_required_quantile(
        entry.candidate_height.quantile,
        ch_path + ("quantile",) in raw_user_keys,
        "trade_filter.wakeup_regime.entry.candidate_height.quantile",
        errors,
        required=ch_enabled,
    )
    _validate_required_int_ge_one(
        entry.candidate_age.max_bars,
        ca_path + ("max_bars",) in raw_user_keys,
        "trade_filter.wakeup_regime.entry.candidate_age.max_bars",
        errors,
        required=ca_enabled,
    )

    atr_short = _validate_required_int_ge_one(
        entry.atr_expansion.short_window,
        atr_path + ("short_window",) in raw_user_keys,
        "trade_filter.wakeup_regime.entry.atr_expansion.short_window",
        errors,
        required=atr_enabled,
    )
    atr_long = _validate_required_int_ge_one(
        entry.atr_expansion.long_window,
        atr_path + ("long_window",) in raw_user_keys,
        "trade_filter.wakeup_regime.entry.atr_expansion.long_window",
        errors,
        required=atr_enabled,
    )
    _validate_required_positive_finite(
        entry.atr_expansion.min_ratio,
        atr_path + ("min_ratio",) in raw_user_keys,
        "trade_filter.wakeup_regime.entry.atr_expansion.min_ratio",
        errors,
        required=atr_enabled,
    )
    if atr_short is not None and atr_long is not None and atr_long < atr_short:
        errors.append(
            "trade_filter.wakeup_regime.entry.atr_expansion.long_window "
            "must be >= short_window"
        )

    vol_short = _validate_required_int_ge_one(
        entry.volume_expansion.short_window,
        vol_path + ("short_window",) in raw_user_keys,
        "trade_filter.wakeup_regime.entry.volume_expansion.short_window",
        errors,
        required=vol_enabled,
    )
    vol_baseline = _validate_required_int_ge_one(
        entry.volume_expansion.baseline_window,
        vol_path + ("baseline_window",) in raw_user_keys,
        "trade_filter.wakeup_regime.entry.volume_expansion.baseline_window",
        errors,
        required=vol_enabled,
    )
    _validate_required_positive_finite(
        entry.volume_expansion.min_ratio,
        vol_path + ("min_ratio",) in raw_user_keys,
        "trade_filter.wakeup_regime.entry.volume_expansion.min_ratio",
        errors,
        required=vol_enabled,
    )
    if vol_short is not None and vol_baseline is not None and vol_baseline < vol_short:
        errors.append(
            "trade_filter.wakeup_regime.entry.volume_expansion.baseline_window "
            "must be >= short_window"
        )

    if wakeup_enabled and not any((ch_enabled, ca_enabled, atr_enabled, vol_enabled)):
        errors.append(
            "trade_filter.wakeup_regime requires at least one enabled entry component"
        )

    wakeup_exit = wakeup.exit
    ttl_path = exit_key + ("ttl",)
    nf_path = exit_key + ("no_fresh_candidate",)
    mt_path = exit_key + ("max_trades_per_cycle",)
    ctp_path = exit_key + ("cycle_take_profit",)
    lms_path = exit_key + ("local_median_stop",)
    action_path = exit_key + ("action",)

    ttl_enabled = _validate_wakeup_component_enabled(
        wakeup_exit.ttl, ttl_path, raw_user_keys, errors
    )
    nf_enabled = _validate_wakeup_component_enabled(
        wakeup_exit.no_fresh_candidate, nf_path, raw_user_keys, errors
    )
    max_trades_enabled = _validate_wakeup_component_enabled(
        wakeup_exit.max_trades_per_cycle, mt_path, raw_user_keys, errors
    )
    cycle_take_profit_enabled = _validate_wakeup_component_enabled(
        wakeup_exit.cycle_take_profit, ctp_path, raw_user_keys, errors
    )
    local_median_stop_enabled = _validate_wakeup_component_enabled(
        wakeup_exit.local_median_stop, lms_path, raw_user_keys, errors
    )

    _validate_required_int_ge_one(
        wakeup_exit.ttl.bars,
        ttl_path + ("bars",) in raw_user_keys,
        "trade_filter.wakeup_regime.exit.ttl.bars",
        errors,
        required=ttl_enabled,
    )
    _validate_required_quantile(
        wakeup_exit.no_fresh_candidate.quantile,
        nf_path + ("quantile",) in raw_user_keys,
        "trade_filter.wakeup_regime.exit.no_fresh_candidate.quantile",
        errors,
        required=nf_enabled,
    )
    _validate_required_int_ge_one(
        wakeup_exit.no_fresh_candidate.max_age_bars,
        nf_path + ("max_age_bars",) in raw_user_keys,
        "trade_filter.wakeup_regime.exit.no_fresh_candidate.max_age_bars",
        errors,
        required=nf_enabled,
    )
    _validate_required_int_ge_one(
        wakeup_exit.no_fresh_candidate.timeout_bars,
        nf_path + ("timeout_bars",) in raw_user_keys,
        "trade_filter.wakeup_regime.exit.no_fresh_candidate.timeout_bars",
        errors,
        required=nf_enabled,
    )
    _validate_required_int_ge_one(
        wakeup_exit.max_trades_per_cycle.max_trades,
        mt_path + ("max_trades",) in raw_user_keys,
        "trade_filter.wakeup_regime.exit.max_trades_per_cycle.max_trades",
        errors,
        required=max_trades_enabled,
    )
    _validate_required_positive_finite(
        wakeup_exit.cycle_take_profit.pnl_pct,
        ctp_path + ("pnl_pct",) in raw_user_keys,
        "trade_filter.wakeup_regime.exit.cycle_take_profit.pnl_pct",
        errors,
        required=cycle_take_profit_enabled,
    )

    if wakeup_enabled and not any((
        ttl_enabled,
        nf_enabled,
        max_trades_enabled,
        cycle_take_profit_enabled,
        local_median_stop_enabled,
    )):
        errors.append(
            "trade_filter.wakeup_regime requires at least one enabled exit condition"
        )

    action_mode_present = action_path + ("mode",) in raw_user_keys
    action_mode = wakeup_exit.action.mode
    if wakeup_enabled and not action_mode_present:
        errors.append(
            "trade_filter.wakeup_regime.exit.action.mode is required when "
            "wakeup_regime.enabled is true"
        )
    elif action_mode_present and action_mode not in (
        "block_new_entries",
        "close_position",
    ):
        errors.append(
            "trade_filter.wakeup_regime.exit.action.mode must be "
            "'block_new_entries' or 'close_position', got "
            f"{action_mode!r}"
        )

    position_freeze_path = wakeup_key + ("position_freeze",)
    if position_freeze_path in raw_user_keys:
        position_freeze = getattr(wakeup, "position_freeze", None)
        if position_freeze is None:
            return
        enabled = getattr(position_freeze, "enabled", False)
        if not isinstance(enabled, bool):
            _append_validation_error(
                errors,
                "trade_filter.wakeup_regime.position_freeze.enabled must be "
                f"bool (true/false), got {type(enabled).__name__!r} ({enabled!r})",
                _VALIDATION_ERROR_KEYS.get(),
                "position_freeze_enabled_invalid_type",
            )
            return

        if enabled is True:
            if wakeup_enabled is not True:
                _append_validation_error(
                    errors,
                    "trade_filter.wakeup_regime.position_freeze.enabled=true "
                    "requires trade_filter.wakeup_regime.enabled=true",
                    _VALIDATION_ERROR_KEYS.get(),
                    "position_freeze_enabled_requires_wakeup_enabled",
                )
            if not _is_mode_d(tf, raw_user_keys):
                _append_validation_error(
                    errors,
                    "trade_filter.wakeup_regime.position_freeze.enabled=true "
                    "requires trade_filter.zigzag.mode: D",
                    _VALIDATION_ERROR_KEYS.get(),
                    "position_freeze_enabled_requires_mode_d",
                )

            min_hold = getattr(position_freeze, "min_hold_bars", None)
            if (
                not isinstance(min_hold, int)
                or isinstance(min_hold, bool)
                or min_hold < 1
            ):
                _append_validation_error(
                    errors,
                    "trade_filter.wakeup_regime.position_freeze.min_hold_bars "
                    "must be integer >= 1 when position_freeze.enabled is true",
                    _VALIDATION_ERROR_KEYS.get(),
                    "position_freeze_min_hold_bars_invalid",
                )

            apply_to = getattr(position_freeze, "apply_to", None)
            if apply_to != "internal_opposite_st_flip":
                _append_validation_error(
                    errors,
                    "trade_filter.wakeup_regime.position_freeze.apply_to must "
                    "be 'internal_opposite_st_flip' when position_freeze.enabled "
                    f"is true, got {apply_to!r}",
                    _VALIDATION_ERROR_KEYS.get(),
                    "position_freeze_apply_to_invalid",
                )

            release_action = getattr(position_freeze, "release_action", None)
            if release_action != "apply_if_still_opposite":
                _append_validation_error(
                    errors,
                    "trade_filter.wakeup_regime.position_freeze.release_action "
                    "must be 'apply_if_still_opposite' when "
                    f"position_freeze.enabled is true, got {release_action!r}",
                    _VALIDATION_ERROR_KEYS.get(),
                    "position_freeze_release_action_invalid",
                )


def _validate_phase0_mode_d_cross_fields(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    mode_d = _is_mode_d(tf, raw_user_keys)
    exit_c = _is_exit_c(tf, raw_user_keys)
    wakeup_present = ("trade_filter", "wakeup_regime") in raw_user_keys
    wakeup = getattr(tf, "wakeup_regime", None)
    wakeup_enabled = wakeup is not None and getattr(wakeup, "enabled", None) is True

    if mode_d:
        if not exit_c:
            _append_validation_error(
                errors,
                "trade_filter.zigzag.mode='D' requires raw "
                "trade_filter.lifecycle.exit_off_mode: 'exit C'",
                _VALIDATION_ERROR_KEYS.get(),
                "mode_d_requires_exit_c",
            )
        if not wakeup_enabled:
            _append_validation_error(
                errors,
                "trade_filter.zigzag.mode='D' requires "
                "trade_filter.wakeup_regime.enabled: true",
                _VALIDATION_ERROR_KEYS.get(),
                "mode_d_requires_wakeup_enabled",
            )

        ctt = getattr(tf.zigzag, "candidate_trigger_threshold", None)
        if ctt == "auto":
            _append_validation_error(
                errors,
                "trade_filter.zigzag.mode='D' requires numeric "
                "candidate_trigger_threshold; 'auto' is not supported",
                _VALIDATION_ERROR_KEYS.get(),
                "mode_d_candidate_threshold_auto_rejected",
            )
        if ("trade_filter", "zigzag", "candidate_trigger_quantile") in raw_user_keys:
            _append_validation_error(
                errors,
                "trade_filter.zigzag.candidate_trigger_quantile is not allowed "
                "with mode D",
                _VALIDATION_ERROR_KEYS.get(),
                "mode_d_candidate_quantile_rejected",
            )

    if exit_c:
        if not mode_d:
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_off_mode='exit C' requires "
                "trade_filter.zigzag.mode: D",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_c_requires_mode_d",
            )
        if ("trade_filter", "lifecycle", "exit_off_zz_leg_count") in raw_user_keys:
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_off_zz_leg_count is not allowed "
                "with exit C",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_c_rejects_exit_off_zz_leg_count",
            )
        if ("trade_filter", "lifecycle", "exit_b_immediate_off") in raw_user_keys:
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_b_immediate_off is not allowed "
                "with exit C",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_c_rejects_exit_b_immediate_off",
            )
        if ("trade_filter", "triggers") in raw_user_keys:
            _append_validation_error(
                errors,
                "trade_filter.triggers legacy block is not allowed with exit C",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_c_rejects_legacy_triggers",
            )

    if wakeup_present and not mode_d:
        _append_validation_error(
            errors,
            "trade_filter.wakeup_regime is allowed only with "
            "trade_filter.zigzag.mode: D",
            _VALIDATION_ERROR_KEYS.get(),
            "wakeup_regime_requires_mode_d",
        )
    if wakeup_enabled and not mode_d:
        _append_validation_error(
            errors,
            "trade_filter.wakeup_regime.enabled=true requires "
            "trade_filter.zigzag.mode: D",
            _VALIDATION_ERROR_KEYS.get(),
            "wakeup_enabled_requires_mode_d",
        )


def _validate_caller_pipeline_gate(tf: TradeFilterConfig, errors: list[str], raw_user_keys: frozenset[tuple[str, ...]], caller_pipeline: str) -> bool:
    # ------------------------------------------------------------------
    # WP-T3 step 6 — caller_pipeline domain check
    # ------------------------------------------------------------------
    if caller_pipeline not in _CALLER_PIPELINE_DOMAIN:
        errors.append(
            f"validate_trade_filter: unknown caller_pipeline {caller_pipeline!r}; "
            f"supported: {sorted(_CALLER_PIPELINE_DOMAIN)}"
        )
        return False

    if (
        getattr(tf, "enabled", None) is True
        and _is_zigzag_enabled_for_validation(tf, raw_user_keys)
        and getattr(tf, "type", None) == "zigzag_st_mode"
        and caller_pipeline not in _ZIGZAG_ST_MODE_ALLOWED_CALLERS
    ):
        # WP-T3 step 6 - pipeline whitelist (plan section 5.5).
        errors.append(
            f"trade_filter.type='zigzag_st_mode' is not supported in "
            f"pipeline {caller_pipeline!r}; allowed pipelines: "
            f"{sorted(_ZIGZAG_ST_MODE_ALLOWED_CALLERS)}"
        )
        return False

    if caller_pipeline == "wf_grid":
        if _is_mode_d(tf, raw_user_keys):
            _append_validation_error(
                errors,
                "trade_filter.zigzag.mode='D' is not supported by wf_grid "
                "in Phase 0; use tester pipeline",
                _VALIDATION_ERROR_KEYS.get(),
                "mode_d_unsupported_pipeline",
            )
        if _is_exit_c(tf, raw_user_keys):
            _append_validation_error(
                errors,
                "trade_filter.lifecycle.exit_off_mode='exit C' is not "
                "supported by wf_grid in Phase 0; use tester pipeline",
                _VALIDATION_ERROR_KEYS.get(),
                "exit_c_unsupported_pipeline",
            )
        if ("trade_filter", "wakeup_regime") in raw_user_keys:
            _append_validation_error(
                errors,
                "trade_filter.wakeup_regime is not supported by wf_grid "
                "in Phase 0; use tester pipeline",
                _VALIDATION_ERROR_KEYS.get(),
                "wakeup_regime_unsupported_pipeline",
            )

    return True


def _has_legacy_zigzag_marker(
    tf: TradeFilterConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> bool:
    if getattr(tf, "type", None) == "zigzag_st_mode":
        return True
    return any(
        len(key) >= 3
        and key[0] == "trade_filter"
        and key[1] == "zigzag"
        and key[2] != "enabled"
        for key in raw_user_keys
    )


def _is_zigzag_enabled_for_validation(
    tf: TradeFilterConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> bool:
    if is_zigzag_enabled(tf):
        return True
    if not is_trade_filter_enabled(tf):
        return False
    zigzag = getattr(tf, "zigzag", None)
    if zigzag is None:
        return False
    if ("trade_filter", "zigzag", "enabled") in raw_user_keys:
        return False
    return getattr(zigzag, "enabled", None) is None and _has_legacy_zigzag_marker(
        tf, raw_user_keys
    )


def _has_malformed_subfilter_enabled_flags(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> bool:
    before = len(errors)
    if ("trade_filter", "zigzag", "enabled") in raw_user_keys:
        zz_enabled = getattr(getattr(tf, "zigzag", None), "enabled", None)
        if not isinstance(zz_enabled, bool):
            errors.append(
                "trade_filter.zigzag.enabled must be bool (true/false), "
                f"got {type(zz_enabled).__name__!r} ({zz_enabled!r})"
            )
    if ("trade_filter", "volume", "enabled") in raw_user_keys:
        _validate_volume_block(tf, errors, raw_user_keys)
    return len(errors) > before


def _validate_standalone_volume_legality(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    zigzag_payload_keys = [
        ".".join(key)
        for key in raw_user_keys
        if len(key) >= 3
        and key[0] == "trade_filter"
        and key[1] == "zigzag"
        and key[2] != "enabled"
    ]
    if zigzag_payload_keys:
        errors.append(
            "standalone volume mode requires removing ZigZag fields other than "
            "trade_filter.zigzag.enabled; remove "
            f"{sorted(zigzag_payload_keys)} or set trade_filter.zigzag.enabled: true"
        )

    if ("trade_filter", "type") in raw_user_keys and tf.type == "zigzag_st_mode":
        errors.append(
            "trade_filter.type=zigzag_st_mode is only valid when "
            "trade_filter.zigzag.enabled is true; remove trade_filter.type for "
            "standalone volume mode"
        )

    if ("trade_filter", "triggers") in raw_user_keys:
        errors.append(
            "trade_filter.triggers is not allowed when "
            "trade_filter.zigzag.enabled is false; remove triggers or enable ZigZag"
        )


def _validate_zigzag_required_blocks(
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> bool:
    zigzag_present = ("trade_filter", "zigzag") in raw_user_keys
    lifecycle_present = ("trade_filter", "lifecycle") in raw_user_keys

    if not zigzag_present:
        errors.append(
            "trade_filter.zigzag block is required when trade_filter.zigzag.enabled is true"
        )
    if not lifecycle_present:
        errors.append(
            "trade_filter.lifecycle block is required when trade_filter.zigzag.enabled is true"
        )
    return zigzag_present and lifecycle_present


def _validate_subfilter_legality_dispatch(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    if not is_trade_filter_enabled(tf):
        return

    if _has_malformed_subfilter_enabled_flags(tf, errors, raw_user_keys):
        return

    zigzag_enabled = _is_zigzag_enabled_for_validation(tf, raw_user_keys)
    volume_enabled = is_volume_enabled(tf)

    if zigzag_enabled and not volume_enabled:
        if not _validate_zigzag_required_blocks(errors, raw_user_keys):
            return
        _validate_zigzag_block(tf, errors, raw_user_keys)
        _validate_lifecycle_block(tf, errors, raw_user_keys)
        _validate_triggers_block(tf, errors, raw_user_keys)
        _validate_time_filter_block(tf, errors, raw_user_keys)
        _validate_wakeup_regime_block(tf, errors, raw_user_keys)
        _validate_phase0_mode_d_cross_fields(tf, errors, raw_user_keys)
    elif zigzag_enabled and volume_enabled:
        if not _validate_zigzag_required_blocks(errors, raw_user_keys):
            return
        _validate_zigzag_block(tf, errors, raw_user_keys)
        _validate_lifecycle_block(tf, errors, raw_user_keys)
        _validate_triggers_block(tf, errors, raw_user_keys)
        _validate_time_filter_block(tf, errors, raw_user_keys)
        _validate_volume_block(tf, errors, raw_user_keys)
        _validate_wakeup_regime_block(tf, errors, raw_user_keys)
        _validate_phase0_mode_d_cross_fields(tf, errors, raw_user_keys)
    elif not zigzag_enabled and volume_enabled:
        _validate_standalone_volume_legality(tf, errors, raw_user_keys)
        if ("trade_filter", "lifecycle") in raw_user_keys:
            _validate_lifecycle_block(tf, errors, raw_user_keys)
        _validate_time_filter_block(tf, errors, raw_user_keys)
        _validate_volume_block(tf, errors, raw_user_keys)
        _validate_wakeup_regime_block(tf, errors, raw_user_keys)
        _validate_phase0_mode_d_cross_fields(tf, errors, raw_user_keys)
    else:
        errors.append(
            "at least one subfilter must be enabled: set "
            "trade_filter.zigzag.enabled: true or trade_filter.volume.enabled: true"
        )


def validate_trade_filter(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
    caller_pipeline: str = "wf_grid",
    error_keys: Optional[list[str]] = None,
) -> None:
    """Validate the trade_filter block.

    Push-style: appends error strings to ``errors``. Caller raises ConfigError
    if ``errors`` is non-empty after the call. Returns None.
    """
    token = _VALIDATION_ERROR_KEYS.set(error_keys)
    try:
        should_continue = _validate_root_enabled_and_type(tf, errors, raw_user_keys)
        if should_continue:
            _validate_subfilter_legality_dispatch(tf, errors, raw_user_keys)
        _validate_caller_pipeline_gate(tf, errors, raw_user_keys, caller_pipeline)
    finally:
        _VALIDATION_ERROR_KEYS.reset(token)


# ---------------------------------------------------------------------------
# Mode resolution helper (WP-V3-2, ТЗ v3 §3.1, §5)
#
# Kept in the config layer (not in zigzag_st_filter.py) so that the config
# loader and build_zigzag_global_stats both import from the same config-layer
# module without the loader depending on the runtime filter module.
# ---------------------------------------------------------------------------

def resolve_zigzag_mode(
    mode_raw: Optional[str],
    triggers_cfg: object,
) -> str:
    """Resolve the effective ZigZag mode string from config values.

    Priority:
    1. Explicit ``mode`` (canonical v3 config) → use as-is.
    2. No explicit mode + *triggers_cfg is not None* → legacy mapping:
       - ct=True,  cm=False → "A"
       - ct=False, cm=True  → "B"
       - ct=True,  cm=True  → "A+B"
       - ct=False, cm=False → "A" (defensive; validator rejects this)
    3. No explicit mode + *triggers_cfg is None* → default "A".

    **Important:** This function treats any non-None ``triggers_cfg`` as
    "triggers block was explicitly present".  In the production path the
    caller (``_resolve_trade_filter_mode_in_place`` in the loader) is
    responsible for passing ``None`` when the triggers block was absent from
    the YAML, using ``raw_user_keys`` presence information.

    Direct / duck-typed callers (e.g. test fixtures) that do not go through
    the loader must either:
    - set ``mode`` explicitly on the ZigZag config, **or**
    - pass ``triggers_cfg=None`` to get the "no triggers → A" default.

    Parameters
    ----------
    mode_raw:
        Value of ``zigzag.mode`` from the config.  ``None`` means absent.
    triggers_cfg:
        The triggers sub-config object (duck-typed), or ``None`` when absent.
        Uses ``getattr`` for duck-type safety.
    """
    if mode_raw is not None:
        return str(mode_raw)

    if triggers_cfg is None:
        return "A"

    ct_obj = getattr(triggers_cfg, "candidate_threshold", None)
    cm_obj = getattr(triggers_cfg, "confirmed_median", None)
    ct_enabled = bool(getattr(ct_obj, "enabled", True))
    cm_enabled = bool(getattr(cm_obj, "enabled", True))

    if ct_enabled and not cm_enabled:
        return "A"
    if not ct_enabled and cm_enabled:
        return "B"
    if ct_enabled and cm_enabled:
        return "A+B"
    return "A"  # both disabled — validator should have rejected; defensive


def resolve_trade_filter_mode_in_place(
    trade_filter_config: Optional[TradeFilterConfig],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Materialize the effective v3 ZigZag mode on a TradeFilterConfig.

    Shared by WF Grid and Tester after validation. This preserves A3:
    absent ``zigzag.mode`` plus absent legacy ``triggers`` resolves to Mode A,
    even though dataclass defaults still create trigger toggle objects.
    """
    if trade_filter_config is None or not trade_filter_config.enabled:
        return

    zz = trade_filter_config.zigzag
    if zz.mode is not None:
        return

    triggers_present = ("trade_filter", "triggers") in raw_user_keys
    triggers_cfg = trade_filter_config.triggers if triggers_present else None
    zz.mode = resolve_zigzag_mode(None, triggers_cfg)


def resolve_exit_off_mode_in_place(
    trade_filter_config: Optional[TradeFilterConfig],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Materialize default exit-off mode when the YAML key is absent.

    When ``trade_filter.lifecycle.exit_off_mode`` is not present in the parsed
    YAML, set ``lifecycle.exit_off_mode`` to ``\"exit A\"`` so runtime and
    tests see a single resolved value (docs/plan_exit_off_modes.txt §10).
    """
    if trade_filter_config is None or not trade_filter_config.enabled:
        return
    eom_key = ("trade_filter", "lifecycle", "exit_off_mode")
    if eom_key not in raw_user_keys:
        trade_filter_config.lifecycle.exit_off_mode = "exit A"


def resolve_exit_b_immediate_off_in_place(
    trade_filter_config: Optional[TradeFilterConfig],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Materialize default exit_b_immediate_off when the YAML key is absent.

    When ``trade_filter.lifecycle.exit_b_immediate_off`` is not present in the
    parsed YAML, set it to ``False``. If the key is present, leave it unchanged
    (the validator already confirmed it is bool).

    Must be called strictly after ``validate_trade_filter`` and after
    ``resolve_exit_off_mode_in_place`` (§3.4).
    """
    if trade_filter_config is None or not trade_filter_config.enabled:
        return
    imm_key = ("trade_filter", "lifecycle", "exit_b_immediate_off")
    if imm_key not in raw_user_keys:
        trade_filter_config.lifecycle.exit_b_immediate_off = False


def resolve_time_filter_in_place(
    trade_filter_config: Optional[TradeFilterConfig],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Materialise parsed time-window fields on TradeFilterTimeFilterConfig.

    When ``time_filter.enabled=True``, parses the validated ``window`` string
    (format ``HH:MM-HH:MM``) and stores ``_start_hour``, ``_start_minute``,
    ``_end_hour``, ``_end_minute`` directly on the config object.  Runtime
    components (``_infer_time_filter_events``) read these fields instead of
    re-parsing the string each call.

    No-op when:
    - ``trade_filter_config`` is None
    - ``trade_filter.enabled`` is False
    - ``time_filter.enabled`` is False or not bool

    Must be called strictly after ``validate_trade_filter`` (validator
    guarantees ``window`` is a valid ``HH:MM-HH:MM`` string when enabled).

    docs/time_filter_plan_v1_final.txt §2.2
    """
    if trade_filter_config is None or not trade_filter_config.enabled:
        return
    tfl = trade_filter_config.time_filter
    if not isinstance(tfl.enabled, bool) or not tfl.enabled:
        return
    # Validator guarantees window is non-None and matches HH:MM-HH:MM
    w: str = tfl.window  # type: ignore[assignment]
    start_str, end_str = w[:5], w[6:]
    tfl._start_hour = int(start_str[:2])
    tfl._start_minute = int(start_str[3:])
    tfl._end_hour = int(end_str[:2])
    tfl._end_minute = int(end_str[3:])


def resolve_volume_baseline_session_in_place(
    trade_filter_config: Optional[TradeFilterConfig],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Materialise parsed volume baseline-session window fields after validation.

    When ``trade_filter.volume.baseline_session.enabled=True``, parses the
    validated ``window`` string (format ``HH:MM-HH:MM``) and stores
    ``_start_hour``, ``_start_minute``, ``_end_hour``, ``_end_minute`` on the
    baseline-session config object.

    Must be called after ``validate_trade_filter``.
    """
    if trade_filter_config is None or not trade_filter_config.enabled:
        return
    volume = trade_filter_config.volume
    if volume is None or not isinstance(volume.enabled, bool) or not volume.enabled:
        return
    baseline_session = volume.baseline_session
    if (
        not isinstance(baseline_session.enabled, bool)
        or not baseline_session.enabled
    ):
        return
    # Validator guarantees window is non-None and matches HH:MM-HH:MM
    w: str = baseline_session.window  # type: ignore[assignment]
    start_str, end_str = w[:5], w[6:]
    baseline_session._start_hour = int(start_str[:2])
    baseline_session._start_minute = int(start_str[3:])
    baseline_session._end_hour = int(end_str[:2])
    baseline_session._end_minute = int(end_str[3:])


__all__ = [
    "TradeFilterConfig",
    "TradeFilterZigZagConfig",
    "TradeFilterCandidateDurationGateConfig",
    "TradeFilterTriggersConfig",
    "TradeFilterTriggerToggleConfig",
    "TradeFilterLifecycleConfig",
    "TradeFilterDiagnosticsConfig",
    "TradeFilterTimeFilterConfig",
    "TradeFilterBaselineSessionConfig",
    "TradeFilterVolumeConfig",
    "TradeFilterWakeupRegimeConfig",
    "TradeFilterWakeupEntryConfig",
    "TradeFilterWakeupCandidateHeightConfig",
    "TradeFilterWakeupCandidateAgeConfig",
    "TradeFilterWakeupAtrExpansionConfig",
    "TradeFilterWakeupVolumeExpansionConfig",
    "TradeFilterWakeupExitConfig",
    "TradeFilterWakeupTtlExitConfig",
    "TradeFilterWakeupNoFreshCandidateExitConfig",
    "TradeFilterWakeupMaxTradesPerCycleConfig",
    "TradeFilterWakeupCycleTakeProfitExitConfig",
    "TradeFilterWakeupLocalMedianStopExitConfig",
    "TradeFilterWakeupExitActionConfig",
    "TradeFilterWakeupPositionFreezeConfig",
    "validate_trade_filter",
    "collect_raw_user_keys",
    "collect_trade_filter_unknown_keys",
    "build_trade_filter_config_from_raw",
    "is_trade_filter_enabled",
    "is_zigzag_enabled",
    "is_volume_enabled",
    "is_wakeup_volume_enabled",
    "resolve_zigzag_enabled_in_place",
    "resolve_volume_enabled_in_place",
    "resolve_volume_defaults_in_place",
    "TRADE_FILTER_ALLOWED_KEYS",
    "resolve_zigzag_mode",
    "resolve_trade_filter_mode_in_place",
    "resolve_exit_off_mode_in_place",
    "resolve_exit_b_immediate_off_in_place",
    "resolve_time_filter_in_place",
    "resolve_volume_baseline_session_in_place",
    # Constant whitelists exported for tests / CLI gate logic
    "_LIFECYCLE_STOP_CHECK_VALUES",
    "_LIFECYCLE_STOPPING_EXIT_VALUES",
    "_SUPPORTED_TRADE_FILTER_TYPES",
    "_CALLER_PIPELINE_DOMAIN",
    "_ZIGZAG_ST_MODE_ALLOWED_CALLERS",
    "_VALID_ZIGZAG_MODES",
    "_V3_INIT_FAILURE_KEYS",
]
