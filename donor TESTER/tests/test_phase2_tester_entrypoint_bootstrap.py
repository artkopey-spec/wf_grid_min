"""
Production-entrypoint smoke gate (WP-T3 follow-up, owner audit-fix v0.5.2).

Why a separate file from the in-process namespace tests?
    ``test_phase2_tester_namespace_resolution.py`` and the assert in
    ``conftest.py`` only exercise the import contract under a pytest session,
    where the conftest fixture already manipulates ``sys.path``.  They do
    NOT catch the case where a user runs the ACTUAL production CLI:

        cd "donor TESTER"
        python run_batch_tester.py --help

    Without the in-script bootstrap added in WP-T3, that command raised
    ``ModuleNotFoundError: No module named 'supertrend_optimizer.cli.tester'``
    because:

      * Python sets ``sys.path[0]`` to the script's directory
        (``donor TESTER/``);
      * ``donor TESTER/supertrend_optimizer/__init__.py`` still ships as a
        legacy package marker (KEPT in WP-T3 dedup decision);
      * resolution wins for the legacy subtree but the submodule
        ``supertrend_optimizer.cli.tester`` was DELETED in WP-T3, so the
        import fails.

    The bootstrap at the top of ``run_batch_tester.py`` puts
    ``<repo>/donor`` ahead of ``<repo>/donor TESTER`` on ``sys.path``
    BEFORE any ``supertrend_optimizer.*`` import is evaluated.

These tests run the entrypoint via ``subprocess.run`` so pytest's
``conftest.py`` ``sys.path`` manipulation is **not** in scope, and the
result reflects exactly what the user sees when invoking the CLI.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §14
                WP-T3 + audit-fix v0.5.2 (production entrypoint smoke).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TESTER_ROOT = REPO_ROOT / "donor TESTER"
ENTRYPOINT = TESTER_ROOT / "run_batch_tester.py"
DONOR_ROOT = REPO_ROOT / "donor"


def _clean_env() -> dict[str, str]:
    """Subprocess env with PYTHONPATH stripped.

    Stripping PYTHONPATH ensures the test reflects ONLY the in-script
    bootstrap, not whatever the developer happens to have in their shell.
    """
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    # Force unbuffered stdout so --help output is captured immediately.
    env["PYTHONUNBUFFERED"] = "1"
    # Ensure UTF-8 on Windows so we can decode subprocess output deterministically.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_entrypoint(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Invoke ``run_batch_tester.py`` as a subprocess from ``cwd``."""
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *args],
        cwd=str(cwd),
        env=_clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


class TestEntrypointFileExists:
    """Sanity: the production entrypoint and the active donor are present."""

    def test_run_batch_tester_present(self) -> None:
        assert ENTRYPOINT.is_file(), (
            f"Production entrypoint missing: {ENTRYPOINT}"
        )

    def test_donor_supertrend_optimizer_present(self) -> None:
        assert (DONOR_ROOT / "supertrend_optimizer" / "__init__.py").is_file()

    def test_legacy_cli_tester_deleted(self) -> None:
        """Pre-condition the bootstrap is designed against."""
        legacy = TESTER_ROOT / "supertrend_optimizer" / "cli" / "tester.py"
        assert not legacy.exists(), (
            "Legacy cli/tester.py was restored — the bootstrap is no longer "
            "needed and this test (and the bootstrap itself) should be reviewed."
        )


