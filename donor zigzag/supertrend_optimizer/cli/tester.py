"""
CLI entrypoint for SuperTrend Tester.

Usage:
    python -m supertrend_optimizer.cli.tester --csv data.csv --atr 14 --mult 3.0 --mode revers --out result.xlsx
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

from supertrend_optimizer.data.loader import load_ohlc_csv
from supertrend_optimizer.data.validator import validate_ohlc_data, validate_filter_columns
from supertrend_optimizer.data.timeframe import (
    coerce_annualization_config_value,
    detect_timeframe,
    resolve_periods_per_year_from_config,
    validate_market_vs_timeframe,
)
from supertrend_optimizer.io.excel_tester import export_tester_results, export_equal_blocks_results
from supertrend_optimizer.testing.runner import run_all_periods, run_equal_blocks
from supertrend_optimizer.testing.signal_events import build_signal_events
from supertrend_optimizer.utils.config import load_config
from supertrend_optimizer.utils.enums import MarketType, ExecutionModel
from supertrend_optimizer.utils.constants import FILTER_MODES
from supertrend_optimizer.utils.exceptions import ConfigError, DataValidationError
from supertrend_optimizer.utils.warmup import calculate_warmup_tester

# Legacy Excel export: trades with bars_held < N appear on the ``false start`` sheet.
DEFAULT_FALSE_START_MAX_BARS = 4


def _validate_false_start_max_bars(raw: Any) -> int:
    """
    Parse ``export.false_start_max_bars`` from YAML.

    Raises:
        ConfigError: if the value is not an integer >= 1.
    """
    if raw is None:
        raise ConfigError(
            "export.false_start_max_bars must be an integer >= 1, got: null"
        )
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ConfigError(
            "export.false_start_max_bars must be an integer >= 1, "
            f"got type {type(raw).__name__!r} with value {raw!r}"
        )
    if raw < 1:
        raise ConfigError(
            f"export.false_start_max_bars must be an integer >= 1, got: {raw!r}"
        )
    return raw


def _merge_export_config(loaded_config: Dict[str, Any], config: Dict[str, Any]) -> None:
    """Update ``config['export']`` from ``loaded_config['export']`` (in-place)."""
    export_raw = loaded_config.get("export")
    if export_raw is None:
        return
    if not isinstance(export_raw, dict):
        raise ConfigError(
            "export must be a mapping (e.g. {false_start_max_bars: 4}), "
            f"got {type(export_raw).__name__!r}"
        )
    if "false_start_max_bars" in export_raw:
        config["export"]["false_start_max_bars"] = _validate_false_start_max_bars(
            export_raw["false_start_max_bars"]
        )


def _validate_amp_subfield(name: str, val: Any, *, kind: str, lo: Any, hi: Any) -> Any:
    """
    Validate a single ``filters.amplitude.*`` field.

    kind: "int" | "float_pos" | "float_frac" (0 < v < 1) | "int_or_null"
    lo/hi: inclusive bounds (ignored for float_frac).
    Returns the coerced value.
    Raises ConfigError on violation.
    """
    if kind == "int":
        if isinstance(val, bool) or not isinstance(val, int):
            raise ConfigError(f"{name} must be an integer, got {type(val).__name__!r}: {val!r}")
        if not (lo <= val <= hi):
            raise ConfigError(f"{name} must be in [{lo}, {hi}], got: {val!r}")
        return int(val)
    if kind == "float_frac":
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ConfigError(f"{name} must be a float in (0, 1), got {type(val).__name__!r}")
        v = float(val)
        if not (0.0 < v < 1.0):
            raise ConfigError(f"{name} must be in (0, 1), got: {val!r}")
        return v
    if kind == "float_pos":
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ConfigError(f"{name} must be a float >= 0, got {type(val).__name__!r}: {val!r}")
        v = float(val)
        if v < 0.0:
            raise ConfigError(f"{name} must be >= 0, got: {val!r}")
        return v
    if kind == "int_or_null":
        if val is None:
            return None
        if isinstance(val, bool) or not isinstance(val, int):
            raise ConfigError(
                f"{name} must be an integer in [{lo}, {hi}] or null, "
                f"got {type(val).__name__!r}: {val!r}"
            )
        if not (lo <= val <= hi):
            raise ConfigError(f"{name} must be in [{lo}, {hi}] or null, got: {val!r}")
        return int(val)
    raise AssertionError(f"Unknown kind: {kind!r}")


def _validate_zigzag_section(raw_zz: Any) -> Dict[str, Any]:
    """
    Parse and validate ``filters.zigzag`` (RFC v3.1 §6.3, §6.4, §6.5, §6.6).

    Field ranges (hard):
        reversal_threshold:            float in [0.0001, 0.1], default 0.005
        min_legs_global:               int   in [10, 500],     default 50
        q_strong:                      float in (0, 1),        deprecated, default 0.80
        k_local:                       int   in [2, 50],       default 5
        entry_side:                    str ∈ {"counter_trend"}, default "counter_trend"
        arm_timeout_bars_since_extreme int   in [1, 10000],    default 24
        arm_timeout_bars_hard          int   in [1, 10000],    default 78
        structural_reset_min_span      int   in [1, 1000],     default 3   (NEW §5.8)
        readiness                      dict                    default see §6.5 (NEW)

    Cross-field:
        arm_timeout_bars_hard >= arm_timeout_bars_since_extreme.

    Strict schema (§6.4 r.7): unknown keys at any level → ConfigError.

    Raises:
        ConfigError on any violation.
    """
    import warnings as _warnings_zz

    if raw_zz is None:
        raw_zz = {}
    if not isinstance(raw_zz, dict):
        raise ConfigError(
            f"filters.zigzag must be a mapping, got {type(raw_zz).__name__!r}"
        )

    # Strict schema at filters.zigzag level (RFC v3.1 §6.4 r.7)
    _KNOWN_ZZ_KEYS = frozenset({
        "reversal_threshold",
        "min_legs_global",
        "q_strong",
        "k_local",
        "entry_side",
        "arm_timeout_bars_since_extreme",
        "arm_timeout_bars_hard",
        "structural_reset_min_span",
        "readiness",
    })
    for _k in raw_zz:
        if _k not in _KNOWN_ZZ_KEYS:
            raise ConfigError(
                f"filters.zigzag: unknown key {_k!r}. "
                f"Allowed keys: {sorted(_KNOWN_ZZ_KEYS)}"
            )

    # reversal_threshold — optional, default 0.005 (RFC v3.1)
    rt_raw = raw_zz.get("reversal_threshold", 0.005)
    if isinstance(rt_raw, bool) or not isinstance(rt_raw, (int, float)):
        raise ConfigError(
            f"filters.zigzag.reversal_threshold must be a float, "
            f"got {type(rt_raw).__name__!r}: {rt_raw!r}"
        )
    rt = float(rt_raw)
    if not (1e-4 <= rt <= 0.1):
        raise ConfigError(
            f"filters.zigzag.reversal_threshold must be in [0.0001, 0.1], got: {rt!r}"
        )

    # min_legs_global
    mlg_raw = raw_zz.get("min_legs_global", 50)
    if isinstance(mlg_raw, bool) or not isinstance(mlg_raw, int):
        raise ConfigError(
            f"filters.zigzag.min_legs_global must be an integer, "
            f"got {type(mlg_raw).__name__!r}: {mlg_raw!r}"
        )
    if not (10 <= mlg_raw <= 500):
        raise ConfigError(
            f"filters.zigzag.min_legs_global must be in [10, 500], got: {mlg_raw!r}"
        )

    # q_strong — deprecated (§6.4 r.4); still validated if present (fix N-05)
    _q_strong_explicit = "q_strong" in raw_zz
    q_raw = raw_zz.get("q_strong", 0.80)
    if isinstance(q_raw, bool) or not isinstance(q_raw, (int, float)):
        raise ConfigError(
            f"filters.zigzag.q_strong must be a float in (0, 1), "
            f"got {type(q_raw).__name__!r}: {q_raw!r}"
        )
    q = float(q_raw)
    if not (0.0 < q < 1.0):
        raise ConfigError(
            f"filters.zigzag.q_strong must be in (0, 1), got: {q!r}"
        )

    # k_local
    k_raw = raw_zz.get("k_local", 5)
    if isinstance(k_raw, bool) or not isinstance(k_raw, int):
        raise ConfigError(
            f"filters.zigzag.k_local must be an integer, "
            f"got {type(k_raw).__name__!r}: {k_raw!r}"
        )
    if not (2 <= k_raw <= 50):
        raise ConfigError(
            f"filters.zigzag.k_local must be in [2, 50], got: {k_raw!r}"
        )

    # entry_side
    es_raw = raw_zz.get("entry_side", "counter_trend")
    if not isinstance(es_raw, str):
        raise ConfigError(
            f"filters.zigzag.entry_side must be a string, got: {es_raw!r}"
        )
    if es_raw != "counter_trend":
        raise ConfigError(
            f"filters.zigzag.entry_side must be 'counter_trend' (only value "
            f"supported; 'pullback' is reserved), got: {es_raw!r}"
        )

    # arm_timeout_bars_since_extreme
    ate_raw = raw_zz.get("arm_timeout_bars_since_extreme", 24)
    if isinstance(ate_raw, bool) or not isinstance(ate_raw, int):
        raise ConfigError(
            f"filters.zigzag.arm_timeout_bars_since_extreme must be an integer, "
            f"got {type(ate_raw).__name__!r}: {ate_raw!r}"
        )
    if not (1 <= ate_raw <= 10000):
        raise ConfigError(
            f"filters.zigzag.arm_timeout_bars_since_extreme must be in [1, 10000], "
            f"got: {ate_raw!r}"
        )

    # arm_timeout_bars_hard
    ath_raw = raw_zz.get("arm_timeout_bars_hard", 78)
    if isinstance(ath_raw, bool) or not isinstance(ath_raw, int):
        raise ConfigError(
            f"filters.zigzag.arm_timeout_bars_hard must be an integer, "
            f"got {type(ath_raw).__name__!r}: {ath_raw!r}"
        )
    if not (1 <= ath_raw <= 10000):
        raise ConfigError(
            f"filters.zigzag.arm_timeout_bars_hard must be in [1, 10000], "
            f"got: {ath_raw!r}"
        )

    # Cross-field: hard timeout >= since-extreme timeout
    if ath_raw < ate_raw:
        raise ConfigError(
            f"filters.zigzag.arm_timeout_bars_hard ({ath_raw}) must be >= "
            f"arm_timeout_bars_since_extreme ({ate_raw})"
        )

    # structural_reset_min_span (RFC v3.1 §5.8, §6.4 r.6)
    srs_raw = raw_zz.get("structural_reset_min_span", 3)
    if isinstance(srs_raw, bool) or not isinstance(srs_raw, int):
        raise ConfigError(
            f"filters.zigzag.structural_reset_min_span must be an integer, "
            f"got {type(srs_raw).__name__!r}: {srs_raw!r}"
        )
    if not (1 <= srs_raw <= 1000):
        raise ConfigError(
            f"filters.zigzag.structural_reset_min_span must be in [1, 1000], "
            f"got: {srs_raw!r}"
        )

    # readiness block (RFC v3.1 §6.3, §6.4, §6.5, §6.6)
    readiness_raw = raw_zz.get("readiness", None)

    if readiness_raw is None:
        # No readiness block → migration defaults (§6.4 rule 1)
        if _q_strong_explicit:
            _warnings_zz.warn(
                "filters.zigzag.q_strong is deprecated. "
                "Migrate to filters.zigzag.readiness.contour_a.p80_quantile. "
                "q_strong will be removed in a future release.",
                DeprecationWarning,
                stacklevel=4,
            )
        readiness_cfg: Dict[str, Any] = {
            "contour_a": {
                "enabled": True,
                "p80_quantile": q,  # from q_strong or default 0.80
            },
            "contour_b": {
                "enabled": False,
                "local_k": 5,
                "open_ratio": 1.5,
                "close_ratio": 1.0,
            },
        }
    else:
        if not isinstance(readiness_raw, dict):
            raise ConfigError(
                f"filters.zigzag.readiness must be a mapping, "
                f"got {type(readiness_raw).__name__!r}"
            )
        # Strict schema at readiness level (§6.4 r.7)
        _KNOWN_READINESS_KEYS = frozenset({"contour_a", "contour_b"})
        for _k in readiness_raw:
            if _k not in _KNOWN_READINESS_KEYS:
                raise ConfigError(
                    f"filters.zigzag.readiness: unknown key {_k!r}. "
                    f"Allowed keys: {sorted(_KNOWN_READINESS_KEYS)}"
                )

        # --- contour_a ---
        ca_raw = readiness_raw.get("contour_a", {}) or {}
        if not isinstance(ca_raw, dict):
            raise ConfigError(
                f"filters.zigzag.readiness.contour_a must be a mapping, "
                f"got {type(ca_raw).__name__!r}"
            )
        _KNOWN_CA_KEYS = frozenset({"enabled", "p80_quantile"})
        for _k in ca_raw:
            if _k not in _KNOWN_CA_KEYS:
                raise ConfigError(
                    f"filters.zigzag.readiness.contour_a: unknown key {_k!r}. "
                    f"Allowed keys: {sorted(_KNOWN_CA_KEYS)}"
                )

        ca_enabled_raw = ca_raw.get("enabled", True)
        if not isinstance(ca_enabled_raw, bool):
            raise ConfigError(
                f"filters.zigzag.readiness.contour_a.enabled must be a boolean, "
                f"got {type(ca_enabled_raw).__name__!r}: {ca_enabled_raw!r}"
            )

        _p80_explicit = "p80_quantile" in ca_raw
        if _p80_explicit:
            p80_raw = ca_raw["p80_quantile"]
            if isinstance(p80_raw, bool) or not isinstance(p80_raw, (int, float)):
                raise ConfigError(
                    f"filters.zigzag.readiness.contour_a.p80_quantile must be a float in (0, 1), "
                    f"got {type(p80_raw).__name__!r}: {p80_raw!r}"
                )
            p80 = float(p80_raw)
            if not (0.0 < p80 < 1.0):
                raise ConfigError(
                    f"filters.zigzag.readiness.contour_a.p80_quantile must be in (0, 1), "
                    f"got: {p80!r}"
                )
            # Ambiguity check: both q_strong and p80_quantile explicit and different (§6.4 r.3)
            if _q_strong_explicit and abs(p80 - q) > 1e-12:
                raise ConfigError(
                    f"filters.zigzag.q_strong ({q}) and "
                    f"filters.zigzag.readiness.contour_a.p80_quantile ({p80}) "
                    f"are both explicitly set but differ. "
                    f"Remove q_strong or set them to the same value."
                )
        else:
            # No p80_quantile → fall back to q_strong (§6.4 r.2)
            if _q_strong_explicit:
                _warnings_zz.warn(
                    "filters.zigzag.q_strong is deprecated. "
                    "Migrate to filters.zigzag.readiness.contour_a.p80_quantile. "
                    "q_strong will be removed in a future release.",
                    DeprecationWarning,
                    stacklevel=4,
                )
            p80 = q  # from q_strong or default 0.80

        # --- contour_b ---
        cb_raw = readiness_raw.get("contour_b", {}) or {}
        if not isinstance(cb_raw, dict):
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b must be a mapping, "
                f"got {type(cb_raw).__name__!r}"
            )
        _KNOWN_CB_KEYS = frozenset({"enabled", "local_k", "open_ratio", "close_ratio"})
        for _k in cb_raw:
            if _k not in _KNOWN_CB_KEYS:
                raise ConfigError(
                    f"filters.zigzag.readiness.contour_b: unknown key {_k!r}. "
                    f"Allowed keys: {sorted(_KNOWN_CB_KEYS)}"
                )

        cb_enabled_raw = cb_raw.get("enabled", False)
        if not isinstance(cb_enabled_raw, bool):
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b.enabled must be a boolean, "
                f"got {type(cb_enabled_raw).__name__!r}: {cb_enabled_raw!r}"
            )

        local_k_raw = cb_raw.get("local_k", 5)
        if isinstance(local_k_raw, bool) or not isinstance(local_k_raw, int):
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b.local_k must be an integer, "
                f"got {type(local_k_raw).__name__!r}: {local_k_raw!r}"
            )
        if local_k_raw < 1:
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b.local_k must be >= 1, "
                f"got: {local_k_raw!r}"
            )

        open_ratio_raw = cb_raw.get("open_ratio", 1.5)
        if isinstance(open_ratio_raw, bool) or not isinstance(open_ratio_raw, (int, float)):
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b.open_ratio must be a float, "
                f"got {type(open_ratio_raw).__name__!r}: {open_ratio_raw!r}"
            )
        open_ratio = float(open_ratio_raw)
        if open_ratio < 0.0:
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b.open_ratio must be >= 0, "
                f"got: {open_ratio!r}"
            )

        close_ratio_raw = cb_raw.get("close_ratio", 1.0)
        if isinstance(close_ratio_raw, bool) or not isinstance(close_ratio_raw, (int, float)):
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b.close_ratio must be a float, "
                f"got {type(close_ratio_raw).__name__!r}: {close_ratio_raw!r}"
            )
        close_ratio = float(close_ratio_raw)
        if close_ratio < 0.0:
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b.close_ratio must be >= 0, "
                f"got: {close_ratio!r}"
            )

        # Cross-field: open_ratio must be >= close_ratio (§6.6)
        if open_ratio < close_ratio:
            raise ConfigError(
                f"filters.zigzag.readiness.contour_b.open_ratio ({open_ratio}) "
                f"must be >= close_ratio ({close_ratio})"
            )

        # A=off AND B=off → warn (valid in tester, forbidden in optimizer §6.6)
        if not ca_enabled_raw and not cb_enabled_raw:
            _warnings_zz.warn(
                "filters.zigzag.readiness: both contour_a and contour_b are disabled "
                "(A=off, B=off). readiness_on will be always False. "
                "This is valid in tester/debug mode but forbidden in optimizer.",
                UserWarning,
                stacklevel=4,
            )

        readiness_cfg = {
            "contour_a": {
                "enabled": bool(ca_enabled_raw),
                "p80_quantile": p80,
            },
            "contour_b": {
                "enabled": bool(cb_enabled_raw),
                "local_k": int(local_k_raw),
                "open_ratio": open_ratio,
                "close_ratio": close_ratio,
            },
        }

    return {
        "reversal_threshold": rt,
        "min_legs_global": int(mlg_raw),
        "q_strong": q,
        "k_local": int(k_raw),
        "entry_side": es_raw,
        "arm_timeout_bars_since_extreme": int(ate_raw),
        "arm_timeout_bars_hard": int(ath_raw),
        "structural_reset_min_span": int(srs_raw),
        "readiness": readiness_cfg,
    }


def _validate_amplitude_section(raw_amp: Any, n: int) -> Dict[str, Any]:
    """
    Parse and validate ``filters.amplitude``.

    ``n`` must already be resolved (used for min_separation bounds check).
    """
    if not isinstance(raw_amp, dict):
        raise ConfigError(
            f"filters.amplitude must be a mapping, got {type(raw_amp).__name__!r}"
        )

    lookback = _validate_amp_subfield(
        "filters.amplitude.lookback", raw_amp.get("lookback", 500),
        kind="int", lo=200, hi=2000,
    )
    q = _validate_amp_subfield(
        "filters.amplitude.q", raw_amp.get("q", 0.60),
        kind="float_frac", lo=None, hi=None,
    )
    atr_period = _validate_amp_subfield(
        "filters.amplitude.atr_period", raw_amp.get("atr_period", 14),
        kind="int", lo=2, hi=500,
    )
    atr_floor = _validate_amp_subfield(
        "filters.amplitude.atr_floor", raw_amp.get("atr_floor", 0.0),
        kind="float_pos", lo=None, hi=None,
    )

    raw_sep = raw_amp.get("min_separation", None)
    if raw_sep is None:
        min_separation = None
    else:
        min_separation = _validate_amp_subfield(
            "filters.amplitude.min_separation", raw_sep,
            kind="int_or_null", lo=2, hi=n - 1,
        )

    return {
        "n": n,
        "min_separation": min_separation,
        "lookback": lookback,
        "q": q,
        "atr_period": atr_period,
        "atr_floor": atr_floor,
    }


def _validate_filters_config(raw: Any) -> Dict[str, Any]:
    """
    Parse and validate the ``filters`` section from YAML.

    Returns a fully-normalised dict with all sub-keys present.
    Absent section or ``None`` is normalised to ``mode: none``.

    Raises:
        ConfigError: on any semantic violation.
    """
    import warnings as _warnings

    _default_volatility: Dict[str, Any] = {"min_atr_pct": None, "max_atr_pct": None}
    _default_amplitude: Dict[str, Any] = {
        "n": 20,
        "min_separation": None,
        "lookback": 500,
        "q": 0.60,
        "atr_period": 14,
        "atr_floor": 0.0,
    }
    _default_volume: Dict[str, Any] = {
        "volume_column": "Volume",
        "volume_ma_column": "Volume MA",
        "min_ratio": None,
        "max_ratio": None,
    }
    _default_zigzag: Dict[str, Any] = {
        "reversal_threshold": 0.005,
        "min_legs_global": 50,
        "q_strong": 0.80,
        "k_local": 5,
        "entry_side": "counter_trend",
        "arm_timeout_bars_since_extreme": 24,
        "arm_timeout_bars_hard": 78,
        # RFC v3.1 §6.5 defaults
        "structural_reset_min_span": 3,
        "readiness": {
            "contour_a": {"enabled": True, "p80_quantile": 0.80},
            "contour_b": {"enabled": False, "local_k": 5, "open_ratio": 1.5, "close_ratio": 1.0},
        },
    }

    if raw is None:
        return {
            "mode": "none",
            "volatility": _default_volatility.copy(),
            "amplitude": _default_amplitude.copy(),
            "volume": _default_volume.copy(),
            "zigzag": _default_zigzag.copy(),
        }

    if not isinstance(raw, dict):
        raise ConfigError(
            f"filters must be a mapping, got {type(raw).__name__!r}"
        )

    mode = raw.get("mode", "none")
    if mode not in FILTER_MODES:
        raise ConfigError(
            f"filters.mode must be one of {sorted(FILTER_MODES)}, got: {mode!r}"
        )

    # --- Deprecation warnings for legacy modes (patch §G.3) ---
    if mode in ("volatility", "volatility_and_volume"):
        _warnings.warn(
            f"filters.mode {mode!r} is deprecated and will be removed in the next "
            f"release. Migrate to 'amplitude' (or 'amplitude_and_volume') with a "
            f"filters.amplitude section. See docs/amp_filter_migration.md.",
            DeprecationWarning,
            stacklevel=4,
        )

    # --- amplitude vs volatility in one YAML (relaxed §G.2) ---
    # Both ``volatility`` and ``amplitude`` blocks may be present; only the block
    # that matches ``mode`` is used by the engine. This lets users switch modes
    # without deleting the other preset from the file.
    has_amp_section = bool(raw.get("amplitude"))
    has_zz_section = bool(raw.get("zigzag"))

    if mode == "volume":
        if has_amp_section:
            raise ConfigError(
                "filters.amplitude section is forbidden when filters.mode == 'volume'."
            )
        if has_zz_section:
            raise ConfigError(
                "filters.zigzag section is forbidden when filters.mode == 'volume'."
            )

    # --- cross-section warnings (relaxed §G.2) ---
    if mode in ("zigzag", "zigzag_and_volume") and has_amp_section:
        _warnings.warn(
            f"filters.amplitude section is present but filters.mode == {mode!r}; "
            f"amplitude section is ignored in zigzag modes (plan §G.2 relaxed).",
            UserWarning,
            stacklevel=4,
        )
    if mode in ("amplitude", "amplitude_and_volume") and has_zz_section:
        _warnings.warn(
            f"filters.zigzag section is present but filters.mode == {mode!r}; "
            f"zigzag section is ignored in amplitude modes (plan §G.2 relaxed).",
            UserWarning,
            stacklevel=4,
        )

    # --- volatility sub-section ---
    vol_raw = raw.get("volatility", {}) or {}
    if not isinstance(vol_raw, dict):
        raise ConfigError(
            f"filters.volatility must be a mapping, got {type(vol_raw).__name__!r}"
        )

    min_atr_pct = vol_raw.get("min_atr_pct", None)
    max_atr_pct = vol_raw.get("max_atr_pct", None)

    for name, val in (("filters.volatility.min_atr_pct", min_atr_pct),
                      ("filters.volatility.max_atr_pct", max_atr_pct)):
        if val is not None:
            if isinstance(val, bool):
                raise ConfigError(f"{name} must be a float >= 0 or null, got bool: {val!r}")
            if not isinstance(val, (int, float)):
                raise ConfigError(
                    f"{name} must be a float >= 0 or null, got {type(val).__name__!r}: {val!r}"
                )
            if float(val) < 0:
                raise ConfigError(f"{name} must be >= 0, got: {val!r}")

    if min_atr_pct is not None and max_atr_pct is not None:
        if float(min_atr_pct) > float(max_atr_pct):
            raise ConfigError(
                f"filters.volatility.min_atr_pct ({min_atr_pct}) "
                f"must be <= max_atr_pct ({max_atr_pct})"
            )

    # --- amplitude sub-section (patch §G.4) ---
    amp_raw = raw.get("amplitude", {}) or {}
    if mode in ("amplitude", "amplitude_and_volume"):
        # n must be validated first; it is needed for min_separation bounds.
        raw_n = amp_raw.get("n", 20)
        n_amp = _validate_amp_subfield(
            "filters.amplitude.n", raw_n, kind="int", lo=10, hi=60,
        )
        amp_cfg = _validate_amplitude_section(amp_raw, n=n_amp)
    else:
        amp_cfg = _default_amplitude.copy()

    # --- volume sub-section ---
    vvol_raw = raw.get("volume", {}) or {}
    if not isinstance(vvol_raw, dict):
        raise ConfigError(
            f"filters.volume must be a mapping, got {type(vvol_raw).__name__!r}"
        )

    volume_column = vvol_raw.get("volume_column", "Volume")
    volume_ma_column = vvol_raw.get("volume_ma_column", "Volume MA")

    if not isinstance(volume_column, str):
        raise ConfigError(
            f"filters.volume.volume_column must be a string (or omitted), "
            f"got: {volume_column!r}"
        )
    if not isinstance(volume_ma_column, str):
        raise ConfigError(
            f"filters.volume.volume_ma_column must be a string, got: {volume_ma_column!r}"
        )

    if mode in ("volume", "volatility_and_volume", "amplitude_and_volume"):
        if not volume_ma_column.strip():
            raise ConfigError(
                "filters.volume.volume_ma_column is required when "
                f"filters.mode == {mode!r}"
            )

    min_ratio = vvol_raw.get("min_ratio", None)
    max_ratio = vvol_raw.get("max_ratio", None)

    for name, val in (("filters.volume.min_ratio", min_ratio),
                      ("filters.volume.max_ratio", max_ratio)):
        if val is not None:
            if isinstance(val, bool):
                raise ConfigError(f"{name} must be a float >= 0 or null, got bool: {val!r}")
            if not isinstance(val, (int, float)):
                raise ConfigError(
                    f"{name} must be a float >= 0 or null, got {type(val).__name__!r}: {val!r}"
                )
            if float(val) < 0:
                raise ConfigError(f"{name} must be >= 0, got: {val!r}")

    if min_ratio is not None and max_ratio is not None:
        if float(min_ratio) > float(max_ratio):
            raise ConfigError(
                f"filters.volume.min_ratio ({min_ratio}) "
                f"must be <= max_ratio ({max_ratio})"
            )

    # --- zigzag sub-section (plan v2.0 §3.5.3) ---
    zz_raw = raw.get("zigzag", {}) or {}
    if mode in ("zigzag", "zigzag_and_volume"):
        zz_cfg = _validate_zigzag_section(zz_raw)
        # For zigzag_and_volume: volume_ma_column must be non-empty.  Reuse the
        # same rule already enforced above for other _and_volume modes (the
        # rule lives earlier in this function under the `volume_ma_column is
        # required` block — we just need to make sure mode matches).  Below
        # we re-raise explicitly for clarity (no functional duplication).
        if mode == "zigzag_and_volume" and not volume_ma_column.strip():
            raise ConfigError(
                "filters.volume.volume_ma_column is required when "
                "filters.mode == 'zigzag_and_volume'"
            )
    else:
        zz_cfg = _default_zigzag.copy()

    return {
        "mode": mode,
        "volatility": {
            "min_atr_pct": float(min_atr_pct) if min_atr_pct is not None else None,
            "max_atr_pct": float(max_atr_pct) if max_atr_pct is not None else None,
        },
        "amplitude": amp_cfg,
        "volume": {
            "volume_column": volume_column,
            "volume_ma_column": volume_ma_column,
            "min_ratio": float(min_ratio) if min_ratio is not None else None,
            "max_ratio": float(max_ratio) if max_ratio is not None else None,
        },
        "zigzag": zz_cfg,
    }


def parse_args(args=None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="SuperTrend Tester - backtest with fixed parameters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m supertrend_optimizer.cli.tester --csv data.csv --atr 14 --mult 3.0 --mode revers --out result.xlsx
  python -m supertrend_optimizer.cli.tester --csv data.csv --atr 10 --mult 2.5 --mode long --config config.yaml --out result.xlsx
        """
    )
    
    parser.add_argument(
        "--csv",
        type=str,
        required=True,
        help="Path to OHLC CSV file"
    )
    
    parser.add_argument(
        "--atr",
        type=int,
        required=False,
        default=None,
        help="ATR period for SuperTrend (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--mult",
        type=float,
        required=False,
        default=None,
        help="ATR multiplier for SuperTrend (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--mode",
        type=str,
        required=False,
        default=None,
        choices=["revers", "long", "short"],
        help="Trading mode: revers (long+short), long, short (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Path to output XLSX file"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to YAML config file (optional, for supertrend/trade_mode/commission/warmup settings)"
    )
    
    parser.add_argument(
        "--execution-model",
        type=str,
        required=False,
        default=None,
        choices=["open_to_open", "close_to_close"],
        help="Execution model: open_to_open (default), close_to_close (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--market",
        type=str,
        required=False,
        default=None,
        choices=["stocks", "crypto", "futures", "forex"],
        help="Market type for annualization defaults and validation (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--annualization-basis",
        type=str,
        required=False,
        default=None,
        choices=["calendar", "trading"],
        help="Annualization basis: calendar (365.25 days/year) or trading (252 days/year) (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--periods-per-year",
        type=str,
        required=False,
        default=None,
        help="Periods per year for annualization: integer or 'auto' (can be set in config.yaml)"
    )
    
    return parser.parse_args(args)


