"""
Capture a baseline pipeline fingerprint (plan WP1).

Run this script BEFORE making any trade_filter changes (or whenever the
baseline source of truth needs to be refreshed).  It runs the full WF Grid
pipeline with the supplied config, computes a deterministic fingerprint of
the resulting PipelineResult (DataFrame hashes + pinned numeric statistics
+ diagnostics summary) and writes it to disk.

The corresponding regression test (``wf_grid/tests/test_wp1_baseline_capture.py``)
re-runs the pipeline and compares against this fingerprint to guarantee
bit-identical baseline behaviour throughout WP2..WP10.

Usage:
    python scripts/capture_baseline.py
    python scripts/capture_baseline.py --config config.yaml --output tests/baseline/baseline_v0.json

Spec reference: Appendix A v1.1 §11, §17.1, §18.
"""

from __future__ import annotations

import argparse
import logging
import platform
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add donor/ to import path BEFORE importing anything from supertrend_optimizer
# (mirrors run.py to keep behaviour identical).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "donor"))
sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from wf_grid.baseline import (  # noqa: E402
    compute_pipeline_fingerprint,
    save_baseline,
)
from wf_grid.pipeline.orchestrator import run_grid_pipeline  # noqa: E402

DEFAULT_BASELINE_PATH = "tests/baseline/baseline_v0.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture a baseline pipeline fingerprint (plan WP1)."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config (default: config.yaml).",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_BASELINE_PATH,
        help=f"Output JSON path for the fingerprint (default: {DEFAULT_BASELINE_PATH}).",
    )
    parser.add_argument(
        "--xlsx-output",
        default=None,
        help="Optional override for the XLSX export path.",
    )
    parser.add_argument(
        "--comment",
        default="",
        help="Optional human-readable comment stored in the baseline metadata.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("capture_baseline")

    config_path = Path(args.config).resolve()
    output_path = Path(args.output).resolve()
    log.info("Config:  %s", config_path)
    log.info("Output:  %s", output_path)

    log.info("Running pipeline...")
    # Sequential WF execution yields a stable, reproducible fingerprint across
    # machines and pytest reruns. Parallel can introduce tiny float drift in
    # aggregations and break baseline equality even when logic is identical.
    result = run_grid_pipeline(
        config_path=str(config_path),
        output_path=args.xlsx_output,
        parallel_enabled=False,
    )
    if result.error:
        log.error("Pipeline failed: %s", result.error)
        return 1

    # Re-load the input data so we can hash it into the metadata.  The
    # orchestrator does not surface the raw OHLC frame in PipelineResult.
    data_path: str | None = None
    data: pd.DataFrame | None = None
    if result.config is not None and result.config.data.file_path:
        data_path = result.config.data.file_path
        try:
            data = pd.read_csv(data_path, parse_dates=True, index_col=0)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Could not re-load data for hash: %s", exc)
            data = None

    extra_metadata = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "comment": args.comment,
    }

    fp = compute_pipeline_fingerprint(
        result,
        config_path=str(config_path),
        data_path=data_path,
        data=data,
        extra_metadata=extra_metadata,
    )

    save_baseline(fp, output_path)
    log.info("Baseline fingerprint saved to %s", output_path)

    if result.diagnostics is not None:
        d = result.diagnostics
        log.info(
            "Pipeline summary: grid=%d points, wf_steps=%d, status=%s",
            d.grid_size, d.n_wf_steps, d.step_status_counts,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
