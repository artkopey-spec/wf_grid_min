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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Lifecycle / type literal whitelists (Appendix A v1.1 §11)
# ---------------------------------------------------------------------------

_LIFECYCLE_STOP_CHECK_VALUES = {"confirm_bar_only"}
_LIFECYCLE_STOPPING_EXIT_VALUES = {"opposite_st_flip"}
_SUPPORTED_TRADE_FILTER_TYPES = {"zigzag_st_mode"}

# WP-T3 step 6 — caller_pipeline whitelist for type=zigzag_st_mode.
# Plan §5.5 (mode-rejection gate, owner-approved per audit-fix v0.5).
# Domain = {wf_grid, tester, optimizer, single}; whitelist = {wf_grid, tester}.
# Any caller outside the whitelist invoking enabled+zigzag_st_mode is rejected.
_CALLER_PIPELINE_DOMAIN = frozenset({"wf_grid", "tester", "optimizer", "single"})
_ZIGZAG_ST_MODE_ALLOWED_CALLERS = frozenset({"wf_grid", "tester"})


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
class TradeFilterZigZagConfig:
    """ZigZag parameters for the trade filter.

    IMPORTANT (plan §6.3): candidate_trigger_threshold and candidate_trigger_quantile
    MUST default to None.  The YAML template shows 0.80 only as an example.
    If either default were a concrete number, raw-key presence tracking (§6.4.1)
    would be unable to distinguish "user supplied the key" from "dataclass default"
    and the numeric-threshold + explicit-quantile reject rule (§11.3) would
    malfunction.
    """
    global_stats_source: str = "full_dataset"
    leg_height_mode: str = "pct"
    reversal_threshold: Optional[float] = None          # required when enabled; numeric fraction
    candidate_trigger_threshold: Union[float, str, None] = None  # numeric fraction | "auto" | None
    candidate_trigger_quantile: Optional[float] = None  # MUST stay None; §6.3 invariant
    global_median: str = "auto"
    local_window: int = 5                               # integer >= 1
    daily_reset: bool = False                           # calendar-day reset of ZigZag+FSM (plan v3 §2.1)


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


@dataclass
class TradeFilterDiagnosticsConfig:
    """Optional diagnostics export flags (§13)."""
    export_state_columns: bool = True
    export_trigger_columns: bool = True


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


# ---------------------------------------------------------------------------
# Strict schema (allowed keys per dotted path) for the trade_filter subtree
#
# Spec reference: Appendix A v1.1 §11; plan Phase 2 §5.3.1
# ---------------------------------------------------------------------------