def validate_paths(csv_path: str, output_path: str) -> None:
    """
    Validate input and output file paths.
    
    Args:
        csv_path: Path to input CSV file
        output_path: Path to output Excel file
        
    Raises:
        FileNotFoundError: If CSV file does not exist
        ValueError: If output directory does not exist
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"Input CSV file not found: {csv_path}")
    
    output_file = Path(output_path)
    output_dir = output_file.parent
    if output_dir != Path('.') and not output_dir.exists():
        raise ValueError(f"Output directory does not exist: {output_dir}")


def load_tester_config(config_path: Optional[str]) -> Dict[str, Any]:
    """
    Load and parse tester configuration.

    Contract:
        If ``config_path`` is provided (non-empty string), any failure to read or
        parse the file — missing path, OS/read error, invalid YAML, a non-mapping
        root, or semantic validation expressed as ``ConfigError`` / ``ValueError``
        from ``load_config`` / YAML — must **fail-fast** (exception propagates).
        The caller must **not** fall back to built-in defaults with only a warning,
        as that would silently change commission, annualization, segmentation, etc.

        If ``config_path`` is ``None`` or ``""``, returns the built-in defaults
        without reading any file.

    Args:
        config_path: Path to YAML config file (optional)

    Returns:
        Dictionary with configuration values (with defaults when no path given)
    """
    # Default values
    config = {
        "commission": 0.0,
        "warmup_period": 0,
        "warmup_period_auto": False,
        "min_trades_required": 5,
        "annualization_factor": 252,  # int or "auto" after coerce
        "annualization_basis": None,  # Optional: "calendar" | "trading"
        "market": None,  # Optional: "stocks" | "crypto" | "futures" | "forex"
        "execution_model": None,  # Optional: "open_to_open" | "close_to_close"
        "trade_mode": None,
        "supertrend": {
            "atr_period": None,
            "multiplier": None,
        },
        "segmentation": {
            "mode": "legacy",
            "n_parts": 5,
        },
        "export": {
            "false_start_max_bars": DEFAULT_FALSE_START_MAX_BARS,
        },
        "filters_cfg": _validate_filters_config(None),
    }

    if not config_path:
        return config

    loaded_config = load_config(config_path)
    if loaded_config is None:
        raise ConfigError(
            f"Config file is empty or invalid (expected a YAML mapping): {config_path}"
        )
    if not isinstance(loaded_config, dict):
        raise ConfigError(
            f"Config root must be a YAML mapping, got {type(loaded_config).__name__}: "
            f"{config_path!r}"
        )

    _missing_ann = object()
    raw_af = loaded_config.get("annualization_factor", _missing_ann)
    raw_ppy = loaded_config.get("periods_per_year", _missing_ann)
    if raw_af is not _missing_ann:
        raw_ann = raw_af
    elif raw_ppy is not _missing_ann:
        raw_ann = raw_ppy
    else:
        raw_ann = 252
    try:
        config["annualization_factor"] = coerce_annualization_config_value(raw_ann)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    config["commission"] = loaded_config.get("commission", 0.0)
    config["warmup_period"] = loaded_config.get("warmup_period", 0)
    config["warmup_period_auto"] = loaded_config.get("warmup_period_auto", False)
    config["min_trades_required"] = loaded_config.get("min_trades_required", 5)
    config["annualization_basis"] = loaded_config.get("annualization_basis", None)
    config["market"] = loaded_config.get("market", None)
    config["execution_model"] = loaded_config.get("execution_model", None)
    config["trade_mode"] = loaded_config.get("trade_mode", None)

    supertrend_config = loaded_config.get("supertrend", {})
    if supertrend_config:
        config["supertrend"]["atr_period"] = supertrend_config.get("atr_period", None)
        config["supertrend"]["multiplier"] = supertrend_config.get("multiplier", None)

    seg_config = loaded_config.get("segmentation", {})
    if seg_config:
        raw_mode = seg_config.get("mode", "legacy")
        if raw_mode not in ("legacy", "equal_blocks"):
            raise ConfigError(
                f"segmentation.mode must be 'legacy' or 'equal_blocks', got: {raw_mode!r}"
            )
        config["segmentation"]["mode"] = raw_mode
        raw_n = seg_config.get("n_parts", 5)
        if not isinstance(raw_n, int) or raw_n < 2:
            raise ConfigError(
                f"segmentation.n_parts must be an integer >= 2, got: {raw_n!r}"
            )
        config["segmentation"]["n_parts"] = raw_n

    _merge_export_config(loaded_config, config)

    filters_raw = loaded_config.get("filters", None)
    config["filters_cfg"] = _validate_filters_config(filters_raw)

    print(f"Loaded config from: {config_path}")
    if config["supertrend"]["atr_period"] is not None:
        print(f"  atr_period: {config['supertrend']['atr_period']}")
    if config["supertrend"]["multiplier"] is not None:
        print(f"  multiplier: {config['supertrend']['multiplier']}")
    if config["trade_mode"] is not None:
        print(f"  trade_mode: {config['trade_mode']}")
    print(f"  commission: {config['commission']}")
    print(f"  warmup_period: {config['warmup_period']}")
    print(f"  warmup_period_auto: {config['warmup_period_auto']}")
    print(f"  min_trades_required: {config['min_trades_required']}")
    print(f"  annualization_factor: {config['annualization_factor']}")
    if config["annualization_basis"] is not None:
        print(f"  annualization_basis: {config['annualization_basis']}")
    if config["market"] is not None:
        print(f"  market: {config['market']}")
    if config["execution_model"] is not None:
        print(f"  execution_model: {config['execution_model']}")
    seg = config["segmentation"]
    print(f"  segmentation.mode: {seg['mode']}")
    if seg["mode"] == "equal_blocks":
        print(f"  segmentation.n_parts: {seg['n_parts']}")
    print(
        f"  export.false_start_max_bars: {config['export']['false_start_max_bars']}"
    )
    print(f"  filters.mode: {config['filters_cfg']['mode']}")

    return config


def merge_cli_and_config(
    parsed: argparse.Namespace,
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge CLI arguments with config, CLI takes precedence.
    
    Args:
        parsed: Parsed command line arguments
        config: Loaded configuration
        
    Returns:
        Merged configuration
        
    Raises:
        ValueError: If required parameters are missing
    """
    # CLI arguments override config values
    if parsed.atr is not None:
        atr_period = parsed.atr
    elif config["supertrend"]["atr_period"] is not None:
        atr_period = config["supertrend"]["atr_period"]
    else:
        raise ValueError(
            "atr_period must be specified either via --atr argument or in config.yaml"
        )
    
    if parsed.mult is not None:
        multiplier = parsed.mult
    elif config["supertrend"]["multiplier"] is not None:
        multiplier = config["supertrend"]["multiplier"]
    else:
        raise ValueError(
            "multiplier must be specified either via --mult argument or in config.yaml"
        )
    
    if parsed.mode is not None:
        trade_mode = parsed.mode
    elif config["trade_mode"] is not None:
        trade_mode = config["trade_mode"]
    else:
        raise ValueError(
            "trade_mode must be specified either via --mode argument or in config.yaml"
        )
    
    # CLI overrides for new parameters
    annualization_factor = config["annualization_factor"]
    if parsed.periods_per_year is not None:
        # Parse CLI argument (can be "auto" or integer string)
        if parsed.periods_per_year == "auto":
            annualization_factor = "auto"
        else:
            try:
                annualization_factor = int(parsed.periods_per_year)
            except ValueError:
                raise ValueError(
                    f"--periods-per-year must be 'auto' or an integer, got: {parsed.periods_per_year}"
                )
    
    annualization_basis = config["annualization_basis"]
    if parsed.annualization_basis is not None:
        annualization_basis = parsed.annualization_basis
    
    market = config["market"]
    if parsed.market is not None:
        market = parsed.market
    
    execution_model = config["execution_model"]
    if parsed.execution_model is not None:
        execution_model = parsed.execution_model
    
    # Backwards-compat: legacy callers may build ``config`` dicts by hand
    # without the ``filters_cfg`` key (tests, external tooling). Fall back
    # to the mode=none default in that case so the merge does not fail.
    filters_cfg = config.get("filters_cfg") or _validate_filters_config(None)

    return {
        "atr_period": atr_period,
        "multiplier": multiplier,
        "trade_mode": trade_mode,
        "commission": config["commission"],
        "warmup_period": config["warmup_period"],
        "warmup_period_auto": config["warmup_period_auto"],
        "min_trades_required": config["min_trades_required"],
        "annualization_factor": annualization_factor,
        "annualization_basis": annualization_basis,
        "market": market,
        "execution_model": execution_model,
        "segmentation": config["segmentation"],
        "false_start_max_bars": config["export"]["false_start_max_bars"],
        "filters_cfg": filters_cfg,
    }


