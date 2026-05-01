"""
WP-T2 / B-2 unblocker — packaging smoke test.

Pins the contract that the ``supertrend_optimizer`` top-level package resolves
from ``donor/`` (the active donor / Mode C runtime root), NOT from
``donor TESTER/``. This is the WP-T3 step 0a-0c assertion authorized to land
inside WP-T2 narrowly because cli/tester.py duplication blocks WP-T2 imports
otherwise.

If this test fails, WP-T2 / WP-T3 / WP-T4 imports cannot be trusted: the
single-source-of-truth ``trade_filter_config`` module would resolve from a
stale or wrong location.

Plan references:
    * v0.5.2 §15 #9 — packaging asymmetry (BLOCKER B-2).
    * WP-T3 step 0a-0c (authorized narrowly inside WP-T2).
    * docs/zigzag_st_tester_phase2_implementation_plan.txt §3.1 (Mode C).
"""

from __future__ import annotations

from pathlib import Path

import pytest


# conftest.py guarantees donor/ precedes donor TESTER/ in sys.path.
import supertrend_optimizer  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DONOR_PKG_INIT = REPO_ROOT / "donor" / "supertrend_optimizer" / "__init__.py"
TESTER_PKG_INIT = REPO_ROOT / "donor TESTER" / "supertrend_optimizer" / "__init__.py"


class TestPackagingContract:
    """The supertrend_optimizer top-level MUST be a regular package from donor/."""

    def test_top_level_file_attribute_is_set(self) -> None:
        """Regular packages have ``__file__`` set; namespace packages do not.

        If this fails to ``None``, the package degenerated to a namespace
        package — usually because ``donor/supertrend_optimizer/__init__.py``
        was deleted or never created.
        """
        assert supertrend_optimizer.__file__ is not None, (
            "supertrend_optimizer is a namespace package (__file__ is None). "
            "Expected regular package. Check that "
            "donor/supertrend_optimizer/__init__.py exists."
        )

    def test_top_level_resolves_from_active_donor(self) -> None:
        """``supertrend_optimizer.__file__`` MUST point inside ``donor/``."""
        actual = Path(supertrend_optimizer.__file__).resolve()
        expected = DONOR_PKG_INIT.resolve()
        assert actual == expected, (
            f"supertrend_optimizer resolved from {actual}, expected {expected}. "
            "Check sys.path order: donor/ MUST precede donor TESTER/ "
            "(see donor TESTER/tests/conftest.py)."
        )

    def test_top_level_does_not_resolve_from_donor_tester(self) -> None:
        """Negative check — donor TESTER/ MUST NOT win the top-level race."""
        actual = Path(supertrend_optimizer.__file__).resolve()
        forbidden = TESTER_PKG_INIT.resolve()
        assert actual != forbidden, (
            "supertrend_optimizer resolved from donor TESTER/ — wrong winner. "
            "Plan §3.1 / WP-T3 step 0a-0c contract violated."
        )

    def test_package_path_is_single_directory(self) -> None:
        """Regular package — ``__path__`` is a single directory inside donor/."""
        paths = [Path(p).resolve() for p in supertrend_optimizer.__path__]
        assert len(paths) == 1, (
            f"Expected single-entry __path__ for regular package, got {paths}. "
            "Multiple entries imply namespace package merge — not allowed in "
            "Mode C until WP-T3 step 0d resolves dedup."
        )
        assert paths[0] == DONOR_PKG_INIT.parent.resolve(), (
            f"__path__[0] = {paths[0]}, expected {DONOR_PKG_INIT.parent}."
        )


class TestSubmoduleResolution:
    """Submodule resolution follows the top-level package — also from donor/."""

    @pytest.mark.parametrize(
        "module_name",
        [
            "supertrend_optimizer.core",
            "supertrend_optimizer.cli",
            "supertrend_optimizer.engine",
        ],
    )
    def test_core_submodules_resolve_from_donor(self, module_name: str) -> None:
        """Submodules under the resolved top-level all live in donor/."""
        import importlib

        mod = importlib.import_module(module_name)
        mod_file = getattr(mod, "__file__", None)
        if mod_file is None:
            mod_path = list(mod.__path__)
            assert mod_path, f"{module_name} has neither __file__ nor __path__"
            mod_file = mod_path[0]
        actual = Path(mod_file).resolve()
        donor_root = (REPO_ROOT / "donor").resolve()
        assert donor_root in actual.parents or actual.parent == donor_root, (
            f"{module_name} resolved from {actual} — expected under {donor_root}."
        )

    def test_trade_filter_config_module_importable(self) -> None:
        """The new shared module created in WP-T2 prerequisite refactor."""
        from supertrend_optimizer.core import trade_filter_config

        actual = Path(trade_filter_config.__file__).resolve()
        expected = (
            REPO_ROOT
            / "donor"
            / "supertrend_optimizer"
            / "core"
            / "trade_filter_config.py"
        ).resolve()
        assert actual == expected, (
            f"trade_filter_config resolved from {actual}, expected {expected}."
        )
