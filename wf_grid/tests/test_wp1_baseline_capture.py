"""
WP1 — Baseline regression test (plan §11 / WP1, §14 DoD #2).

Goal
----
Pin the current pipeline output as the "baseline before any trade_filter
changes" and prove that running the pipeline again yields a bit-identical
result for as long as ``trade_filter`` is absent / disabled.

How it works
------------
1.  The companion script ``scripts/capture_baseline.py`` runs the pipeline
    once on the canonical ``config.yaml`` + ``data.csv`` and saves a JSON
    fingerprint to ``tests/baseline/baseline_v0.json``.
2.  This test re-runs the pipeline with the same config and recomputes the
    fingerprint, then asserts that the two fingerprints are identical
    (modulo free-form metadata such as captured_at / host).
3.  The test is marked ``slow`` because a full grid run is multi-minute.
    It is also automatically skipped when the baseline fingerprint has not
    been captured yet — that lets the rest of the test suite stay fast on
    a freshly cloned repo while still allowing CI to opt-in.

Spec reference: Appendix A v1.1 §11, §17.1, §17.2.11, §18.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from wf_grid.baseline import (
    compute_pipeline_fingerprint,
    fingerprints_equal,
    load_baseline,
    summarize_diff,
)

# Repo-root resolution: this file lives at <repo>/wf_grid/tests/, so two
# .parent calls take us back to <repo>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINE_PATH = _REPO_ROOT / "tests" / "baseline" / "baseline_v0.json"
_CONFIG_PATH = _REPO_ROOT / "config.yaml"


pytestmark = pytest.mark.slow


def _baseline_available() -> bool:
    return _BASELINE_PATH.exists() and _CONFIG_PATH.exists()


@pytest.mark.skipif(
    not _baseline_available(),
    reason=(
        "Baseline fingerprint not captured yet. "
        "Run `python scripts/capture_baseline.py` to create "
        "tests/baseline/baseline_v0.json."
    ),
)
def test_disabled_path_matches_baseline():
    """Re-running the pipeline with no trade_filter must reproduce the baseline.

    This is the canonical bit-identical-baseline regression gate (plan §14 #2).
    Acceptable differences are limited to free-form metadata (captured_at,
    host, comment); every numeric statistic / shape / dtype / content_hash
    must match exactly.
    """
    from wf_grid.pipeline.orchestrator import run_grid_pipeline

    baseline = load_baseline(_BASELINE_PATH)

    result = run_grid_pipeline(config_path=str(_CONFIG_PATH))
    assert result.error is None, f"Pipeline run failed: {result.error}"

    data: pd.DataFrame | None = None
    if result.config is not None and result.config.data.file_path:
        try:
            data = pd.read_csv(
                result.config.data.file_path, parse_dates=True, index_col=0,
            )
        except Exception:
            data = None

    current = compute_pipeline_fingerprint(
        result,
        config_path=str(_CONFIG_PATH),
        data_path=str(result.config.data.file_path) if result.config else None,
        data=data,
    )

    diff = summarize_diff(baseline, current)
    assert fingerprints_equal(baseline, current), (
        "Disabled-path baseline regression detected. "
        "First differences:\n  - " + "\n  - ".join(diff[:20])
    )