def run_backtest(args: argparse.Namespace) -> str:
    """
    Run the backtest pipeline.
    
    Args:
        args: Parsed command line arguments
        
    Returns:
        Path to the output file
    """
    # Validate paths
    validate_paths(args.csv, args.out)
    
    # Load config
    config = load_tester_config(args.config)
    
    # Merge CLI and config
    params = merge_cli_and_config(args, config)
    
    # Load and validate data
    df = load_ohlc_csv(args.csv)
    df = validate_ohlc_data(df)
    validate_filter_columns(df, params["filters_cfg"])

    # Extract the volume-MA column once into an ndarray (plan §7.7 updated).
    # Runner never touches df for volume — it receives the pre-extracted
    # array and a dataset-level scalar baseline.
    #
    # New semantics (minimal-invasive rework):
    #   ratio[t] = volume_ma[t] / global_volume_ma_mean
    # where ``global_volume_ma_mean`` is computed ONCE here over the whole
    # validated dataset (including the warmup region — it is a dataset-level
    # constant, not a metric), and then propagated unchanged through every
    # slice/segment. ``volume_column`` is no longer required or read.
    from supertrend_optimizer.core.filters import compute_global_volume_ma_mean

    filters_cfg = params["filters_cfg"]
    mode = filters_cfg.get("mode", "none")
    if mode in ("volume", "volatility_and_volume", "amplitude_and_volume",
                "zigzag_and_volume"):
        vol_ma_col = filters_cfg["volume"]["volume_ma_column"].lower()
        volume_ma_arr = df[vol_ma_col].to_numpy(dtype=float, copy=True)
        global_volume_ma_mean = compute_global_volume_ma_mean(volume_ma_arr)
    else:
        volume_ma_arr = None
        global_volume_ma_mean = None

    n = len(df)

    # --- Amplitude-filter guards (patch §B.1, §H) ---------------------------
    # These checks run before warmup resolution so that fail-fast errors are
    # reported early with helpful messages.
    _amp_mode = filters_cfg.get("mode", "none")
    if _amp_mode in ("amplitude", "amplitude_and_volume"):
        _amp_sub = filters_cfg.get("amplitude", {}) or {}
        _amp_n = int(_amp_sub.get("n", 20))
        _amp_lookback = int(_amp_sub.get("lookback", 500))
        _required_amp_warmup = _amp_n + _amp_lookback

        # §H: minimum dataset length guard.
        _min_data = 2 * _required_amp_warmup
        if n < _min_data:
            raise ConfigError(
                f"amplitude filter requires dataset length >= {_min_data} "
                f"(= 2 * (n={_amp_n} + lookback={_amp_lookback})), "
                f"got {n} bars. "
                f"Reduce filters.amplitude.lookback or provide more data."
            )

    # Resolve periods_per_year from config/CLI + data
    market_enum = MarketType(params["market"]) if params["market"] else None
    
    periods_per_year = resolve_periods_per_year_from_config(
        config_value=params["annualization_factor"],
        index=df.index,
        explicit_basis=params["annualization_basis"],
        market=market_enum
    )
    
    print(f"\nResolved periods_per_year: {periods_per_year:.2f}")
    
    # Emit market warnings if applicable (only for calendar_days_span >= 30)
    if market_enum is not None:
        stats = detect_timeframe(df.index)
        if stats.calendar_days_span >= 30:
            warnings = validate_market_vs_timeframe(market_enum, stats)
            for warning in warnings:
                print(f"WARNING: {warning}")
    
    # Resolve execution_model
    if params["execution_model"] is not None:
        execution_model = ExecutionModel(params["execution_model"])
    else:
        execution_model = ExecutionModel.OPEN_TO_OPEN  # Default
    
    # Calculate warmup period (patch §B.1: amplitude orchestration).
    if params["warmup_period_auto"]:
        warmup_period = calculate_warmup_tester(
            n=n,
            atr_period=params["atr_period"],
            warmup_period_auto=True,
            warmup_period=params["warmup_period"],  # honoured even with auto (patch §F10)
        )
        # For amp modes, raise the floor to n + lookback if needed.
        if _amp_mode in ("amplitude", "amplitude_and_volume"):
            if warmup_period < _required_amp_warmup:
                warmup_period = _required_amp_warmup
                print(
                    f"Using auto-warmup (amp): {warmup_period} bars "
                    f"(raised to n+lookback={_required_amp_warmup} for amplitude filter)"
                )
            else:
                print(f"Using auto-warmup: {warmup_period} bars (10% of {n} bars, clamped)")
        else:
            print(f"Using auto-warmup: {warmup_period} bars (10% of {n} bars, clamped)")
    else:
        warmup_period = max(params["warmup_period"], params["atr_period"])
        # Fail-fast for amp modes when manual warmup is too small (patch §B.1).
        if _amp_mode in ("amplitude", "amplitude_and_volume"):
            if warmup_period < _required_amp_warmup:
                raise ConfigError(
                    f"amplitude filter requires warmup_period >= {_required_amp_warmup} "
                    f"(= n={_amp_n} + lookback={_amp_lookback}), "
                    f"got {warmup_period}. "
                    f"Fix: set warmup_period >= {_required_amp_warmup}, "
                    f"or enable warmup_period_auto: true, "
                    f"or reduce filters.amplitude.lookback."
                )
    
    seg_mode = params["segmentation"]["mode"]
    n_parts = params["segmentation"]["n_parts"]

    print(f"\nRunning backtest...")
    print(f"  CSV: {args.csv}")
    print(f"  ATR period: {params['atr_period']}")
    print(f"  Multiplier: {params['multiplier']}")
    print(f"  Mode: {params['trade_mode']}")
    print(f"  Commission: {params['commission']}")
    print(f"  Warmup period: {warmup_period}")
    print(f"  auto_warmup: {params['warmup_period_auto']}")
    print(f"  min_trades_required: {params['min_trades_required']}")
    print(f"  Execution model: {execution_model.value}")
    print(f"  Segmentation mode: {seg_mode}")
    if seg_mode == "equal_blocks":
        print(f"  n_parts: {n_parts}")

    if seg_mode == "equal_blocks":
        segment_results = run_equal_blocks(
            df=df,
            n_parts=n_parts,
            warmup_period=warmup_period,
            atr_period=params["atr_period"],
            multiplier=params["multiplier"],
            trade_mode=params["trade_mode"],
            commission=params["commission"],
            periods_per_year=periods_per_year,
            execution_model=execution_model,
            min_trades_required=params["min_trades_required"],
            filters_cfg=filters_cfg,
            volume_ma_arr=volume_ma_arr,
            global_volume_ma_mean=global_volume_ma_mean,
        )

        print(f"\nBacktest completed ({n_parts} segments):")
        for s in segment_results:
            m = s.segment_metrics
            print(
                f"  {s.segment_label} [{s.range_label}]: "
                f"{m.get('num_trades', 0)} trades, "
                f"Sum PnL: {m.get('sum_pnl_pct', 0):.2f}%"
            )

        print(f"\nExporting to Excel: {args.out}")
        actual_output = export_equal_blocks_results(segment_results, args.out)

    else:
        # Legacy mode: 5 tail slices
        results = run_all_periods(
            df=df,
            atr_period=params["atr_period"],
            multiplier=params["multiplier"],
            trade_mode=params["trade_mode"],
            commission=params["commission"],
            warmup_period=warmup_period,
            periods_per_year=periods_per_year,
            execution_model=execution_model,
            auto_warmup=params["warmup_period_auto"],
            min_trades_required=params["min_trades_required"],
            filters_cfg=filters_cfg,
            volume_ma_arr=volume_ma_arr,
            global_volume_ma_mean=global_volume_ma_mean,
        )

        print(f"\nBacktest completed:")
        for r in results:
            print(f"  {r.period_label}: {r.metrics['num_trades']} trades, Sum PnL: {r.metrics['sum_pnl_pct']:.2f}%")

        close_arr = df["close"].values.astype(float)
        # Extract engine-computed amp diagnostics from the 100% period result
        # so that build_signal_events uses the same arrays as the engine and
        # does not fall back to a recomputed / wrong ATR (patch §F1).
        _fd0 = results[0].result.filter_diagnostics or {}
        signals_df = build_signal_events(
            df=df,
            trend=results[0].result.trend,
            atr_period=params["atr_period"],
            trade_mode=params["trade_mode"],
            execution_model=execution_model,
            atr=results[0].result.atr,
            close=close_arr,
            volume_ma=volume_ma_arr,
            global_volume_ma_mean=global_volume_ma_mean,
            filters_cfg=filters_cfg,
            filter_diagnostics=_fd0,
            # Legacy per-field kwargs kept for BC (filter_diagnostics takes priority).
            amp_n_arr=_fd0.get("amp_n"),
            amp_threshold_arr=_fd0.get("amp_threshold"),
            atr_amp_arr=_fd0.get("atr_amp"),
            sep_arr=_fd0.get("separation"),
        )

        print(f"\nExporting to Excel: {args.out}")
        actual_output = export_tester_results(
            results,
            args.out,
            signals_df=signals_df,
            false_start_max_bars=params["false_start_max_bars"],
        )

    print(f"\n[SUCCESS] Results exported to: {actual_output}")

    return actual_output


def main(args=None) -> None:
    """Main entry point with error handling."""
    parsed = parse_args(args)
    
    try:
        run_backtest(parsed)
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
        
    except (ConfigError, DataValidationError) as e:
        print(f"Validation error: {e}", file=sys.stderr)
        sys.exit(1)
        
    except yaml.YAMLError as e:
        print(f"Configuration file error: {e}", file=sys.stderr)
        sys.exit(1)
        
    except pd.errors.ParserError as e:
        print(f"CSV parsing error: {e}", file=sys.stderr)
        sys.exit(1)
        
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    except PermissionError as e:
        print(
            f"Error: could not write output file (is it open in another program?): {e}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
