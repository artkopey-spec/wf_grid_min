"""
Baseline fingerprinting utilities (plan WP1).

A "fingerprint" is a JSON-serialisable dict that describes a PipelineResult
deterministically enough to detect bit-level regressions on the disabled
(trade_filter-less) baseline path.

Why a fingerprint and not the raw artefacts?
  - The full PipelineResult contains tens of thousands of rows across many
    DataFrames; storing the raw frames would bloat the repo.
  - We want to detect *any* deterministic change.  A combination of
    shape/columns/dtypes plus a content hash plus a small set of pinned
    numeric values (mean/sum/min/max for every numeric column) is enough
    to catch every kind of drift we care about for WP1.

Compared to a single content hash, the per-column statistics make diff
reports informative ("column sum_pnl_pct_Median.mean drifted from 5.13 to
5.21") instead of an opaque "hash mismatch".

Spec reference: Appendix A v1.1 §11, §17.1, §18.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.core.trade_filter_config import is_volume_enabled

# Schema history:
#   v1 — initial baseline format (frames + diagnostics only).
#   v2 — added top-level `attributes` for scalar PipelineResult fields
#        (bucket_matrix_status, bucket_matrix_error).  Required by plan
#        WP1.steps.2 / DoD #2 which lists those two artefacts alongside the
#        DataFrame-valued ones.
BASELINE_SCHEMA_VERSION = 2


@dataclass
class PipelineFingerprint:
    """JSON-friendly snapshot of a PipelineResult for regression checks."""

    schema_version: int = BASELINE_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)
    frames: dict[str, dict[str, Any]] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PipelineFingerprint":
        return cls(
            schema_version=int(raw.get("schema_version", BASELINE_SCHEMA_VERSION)),
            metadata=dict(raw.get("metadata", {})),
            frames=dict(raw.get("frames", {})),
            attributes=dict(raw.get("attributes", {})),
            diagnostics=dict(raw.get("diagnostics", {})),
        )


# ---------------------------------------------------------------------------
# DataFrame fingerprint
# ---------------------------------------------------------------------------

# Tolerance we accept when two pinned float statistics are compared.  Equal
# pipeline runs on the same machine should produce bit-identical output, so
# the tolerance is set very tight; it only protects us against the very last
# IEEE-754 bit in JSON round-tripping (json -> str -> float).
_FLOAT_ABS_TOL = 1e-12
_FLOAT_REL_TOL = 1e-12


def _is_numeric_dtype(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def _safe_stat(value: float) -> Optional[float]:
    """Convert a numpy/pandas scalar to a JSON-friendly float (NaN/Inf as None)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _column_stats(series: pd.Series) -> dict[str, Optional[float]]:
    """Compute pinned statistics for a numeric column."""
    if series.empty:
        return {
            "count": 0,
            "n_non_null": 0,
            "n_null": 0,
            "sum": None,
            "mean": None,
            "min": None,
            "max": None,
        }
    n_total = int(series.size)
    n_null = int(series.isna().sum())
    n_non_null = n_total - n_null
    if n_non_null == 0:
        return {
            "count": n_total,
            "n_non_null": 0,
            "n_null": n_null,
            "sum": None,
            "mean": None,
            "min": None,
            "max": None,
        }
    return {
        "count": n_total,
        "n_non_null": n_non_null,
        "n_null": n_null,
        "sum": _safe_stat(series.sum(skipna=True)),
        "mean": _safe_stat(series.mean(skipna=True)),
        "min": _safe_stat(series.min(skipna=True)),
        "max": _safe_stat(series.max(skipna=True)),
    }


