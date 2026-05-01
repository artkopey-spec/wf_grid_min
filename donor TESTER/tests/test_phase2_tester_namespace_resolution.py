"""
Test #27 (audit-fix v0.5; plan §10 / §14 WP-T3 step 5) —
**Mode-C namespace resolution gate**.

Pin the contract that EVERY ``supertrend_optimizer.*`` symbol consumed by the
tester pipeline (engine, core, cli, testing, io, top-level) resolves from
the active donor package ``donor/supertrend_optimizer/``, never from
``donor TESTER/supertrend_optimizer/``.

Why a separate file from ``test_wp_t2_packaging_smoke.py``?
    The smoke test is a narrow B-2 unblocker gate (top-level + a few
    submodules). This file is the broader Mode-C resolution contract enforced
    after the WP-T3 dedup pass — covering EVERY runtime-critical module.

A failing assertion here means a stale ``donor TESTER/`` copy of the named
module has been resurrected, OR ``donor/`` has been removed from
``sys.path[0]`` ordering.

Spec reference: Appendix A v1.1 §13 (filter integration surface)
Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt
                §3.1 (Mode C), §10 #27, §14 WP-T3 step 5, §15 #9 (BLOCKER B-2)
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DONOR_PKG_ROOT = (REPO_ROOT / "donor" / "supertrend_optimizer").resolve()
TESTER_PKG_ROOT = (REPO_ROOT / "donor TESTER" / "supertrend_optimizer").resolve()


# Modules pinned to donor/.  Each MUST be a regular .py file under DONOR_PKG_ROOT.
# Order matches plan §13 file impact table grouping (engine, core, cli, testing,
# io, top-level helpers).
DONOR_RESOLVED_MODULES: list[str] = [
    "supertrend_optimizer",
    "supertrend_optimizer.engine",
    "supertrend_optimizer.engine.run",
    "supertrend_optimizer.engine.result",
    "supertrend_optimizer.core",
    "supertrend_optimizer.core.backtest",
    "supertrend_optimizer.core.zigzag_st_filter",
    "supertrend_optimizer.core.trade_filter_config",
    "supertrend_optimizer.core.trades",
    "supertrend_optimizer.core.metrics",
    "supertrend_optimizer.core.calculator",
    "supertrend_optimizer.cli",
    "supertrend_optimizer.cli.tester",
    "supertrend_optimizer.testing",
    "supertrend_optimizer.testing.runner",
    "supertrend_optimizer.testing.signal_events",
    "supertrend_optimizer.io.excel_tester",
    "supertrend_optimizer.io.excel_format_helpers",
    "supertrend_optimizer.utils.enums",
    "supertrend_optimizer.utils.exceptions",
    "supertrend_optimizer.utils.warmup",
    "supertrend_optimizer.utils.config",
    "supertrend_optimizer.data.loader",
    "supertrend_optimizer.data.validator",
    "supertrend_optimizer.data.timeframe",
]


def _module_file(module_name: str) -> Path:
    """Return ``__file__`` (or ``__path__[0]`` for namespace packages)."""
    mod = importlib.import_module(module_name)
    file_attr = getattr(mod, "__file__", None)
    if file_attr is not None:
        return Path(file_attr).resolve()
    # Namespace packages (e.g. supertrend_optimizer.io has no __init__.py)
    paths = list(getattr(mod, "__path__", []))
    if not paths:
        raise AssertionError(
            f"{module_name!r} has neither __file__ nor __path__"
        )
    return Path(paths[0]).resolve()


class TestModeCResolution:
    """Every pinned module MUST resolve from ``donor/supertrend_optimizer/``."""

    @pytest.mark.parametrize("module_name", DONOR_RESOLVED_MODULES)
    def test_resolved_under_donor_root(self, module_name: str) -> None:
        path = _module_file(module_name)
        assert (
            path == DONOR_PKG_ROOT
            or path == DONOR_PKG_ROOT / "__init__.py"
            or DONOR_PKG_ROOT in path.parents
        ), (
            f"{module_name} resolved from {path}, expected under "
            f"{DONOR_PKG_ROOT}. WP-T3 namespace contract violated."
        )

    @pytest.mark.parametrize("module_name", DONOR_RESOLVED_MODULES)
    def test_not_resolved_from_donor_tester(self, module_name: str) -> None:
        path = _module_file(module_name)
        assert TESTER_PKG_ROOT not in path.parents, (
            f"{module_name} resolved from donor TESTER/ ({path}). "
            "BLOCKER B-2 regression."
        )


class TestDeletedTesterArtifacts:
    """Files explicitly removed in WP-T3 dedup pass MUST stay removed."""

    DELETED_FILES = [
        "engine/run.py",
        "engine/result.py",
        "engine/__init__.py",
        "core/backtest.py",
        "core/trades.py",
        "core/metrics.py",
        "core/calculator.py",
        "core/__init__.py",
        "testing/runner.py",
        "testing/signal_events.py",
        "testing/__init__.py",
        "io/excel_tester.py",
        "io/excel_format_helpers.py",
        "cli/tester.py",
        "cli/__init__.py",
    ]

    @pytest.mark.parametrize("relpath", DELETED_FILES)
    def test_tester_duplicate_deleted(self, relpath: str) -> None:
        full = TESTER_PKG_ROOT / relpath
        assert not full.exists(), (
            f"WP-T3 dedup violated: {full} reappeared. "
            "Either it was restored by mistake, or the dedup_log row is stale."
        )


class TestRuntimeContractDocumented:
    """Sanity: top-level package version + path exposed for downstream debug."""

    def test_top_level_version_set(self) -> None:
        import supertrend_optimizer

        assert hasattr(supertrend_optimizer, "__version__")
        assert isinstance(supertrend_optimizer.__version__, str)
        assert supertrend_optimizer.__version__  # non-empty

    def test_top_level_file_under_donor(self) -> None:
        import supertrend_optimizer

        assert (
            Path(supertrend_optimizer.__file__).resolve()
            == DONOR_PKG_ROOT / "__init__.py"
        )