class TestProductionEntrypointBootstrap:
    """The actual gate: run the CLI and verify it imports cleanly."""

    def test_cli_help_from_tester_root_succeeds(self) -> None:
        """Owner repro: ``cd "donor TESTER" && python run_batch_tester.py --help``.

        Before the bootstrap landed, this raised ``ModuleNotFoundError``.
        After the bootstrap, ``--help`` exits 0 and prints argparse usage.
        """
        result = _run_entrypoint("--help", cwd=TESTER_ROOT)

        assert result.returncode == 0, (
            f"Production CLI failed with exit code {result.returncode}.\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
        # argparse always emits "usage:" on --help.
        combined = (result.stdout or "") + (result.stderr or "")
        assert "usage:" in combined.lower(), (
            "argparse --help output not detected; bootstrap may have died "
            "silently before argparse ran.\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )

    def test_cli_help_from_repo_root_succeeds(self) -> None:
        """Same gate but with cwd = repo root (different ``sys.path[0]``).

        When invoked as ``python "donor TESTER/run_batch_tester.py" --help``
        from the repo root, ``sys.path[0]`` becomes the script's directory
        (``donor TESTER/``), reproducing the original break even though cwd
        differs.
        """
        result = _run_entrypoint("--help", cwd=REPO_ROOT)

        assert result.returncode == 0, (
            f"Production CLI failed with exit code {result.returncode}.\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )

    def test_no_modulenotfound_on_supertrend_optimizer(self) -> None:
        """The specific regression we are pinning."""
        result = _run_entrypoint("--help", cwd=TESTER_ROOT)
        haystack = (result.stdout or "") + (result.stderr or "")
        assert "ModuleNotFoundError" not in haystack, (
            "ModuleNotFoundError leaked into CLI output:\n" + haystack
        )
        assert (
            "No module named 'supertrend_optimizer" not in haystack
        ), (
            "Stale import path leaked into CLI output:\n" + haystack
        )

    def test_bootstrap_assertion_message_format_pinned(self) -> None:
        """If a future regression breaks the bootstrap, the error message
        format is documented and stable."""
        # We can't trigger the assert from the test (would require sabotaging
        # the file), but we CAN pin the exact substrings in the source that
        # the assert uses.  Future refactors that drop these substrings would
        # silently weaken the diagnostic.
        src = ENTRYPOINT.read_text(encoding="utf-8")
        assert "Mode-C bootstrap" in src, (
            "Bootstrap diagnostic substring missing from run_batch_tester.py"
        )
        assert "expected" in src
        assert "PYTHONPATH" in src


class TestImportContractFromSubprocess:
    """End-to-end: subprocess imports the entrypoint module and asserts
    every WP-T3 symbol resolves under donor/.

    This complements ``test_phase2_tester_namespace_resolution.py`` —
    that file runs under pytest (conftest.py controls sys.path); this one
    runs in a fresh interpreter where only the in-script bootstrap is
    in effect.
    """

    PROBE_SCRIPT = """
import sys
from pathlib import Path

# Mimic the production invocation by adding the script dir to sys.path[0]
# (Python does this automatically when running ``python script.py``).
script_dir = Path(r"{tester_root}")
sys.path.insert(0, str(script_dir))

# Import the entrypoint module — this triggers its in-script bootstrap.
import importlib.util
spec = importlib.util.spec_from_file_location(
    "_run_batch_tester_probe", script_dir / "run_batch_tester.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

import supertrend_optimizer
import supertrend_optimizer.cli.tester
import supertrend_optimizer.engine.run
import supertrend_optimizer.core.backtest
import supertrend_optimizer.core.zigzag_st_filter
import supertrend_optimizer.core.trade_filter_config

donor_root = Path(r"{donor_root}").resolve()
expected_top = (donor_root / "supertrend_optimizer" / "__init__.py").resolve()

for name, mod in [
    ("supertrend_optimizer", supertrend_optimizer),
    ("supertrend_optimizer.cli.tester", supertrend_optimizer.cli.tester),
    ("supertrend_optimizer.engine.run", supertrend_optimizer.engine.run),
    ("supertrend_optimizer.core.backtest", supertrend_optimizer.core.backtest),
    ("supertrend_optimizer.core.zigzag_st_filter", supertrend_optimizer.core.zigzag_st_filter),
    ("supertrend_optimizer.core.trade_filter_config", supertrend_optimizer.core.trade_filter_config),
]:
    p = Path(mod.__file__).resolve()
    assert donor_root in p.parents or p == expected_top, (
        f"{{name}} resolved from {{p}}, expected under {{donor_root}}"
    )

print("OK")
"""

    def test_probe_script_succeeds_in_fresh_interpreter(self) -> None:
        probe = self.PROBE_SCRIPT.format(
            tester_root=str(TESTER_ROOT),
            donor_root=str(DONOR_ROOT),
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            env=_clean_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, (
            f"Subprocess probe failed (exit {result.returncode}).\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
        assert result.stdout.strip().endswith("OK"), (
            f"Probe did not print OK marker.  stdout:\n{result.stdout}"
        )