def _content_hash(df: pd.DataFrame) -> str:
    """Deterministic hash of a DataFrame's content (rows + columns + dtypes).

    We avoid pickling because pickle output depends on pandas / numpy versions.
    Instead we use ``pd.util.hash_pandas_object`` which is stable across
    pandas versions for fixed dtypes.
    """
    if df.empty:
        # An empty DataFrame still carries column / dtype information that we
        # want in the fingerprint metadata; the content hash itself is the
        # hash of an empty byte string.
        h = hashlib.sha256()
        h.update(b"empty")
        h.update(",".join(map(str, df.columns)).encode("utf-8"))
        h.update(",".join(str(df[c].dtype) for c in df.columns).encode("utf-8"))
        return h.hexdigest()

    h = hashlib.sha256()
    # Row-level hash (pd.util.hash_pandas_object returns a uint64 series).
    row_hashes = pd.util.hash_pandas_object(df, index=True).values
    h.update(row_hashes.tobytes())
    # Column / dtype guardrail so that adding a column with all-NaN values
    # still changes the fingerprint.
    h.update(",".join(map(str, df.columns)).encode("utf-8"))
    h.update(",".join(str(df[c].dtype) for c in df.columns).encode("utf-8"))
    return h.hexdigest()


def compute_dataframe_fingerprint(df: Optional[pd.DataFrame]) -> dict[str, Any]:
    """Return a deterministic, JSON-friendly fingerprint of a DataFrame.

    ``None`` is encoded as ``{"present": False}`` so a baseline that has
    a frame and a current run that doesn't (or vice versa) reliably mismatch.
    """
    if df is None:
        return {"present": False}

    cols = list(map(str, df.columns))
    dtypes = {c: str(df[c].dtype) for c in cols}

    column_stats: dict[str, dict[str, Any]] = {}
    for c in cols:
        s = df[c]
        if _is_numeric_dtype(s):
            column_stats[c] = _column_stats(s)
        else:
            n_total = int(s.size)
            n_null = int(s.isna().sum())
            column_stats[c] = {
                "count": n_total,
                "n_non_null": n_total - n_null,
                "n_null": n_null,
                "n_unique": int(s.nunique(dropna=True)),
            }

    return {
        "present": True,
        "shape": list(df.shape),
        "columns": cols,
        "dtypes": dtypes,
        "column_stats": column_stats,
        "content_hash": _content_hash(df),
    }


# ---------------------------------------------------------------------------
# Pipeline fingerprint
# ---------------------------------------------------------------------------

# The set of frames we snapshot is locked to the ones the plan calls out as
# baseline artefacts (plan §1 / WP1.steps.2 / Definition of Done §2).
# In PipelineResult naming, WF_Trades == trades_oos and WF_Train_Trades ==
# trades_train (the WF_* names are XLSX export sheet names).
_BASELINE_FRAMES: tuple[str, ...] = (
    "step_oos_long",
    "step_train_long",
    "trades_oos",
    "trades_train",
    "aggregated",
    "ranked",
    "summary_wide",
    "bucket_matrix_median",
)

# Scalar PipelineResult attributes that the plan lists alongside the frames.
# These are simple strings (status / error message), not DataFrames, so we
# capture them in their own JSON section instead of squeezing them through
# compute_dataframe_fingerprint.  The sentinel ``__missing__`` reliably
# differentiates "field absent on result" from "field present and equal to
# None" so a baseline never silently degrades.
_BASELINE_ATTRIBUTES: tuple[str, ...] = (
    "bucket_matrix_status",
    "bucket_matrix_error",
)
_ATTR_MISSING_SENTINEL = "__missing__"

# WP-PAR: runtime-only attributes excluded from all fingerprints (plan §3.9).
# execution_mode changes when parallel is enabled; output_path is environment-
# specific.  Both must not cause spurious baseline mismatches.
_NON_DETERMINISTIC_RESULT_ATTRIBUTES: frozenset[str] = frozenset({
    "execution_mode",
    "output_path",
})