TRADE_FILTER_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "trade_filter": frozenset({
        "enabled", "type", "zigzag", "triggers", "lifecycle", "diagnostics",
    }),
    "trade_filter.zigzag": frozenset({
        "global_stats_source", "leg_height_mode", "reversal_threshold",
        "candidate_trigger_threshold", "candidate_trigger_quantile",
        "global_median", "local_window", "daily_reset",
    }),
    "trade_filter.triggers": frozenset({"candidate_threshold", "confirmed_median"}),
    "trade_filter.triggers.candidate_threshold": frozenset({"enabled"}),
    "trade_filter.triggers.confirmed_median": frozenset({"enabled"}),
    "trade_filter.lifecycle": frozenset({
        "freeze_confirmed_legs", "stop_check", "stopping_exit",
    }),
    "trade_filter.diagnostics": frozenset({
        "export_state_columns", "export_trigger_columns",
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
            if isinstance(child, dict) and child_path in TRADE_FILTER_ALLOWED_KEYS:
                errors.extend(collect_trade_filter_unknown_keys(child, child_path))
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
    zigzag = TradeFilterZigZagConfig(
        global_stats_source=zigzag_raw.get("global_stats_source", "full_dataset"),
        leg_height_mode=zigzag_raw.get("leg_height_mode", "pct"),
        reversal_threshold=zigzag_raw.get("reversal_threshold", None),
        candidate_trigger_threshold=zigzag_raw.get("candidate_trigger_threshold", None),
        candidate_trigger_quantile=zigzag_raw.get("candidate_trigger_quantile", None),
        global_median=zigzag_raw.get("global_median", "auto"),
        local_window=zigzag_raw.get("local_window", 5),
        daily_reset=zigzag_raw.get("daily_reset", False),
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
    )

    diag_raw: dict = tf_raw.get("diagnostics") or {}
    diagnostics = TradeFilterDiagnosticsConfig(
        export_state_columns=bool(diag_raw.get("export_state_columns", True)),
        export_trigger_columns=bool(diag_raw.get("export_trigger_columns", True)),
    )

    return TradeFilterConfig(
        enabled=enabled_raw,
        type=tf_type,
        zigzag=zigzag,
        triggers=triggers,
        lifecycle=lifecycle,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_trade_filter(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
    caller_pipeline: str = "wf_grid",
) -> None:
    """Validate the trade_filter block per Appendix A v1.1 §11–§11.3.

    Push-style: appends error strings to ``errors``. Caller raises ConfigError
    if ``errors`` is non-empty after the call. Returns None.

    Parameters
    ----------
    tf:
        Already-built TradeFilterConfig (raw values preserved for absent /
        wrong-type keys; see _build_trade_filter_config in loader).
    errors:
        Mutable list to append error strings to.
    raw_user_keys:
        Set of dotted-path tuples explicitly present in the parsed YAML.
        Required by plan §6.4.1 (numeric-threshold + explicit-quantile reject).
    caller_pipeline:
        "wf_grid" (Phase 1) or "tester" (Phase 2). Reserved for per-pipeline
        log prefixes; does NOT change validation rules. Default "wf_grid"
        preserves Phase 1 backward compatibility.

    All Appendix A v1.1 §11.x rules are implemented; the one non-rule is:
        freeze_confirmed_legs < local_window is VALID (§3.2, §17.20) — no reject,
        no warning.

    Spec reference: Appendix A v1.1 §11, §11.1, §11.2, §11.3, §15.6, §17.2
    Plan reference: Phase 1 plan §6.4.1, §6.5; Phase 2 plan §5.1, §5.3, §5.5
    """
    # ------------------------------------------------------------------
    # WP-T3 step 6 — caller_pipeline domain check
    # ------------------------------------------------------------------
    if caller_pipeline not in _CALLER_PIPELINE_DOMAIN:
        errors.append(
            f"validate_trade_filter: unknown caller_pipeline {caller_pipeline!r}; "
            f"supported: {sorted(_CALLER_PIPELINE_DOMAIN)}"
        )
        return

    # ------------------------------------------------------------------
    # Rule: enabled key presence and type
    # ------------------------------------------------------------------
    enabled_key = ("trade_filter", "enabled")
    if enabled_key not in raw_user_keys:
        errors.append(
            "trade_filter.enabled is required when trade_filter block is present"
        )
        return  # cannot infer intent without enabled — stop here

    if not isinstance(tf.enabled, bool):
        errors.append(
            f"trade_filter.enabled must be bool (true/false), "
            f"got {type(tf.enabled).__name__!r} ({tf.enabled!r})"
        )
        return

    # ------------------------------------------------------------------
    # Rule: type validation
    # ------------------------------------------------------------------
    type_present = ("trade_filter", "type") in raw_user_keys

    if tf.enabled:
        # enabled=true: type required and must be zigzag_st_mode
        if not type_present or tf.type is None:
            errors.append(
                "trade_filter.type is required when trade_filter.enabled is true; "
                "set type: zigzag_st_mode"
            )
        elif tf.type not in _SUPPORTED_TRADE_FILTER_TYPES:
            errors.append(
                f"trade_filter.type {tf.type!r} is not supported; "
                f"supported: {sorted(_SUPPORTED_TRADE_FILTER_TYPES)}"
            )
        elif (
            tf.type == "zigzag_st_mode"
            and caller_pipeline not in _ZIGZAG_ST_MODE_ALLOWED_CALLERS
        ):
            # WP-T3 step 6 — pipeline whitelist (plan §5.5).
            errors.append(
                f"trade_filter.type='zigzag_st_mode' is not supported in "
                f"pipeline {caller_pipeline!r}; allowed pipelines: "
                f"{sorted(_ZIGZAG_ST_MODE_ALLOWED_CALLERS)}"
            )
            # Hard stop: downstream rules assume the filter is acceptable for
            # this caller.
            return
    else:
        # enabled=false: type optional; if present, must be zigzag_st_mode (§11.1)
        if type_present and tf.type is not None and tf.type not in _SUPPORTED_TRADE_FILTER_TYPES:
            errors.append(
                f"trade_filter.type {tf.type!r} is not supported for disabled filter; "
                f"use zigzag_st_mode or omit type"
            )
        # disabled filter: skip all further validation (§11.1)
        return

    # ------------------------------------------------------------------
    # enabled=true: required sub-blocks
    # ------------------------------------------------------------------
    zigzag_present = ("trade_filter", "zigzag") in raw_user_keys
    triggers_present = ("trade_filter", "triggers") in raw_user_keys
    lifecycle_present = ("trade_filter", "lifecycle") in raw_user_keys

    if not zigzag_present:
        errors.append(
            "trade_filter.zigzag block is required when trade_filter.enabled is true"
        )
    if not triggers_present:
        errors.append(
            "trade_filter.triggers block is required when trade_filter.enabled is true"
        )
    if not lifecycle_present:
        errors.append(
            "trade_filter.lifecycle block is required when trade_filter.enabled is true"
        )

    # Avoid cascade errors if critical blocks are missing
    if not zigzag_present or not triggers_present or not lifecycle_present:
        return

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # ZigZag block
    # ------------------------------------------------------------------
    zz = tf.zigzag

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

    # NOTE: freeze_confirmed_legs < local_window is VALID — not a warning, not a
    # reject (Appendix A v1.1 §3.2, §17.20; plan §6.5 Note 1).  No check here.


__all__ = [
    "TradeFilterConfig",
    "TradeFilterZigZagConfig",
    "TradeFilterTriggersConfig",
    "TradeFilterTriggerToggleConfig",
    "TradeFilterLifecycleConfig",
    "TradeFilterDiagnosticsConfig",
    "validate_trade_filter",
    "collect_raw_user_keys",
    "collect_trade_filter_unknown_keys",
    "build_trade_filter_config_from_raw",
    "TRADE_FILTER_ALLOWED_KEYS",
    # Constant whitelists exported for tests / CLI gate logic
    "_LIFECYCLE_STOP_CHECK_VALUES",
    "_LIFECYCLE_STOPPING_EXIT_VALUES",
    "_SUPPORTED_TRADE_FILTER_TYPES",
    "_CALLER_PIPELINE_DOMAIN",
    "_ZIGZAG_ST_MODE_ALLOWED_CALLERS",
]
