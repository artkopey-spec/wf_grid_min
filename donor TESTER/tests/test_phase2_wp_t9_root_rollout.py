"""
WP-T9 root rollout hardening.

These checks pin the production-facing root artifacts that sit outside the
canonical ``donor TESTER`` implementation:

* repo-root ``run_batch_tester.py`` remains a compatibility wrapper;
* repo-root ``config_tester.yaml`` keeps a disabled Phase 2 template;
* the user-facing filter document exists and points to the spec.
"""

from pathlib import Path
import subprocess
import sys

from supertrend_optimizer.cli.tester import load_tester_config


REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENTRYPOINT = REPO_ROOT / "run_batch_tester.py"
ROOT_CONFIG = REPO_ROOT / "config_tester.yaml"
FILTER_DOC = REPO_ROOT / "docs" / "zigzag_st_filter.md"


def test_root_entrypoint_is_thin_canonical_wrapper() -> None:
    src = ROOT_ENTRYPOINT.read_text(encoding="utf-8")

    assert "donor TESTER" in src
    assert "run_batch_tester.py" in src
    assert "runpy.run_path" in src
    assert "build_zigzag_global_stats" not in src
    assert "export_tester_results" not in src


def test_root_entrypoint_help_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT_ENTRYPOINT), "--help"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Batch SuperTrend Tester" in result.stdout
    assert "--csv" in result.stdout
    assert "--config" in result.stdout


def test_root_config_template_is_disabled_and_baseline_safe() -> None:
    text = ROOT_CONFIG.read_text(encoding="utf-8")

    assert "# trade_filter:" in text
    assert "#   enabled: false" in text
    assert "segmentation.mode: legacy" in text

    cfg = load_tester_config(str(ROOT_CONFIG))
    tf_cfg = cfg.get("trade_filter")
    assert tf_cfg is None or tf_cfg.enabled is False


def test_user_facing_filter_doc_deliverable_exists() -> None:
    text = FILTER_DOC.read_text(encoding="utf-8")

    required = [
        "zigzag_st_trade_filter_spec_v1_1.txt",
        "FSM: 5",
        "WAIT_FIRST_ST_FLIP",
        "ST_ACTIVE_FREEZE",
        "ST_ACTIVE_MONITORING",
        "ST_STOPPING",
        "export_state_columns",
        "export_trigger_columns",
        "FILTER_REASON_WHITELIST",
        "build_zigzag_global_stats",
        "Migration guide",
        "Excel export",
    ]
    missing = [needle for needle in required if needle not in text]
    assert not missing, f"Missing required doc markers: {missing}"
