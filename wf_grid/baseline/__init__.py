"""
WF Grid baseline capture package (plan WP1).

Small deterministic fingerprint utilities that let us snapshot the current
PipelineResult and compare future runs against the snapshot bit-for-bit.

The package is intentionally minimal in WP1: it knows nothing about the
trade_filter feature and just freezes the current pipeline output as the
"baseline before any trade_filter changes".

Spec reference: Appendix A v1.1 §11, §17.1, §18.
"""

from __future__ import annotations

from wf_grid.baseline.fingerprint import (
    BASELINE_SCHEMA_VERSION,
    PipelineFingerprint,
    compute_dataframe_fingerprint,
    compute_pipeline_fingerprint,
    fingerprints_equal,
    load_baseline,
    save_baseline,
    summarize_diff,
)

__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "PipelineFingerprint",
    "compute_dataframe_fingerprint",
    "compute_pipeline_fingerprint",
    "fingerprints_equal",
    "load_baseline",
    "save_baseline",
    "summarize_diff",
]
