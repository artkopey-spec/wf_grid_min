"""WP-T9 root rollout hardening.

These checks pin the production-facing root artifacts that sit outside the
canonical ``donor TESTER`` implementation:

* repo-root ``run_batch_tester.py`` remains a compatibility wrapper;
* repo-root ``config_tester.yaml`` loads via ``load_tester_config`` as the
  current operator-facing sample config;
* a dedicated fixture proves legacy ``zigzag_st_mode`` still loads when
  ``segmentation.mode: legacy``;
* the user-facing filter document exists and points to the spec.
"""

from pathlib import Path
import subprocess
import sys
from textwrap import dedent

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


def test_root_config_loads_as_current_operator_sample() -> None:
    text = ROOT_CONFIG.read_text(encoding="utf-8")

    assert "mode: legacy" in text
    assert "trade_filter:" in text
    assert "wakeup_regime:" in text

    cfg = load_tester_config(str(ROOT_CONFIG))
    tf_cfg = cfg.get("trade_filter")
    assert tf_cfg is not None
    assert tf_cfg.enabled is True
    assert tf_cfg.zigzag.mode == "D"
    assert tf_cfg.lifecycle.exit_off_mode == "exit C"
    assert tf_cfg.wakeup_regime is not None
    assert tf_cfg.wakeup_regime.enabled is True


def test_legacy_segmentation_accepts_zigzag_st_mode_fixture(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config_tester.yaml"
    cfg_path.write_text(
        dedent(
            """\
            supertrend:
              atr_period: 20
              multiplier: 1.0
            trade_mode: revers
            commission: 0.0
            warmup_period_auto: true
            periods_per_year: auto
            market: forex
            segmentation:
              mode: legacy
              n_parts: 3
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                enabled: true
                global_stats_source: full_dataset
                leg_height_mode: pct
                reversal_threshold: 0.005
                candidate_trigger_threshold: 0.012
                global_median: auto
                local_window: 5
              triggers:
                candidate_threshold:
                  enabled: true
                confirmed_median:
                  enabled: true
              lifecycle:
                freeze_confirmed_legs: 5
                stop_check: confirm_bar_only
                stopping_exit: opposite_st_flip
              diagnostics:
                export_state_columns: true
                export_trigger_columns: true
            """
        ),
        encoding="utf-8",
    )

    cfg = load_tester_config(str(cfg_path))
    tf_cfg = cfg.get("trade_filter")
    assert tf_cfg is not None
    assert tf_cfg.type == "zigzag_st_mode"
    assert tf_cfg.zigzag.enabled is True


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