def _json_safe_value(value: Any) -> Any:
    """Recursively convert diagnostics values to stable JSON primitives."""
    if isinstance(value, Mapping):
        return {str(k): _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _diagnostics_to_dict(diag: Any) -> dict[str, Any]:
    """Best-effort serialisation of PipelineDiagnostics to a JSON-friendly dict.

    PipelineDiagnostics is a frozen-ish dataclass (we don't import it directly
    here to avoid a circular module dependency).  We only persist
    deterministic fields and skip ``timings`` because wall-clock numbers are
    not reproducible.
    """
    if diag is None:
        return {"present": False}

    fields = (
        "grid_size",
        "n_wf_steps",
        "step_status_counts",
        "tier_counts",
        "top5_ranked",
    )
    out: dict[str, Any] = {"present": True}
    for f in fields:
        if hasattr(diag, f):
            v = getattr(diag, f)
            # Normalise keys to strings so that fingerprints survive a JSON
            # round-trip unchanged, and normalise NaN/Inf to None so equal
            # diagnostics do not falsely differ because NaN != NaN.
            out[f] = _json_safe_value(v)
    return out


def _data_fingerprint(data: Optional[pd.DataFrame]) -> dict[str, Any]:
    if data is None:
        return {"present": False}
    return {
        "present": True,
        "shape": list(data.shape),
        "columns": list(map(str, data.columns)),
        "index_first": str(data.index[0]) if len(data.index) else None,
        "index_last": str(data.index[-1]) if len(data.index) else None,
        "content_hash": _content_hash(data),
    }


def _volume_snapshot_from_result_config(result: Any) -> dict[str, Any]:
    config = getattr(result, "config", None)
    tf = getattr(config, "trade_filter", None)
    if not is_volume_enabled(tf):
        return {}
    volume = tf.volume
    threshold_ratio = volume.threshold_ratio
    exit_hysteresis_ratio = getattr(volume, "exit_hysteresis_ratio", None)
    if exit_hysteresis_ratio is None:
        exit_hysteresis_ratio = threshold_ratio
    exit_freeze_bars = getattr(volume, "exit_freeze_bars", None)
    if exit_freeze_bars is None:
        exit_freeze_bars = 0
    return {
        "volume_filter_enabled": True,
        "volume_filter_mode": volume.mode,
        "volume_daily_reset": bool(getattr(volume, "daily_reset", False)),
        "volume_short_window": volume.short_window,
        "volume_baseline_window": volume.baseline_window,
        "volume_threshold_ratio": threshold_ratio,
        "volume_exit_hysteresis_ratio": exit_hysteresis_ratio,
        "volume_exit_freeze_bars": exit_freeze_bars,
        "volume_regime_low_ratio": volume.regime_low_ratio,
        "volume_regime_high_ratio": volume.regime_high_ratio,
        "volume_direction_lookback_bars": volume.direction_lookback_bars,
    }


def compute_pipeline_fingerprint(
    result: Any,
    *,
    config_path: Optional[str] = None,
    data_path: Optional[str] = None,
    data: Optional[pd.DataFrame] = None,
    extra_metadata: Optional[Mapping[str, Any]] = None,
) -> PipelineFingerprint:
    """Build a fingerprint that uniquely identifies a baseline pipeline run.

    Parameters
    ----------
    result:
        The ``PipelineResult`` returned by ``run_grid_pipeline``.
    config_path:
        Path of the YAML config that produced ``result`` (stored in metadata).
    data_path:
        Path of the CSV data that produced ``result`` (stored in metadata).
    data:
        Optional already-loaded OHLC DataFrame.  When supplied, its hash is
        added to metadata so we can detect "input data changed under us".
    extra_metadata:
        Free-form metadata to merge into the fingerprint.  Use this for
        ``run_id``, host info, or human-readable comments.
    """
    metadata: dict[str, Any] = {
        "config_path": config_path,
        "data_path": data_path,
    }
    if data is not None:
        metadata["data"] = _data_fingerprint(data)
    metadata.update(_volume_snapshot_from_result_config(result))
    if extra_metadata:
        metadata.update(dict(extra_metadata))

    frames: dict[str, dict[str, Any]] = {}
    for name in _BASELINE_FRAMES:
        frames[name] = compute_dataframe_fingerprint(getattr(result, name, None))

    attributes: dict[str, Any] = {}
    for name in _BASELINE_ATTRIBUTES:
        if name in _NON_DETERMINISTIC_RESULT_ATTRIBUTES:
            continue
        attributes[name] = getattr(result, name, _ATTR_MISSING_SENTINEL)

    diagnostics = _diagnostics_to_dict(getattr(result, "diagnostics", None))

    return PipelineFingerprint(
        schema_version=BASELINE_SCHEMA_VERSION,
        metadata=metadata,
        frames=frames,
        attributes=attributes,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Equality / diff
# ---------------------------------------------------------------------------

def _floats_close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, abs_tol=_FLOAT_ABS_TOL, rel_tol=_FLOAT_REL_TOL)


def _diff_value(path: str, a: Any, b: Any, out: list[str]) -> None:
    if isinstance(a, float) or isinstance(b, float):
        af = a if isinstance(a, float) else (None if a is None else float(a))
        bf = b if isinstance(b, float) else (None if b is None else float(b))
        if not _floats_close(af, bf):
            out.append(f"{path}: {a!r} != {b!r}")
        return
    if a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def _diff_mappings(path: str, a: Mapping[str, Any], b: Mapping[str, Any], out: list[str]) -> None:
    a_keys = set(a.keys())
    b_keys = set(b.keys())
    only_a = sorted(a_keys - b_keys)
    only_b = sorted(b_keys - a_keys)
    for k in only_a:
        out.append(f"{path}.{k}: only in baseline ({a[k]!r})")
    for k in only_b:
        out.append(f"{path}.{k}: only in current ({b[k]!r})")
    for k in sorted(a_keys & b_keys):
        sub_path = f"{path}.{k}" if path else k
        av = a[k]
        bv = b[k]
        if isinstance(av, Mapping) and isinstance(bv, Mapping):
            _diff_mappings(sub_path, av, bv, out)
        elif isinstance(av, list) and isinstance(bv, list):
            if av != bv:
                out.append(f"{sub_path}: list differs ({av!r} != {bv!r})")
        else:
            _diff_value(sub_path, av, bv, out)


_DEFAULT_SKIP_METADATA_KEYS: tuple[str, ...] = (
    # Path/timestamp/host/comment fields are free-form metadata that legitimately
    # change between runs without affecting pipeline determinism.
    "config_path",
    "data_path",
    "captured_at",
    "host",
    "comment",
    # Environment-specific fields populated by capture_baseline.py.  They are
    # *not* part of the pipeline output and must not cause a regression when
    # the baseline is replayed on another machine / Python build.
    "platform",
    "python",
)


def summarize_diff(
    baseline: PipelineFingerprint,
    current: PipelineFingerprint,
    *,
    skip_metadata_keys: Iterable[str] = _DEFAULT_SKIP_METADATA_KEYS,
) -> list[str]:
    """Return a list of human-readable strings describing the differences.

    By default a few free-form metadata fields (paths, timestamps, hostnames,
    comments) are excluded from the diff so that re-running the pipeline on a
    different machine still passes equality.
    """
    out: list[str] = []

    if baseline.schema_version != current.schema_version:
        out.append(
            f"schema_version: {baseline.schema_version} != {current.schema_version}"
        )

    skip = set(skip_metadata_keys)
    base_meta = {k: v for k, v in baseline.metadata.items() if k not in skip}
    cur_meta = {k: v for k, v in current.metadata.items() if k not in skip}
    _diff_mappings("metadata", base_meta, cur_meta, out)

    _diff_mappings("frames", baseline.frames, current.frames, out)
    _diff_mappings("attributes", baseline.attributes, current.attributes, out)
    _diff_mappings("diagnostics", baseline.diagnostics, current.diagnostics, out)
    return out


def fingerprints_equal(
    baseline: PipelineFingerprint,
    current: PipelineFingerprint,
    *,
    skip_metadata_keys: Iterable[str] = _DEFAULT_SKIP_METADATA_KEYS,
) -> bool:
    return not summarize_diff(baseline, current, skip_metadata_keys=skip_metadata_keys)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_baseline(fp: PipelineFingerprint, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fp.to_json(), encoding="utf-8")
    return p


def load_baseline(path: str | Path) -> PipelineFingerprint:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Baseline fingerprint not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    fp = PipelineFingerprint.from_dict(raw)
    if fp.schema_version != BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"Baseline schema_version {fp.schema_version} is not supported "
            f"by current code (expected {BASELINE_SCHEMA_VERSION}). "
            "Re-capture the baseline."
        )
    return fp
