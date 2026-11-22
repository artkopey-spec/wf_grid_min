from __future__ import annotations

from pathlib import Path

from supertrend_optimizer.cli.tester import load_tester_config
from supertrend_optimizer.core.trade_filter_config import (
    resolve_trade_filter_mode,
    trade_filter_effective_snapshot,
)
from wf_grid.config.loader import load_grid_config


ROOT = Path(__file__).resolve().parents[2]


def test_production_profiles_share_time_windows_after_reversal_activation() -> None:
    wf_config = load_grid_config(str(ROOT / "config.yaml"))
    tester_config = load_tester_config(str(ROOT / "config_tester.yaml"))

    wf_filter = wf_config.trade_filter
    tester_filter = tester_config["trade_filter"]
    assert resolve_trade_filter_mode(wf_filter) == "time_only"
    assert resolve_trade_filter_mode(tester_filter) == "time_only"
    wf_snapshot = trade_filter_effective_snapshot(wf_filter)
    tester_snapshot = trade_filter_effective_snapshot(tester_filter)
    assert (
        wf_snapshot["canonical_windows_minutes"]
        == tester_snapshot["canonical_windows_minutes"]
    )
    assert wf_snapshot["time_filter_active"] is True
    assert tester_snapshot["time_filter_active"] is True
    assert "reversal_confirmation_active" not in tester_snapshot


def test_runtime_spec_contains_required_migration_contracts() -> None:
    spec = (ROOT / "docs" / "time_filter_spec.md").read_text(encoding="utf-8")
    migration = (
        ROOT / "docs" / "time_filter_multi_window_migration.md"
    ).read_text(encoding="utf-8")

    required_spec_terms = (
        "calendar-cycle reset",
        "touching",
        "Timezone",
        "open-to-open",
        "gap exposure",
        "Entry attempts",
        "Reset priority",
        "Precompute",
        "Diagnostics capabilities",
        "trade_filter_public_snapshot",
        "trade_filter_effective_snapshot",
        "equal_blocks",
        "skipped_unsupported",
    )
    for term in required_spec_terms:
        assert term.lower() in spec.lower()

    for term in (
        "Ожидаемая дельта",
        "Перед запуском",
        "Проверка",
        "Rollback",
        "09:00-12:00",
        "16:00-18:00",
    ):
        assert term in migration


def test_classified_baseline_failure_manifest_is_exact_and_complete() -> None:
    manifest = (
        ROOT / "docs" / "time_filter_baseline_failures_20260805.md"
    ).read_text(encoding="utf-8")
    node_ids = [
        line.removeprefix("FAILED ")
        for line in manifest.splitlines()
        if line.startswith("FAILED ")
    ]

    assert len(node_ids) == 66
    assert len(set(node_ids)) == 66
    assert "## A. Current FINAM CSV read through obsolete direct pandas path — 5" in manifest
    assert "## B. Annualization validation pre-empts the asserted validation — 2" in manifest
    assert "## C. Walk-forward fixture sizes no longer form a valid window — 9" in manifest
    assert "## D. Pre-existing mojibake expectations in Excel tests — 50" in manifest
