"""
Test #29 (audit-fix v0.5; plan §10 / §14 WP-T3 step 4) —
**static regression grep gate** for forbidden literals in tester paths.

This is a pure-text scan (no execution) that fails the suite if any of the
forbidden patterns reappears. After WP-T3 dedup the in-scope file set is
small; the gate's main job is to prevent **future regressions** in the form
of:

* re-introduced ``CLOSE_TO_CLOSE`` references (retired in plan §15 #2);
* re-introduced ``compute_zigzag_global_stats`` calls (renamed to
  ``build_zigzag_global_stats`` per spec §11);
* re-introduced 7-tuple unpack of ``run_backtest_fast`` (legacy signature
  before filter integration);
* re-introduced 2.0-TESTER ``filter_diagnostics`` keyset literals
  (``allow_entry``, ``filtered_reason``, ``zz_st_armed`` …) — the spec §13
  keyset is final.

Spec reference: Appendix A v1.1 §10, §13, §15.6
Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt
                §10 #29, §14 WP-T3 step 4 (audit-fix v0.5)

Scope (rationale documented inline):
    INCLUDED — files where regressions actually matter (tester runtime
    surface):
        donor TESTER/run_batch_tester.py
        donor TESTER/supertrend_optimizer/__init__.py
        donor TESTER/supertrend_optimizer/{engine,core,testing,io,cli}/**.py
            (after WP-T3 dedup these directories are empty; the scan walks
            them anyway so future re-introduction is caught)

    EXCLUDED:
        donor TESTER/supertrend_optimizer/{data,utils}/**.py
            -> out of WP-T3 owner audit scope (directive #1); these subtrees
            still contain stale stubs (incl. ``ExecutionModel.CLOSE_TO_CLOSE``
            in utils/enums.py) but never resolve at runtime since the WP-T2
            unblocker. Tracked under "Open items" in
            ``docs/wp_t3_tester_dedup_log.md``.
        donor TESTER/tests/**
            -> test files are allowed to reference forbidden tokens in
            negative test cases / docstrings.
        donor TESTER/tests/baselines/**
            -> XLSX binary baselines.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TESTER_ROOT = REPO_ROOT / "donor TESTER"

# In-scope file globs — recursive walk over each.
SCOPED_DIRS = [
    TESTER_ROOT / "supertrend_optimizer" / "engine",
    TESTER_ROOT / "supertrend_optimizer" / "core",
    TESTER_ROOT / "supertrend_optimizer" / "testing",
    TESTER_ROOT / "supertrend_optimizer" / "io",
    TESTER_ROOT / "supertrend_optimizer" / "cli",
]
SCOPED_FILES = [
    TESTER_ROOT / "run_batch_tester.py",
    TESTER_ROOT / "supertrend_optimizer" / "__init__.py",
]

# Out-of-scope (rationale in module docstring).
EXCLUDED_DIRS = {
    (TESTER_ROOT / "supertrend_optimizer" / "data").resolve(),
    (TESTER_ROOT / "supertrend_optimizer" / "utils").resolve(),
    (TESTER_ROOT / "tests").resolve(),
}


def _collect_python_files() -> list[Path]:
    files: list[Path] = []
    for d in SCOPED_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            resolved = p.resolve()
            if any(ex in resolved.parents for ex in EXCLUDED_DIRS):
                continue
            files.append(p)
    for p in SCOPED_FILES:
        if p.exists():
            files.append(p)
    return sorted(set(files))


SCOPED_PY_FILES = _collect_python_files()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Forbidden patterns
# ---------------------------------------------------------------------------

# §15 #2 — close_to_close was removed for look-ahead bias.
RE_CLOSE_TO_CLOSE = re.compile(r"CLOSE_TO_CLOSE|close_to_close")

# Old name for build_zigzag_global_stats. Spec / plan use the new name only.
RE_OLD_STATS_FN = re.compile(r"\bcompute_zigzag_global_stats\b")

# 7-tuple unpack of run_backtest_fast (legacy signature pre filter integration).
# Pattern matches: <ident>, <ident>, <ident>, <ident>, <ident>, <ident>,
# <ident> = run_backtest_fast(   — i.e. seven simple identifiers separated by
# commas, then ``= run_backtest_fast(``.
# Multi-line tolerated via DOTALL because real call sites span lines.
RE_RBF_7TUPLE = re.compile(
    r"(?:[A-Za-z_]\w*\s*,\s*){6}[A-Za-z_]\w*\s*=\s*run_backtest_fast\s*\(",
    re.DOTALL,
)

# 2.0-TESTER keyset literals (writer-paths regression).
TWO_O_TESTER_KEYSET = (
    "'allow_entry'",
    '"allow_entry"',
    "'filtered_reason'",
    '"filtered_reason"',
    "'zz_st_armed'",
    '"zz_st_armed"',
    "'zz_st_locked_'",
    '"zz_st_locked_"',
    "'zz_st_expired_'",
    '"zz_st_expired_"',
    "'zz_st_regime_'",
    '"zz_st_regime_"',
)

# Relative cross-imports `from donor TESTER.supertrend_optimizer....`
# This regex catches both PEP-8 spelling and module-style. In practice the
# space in "donor TESTER" makes such an import impossible without a
# rename, but we still pin it explicitly.
RE_RELATIVE_TESTER_IMPORT = re.compile(
    r"from\s+donor\s*TESTER\s*\.\s*supertrend_optimizer\.\s*(?:engine|core)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _grep(regex: re.Pattern[str], text: str) -> list[int]:
    """Return 1-based line numbers where ``regex`` matches."""
    return [
        i
        for i, line in enumerate(text.splitlines(), start=1)
        if regex.search(line)
    ]


def _grep_literal(literals: tuple[str, ...], text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for lit in literals:
            if lit in line:
                out.append((i, lit))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScopedFileSet:
    """Sanity: scope discovery returns at least the static-listed files."""

    def test_run_batch_tester_in_scope(self) -> None:
        assert (
            TESTER_ROOT / "run_batch_tester.py"
        ) in SCOPED_PY_FILES, "Scope discovery missed run_batch_tester.py"

    def test_top_level_init_in_scope(self) -> None:
        assert (
            TESTER_ROOT / "supertrend_optimizer" / "__init__.py"
        ) in SCOPED_PY_FILES, "Scope discovery missed top-level __init__.py"

    def test_excluded_dirs_not_in_scope(self) -> None:
        for f in SCOPED_PY_FILES:
            for excluded in EXCLUDED_DIRS:
                assert excluded not in f.resolve().parents, (
                    f"Scope discovery leaked into excluded dir: {f}"
                )


class TestNoCloseToClose:
    """Plan §15 #2 — close_to_close MUST NOT appear in scoped tester paths."""

    @pytest.mark.parametrize("path", SCOPED_PY_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
    def test_no_close_to_close(self, path: Path) -> None:
        text = _read(path)
        hits = _grep(RE_CLOSE_TO_CLOSE, text)
        assert not hits, (
            f"{path.relative_to(REPO_ROOT)}: CLOSE_TO_CLOSE references at "
            f"lines {hits}. close_to_close was retired in plan §15 #2 "
            "(look-ahead bias)."
        )


class TestNoOldStatsFn:
    """Spec §11 — only ``build_zigzag_global_stats`` is allowed."""

    @pytest.mark.parametrize("path", SCOPED_PY_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
    def test_no_compute_zigzag_global_stats(self, path: Path) -> None:
        text = _read(path)
        hits = _grep(RE_OLD_STATS_FN, text)
        assert not hits, (
            f"{path.relative_to(REPO_ROOT)}: stale "
            f"`compute_zigzag_global_stats` reference at lines {hits}. "
            "Use `build_zigzag_global_stats` (spec §11)."
        )


class TestNoSevenTupleUnpack:
    """Plan §14 WP-T3 step 4 — 7-tuple unpack of run_backtest_fast forbidden."""

    @pytest.mark.parametrize("path", SCOPED_PY_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
    def test_no_run_backtest_fast_7tuple_unpack(self, path: Path) -> None:
        text = _read(path)
        m = RE_RBF_7TUPLE.search(text)
        assert m is None, (
            f"{path.relative_to(REPO_ROOT)}: 7-tuple unpack of "
            "`run_backtest_fast` detected. The function returns "
            "RawBacktestArtifacts after filter integration."
        )


class TestNoTwoOTesterKeyset:
    """Audit-fix v0.5 — writer-paths must not reintroduce 2.0-TESTER keyset.

    Forbidden tokens are the 2.0-TESTER spec's ``filter_diagnostics`` keys
    that were superseded by the spec §13 keyset.
    """

    @pytest.mark.parametrize("path", SCOPED_PY_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
    def test_no_two_o_tester_keyset_literals(self, path: Path) -> None:
        text = _read(path)
        hits = _grep_literal(TWO_O_TESTER_KEYSET, text)
        assert not hits, (
            f"{path.relative_to(REPO_ROOT)}: 2.0-TESTER keyset literal at "
            f"{hits}. Spec §13 forbids these."
        )


class TestNoRelativeTesterImport:
    """No tester-side cross-imports of engine/core (plan §14 WP-T3 step 4)."""

    @pytest.mark.parametrize("path", SCOPED_PY_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
    def test_no_relative_tester_engine_core_import(self, path: Path) -> None:
        text = _read(path)
        hits = _grep(RE_RELATIVE_TESTER_IMPORT, text)
        assert not hits, (
            f"{path.relative_to(REPO_ROOT)}: relative cross-import from "
            f"`donor TESTER.supertrend_optimizer.{{engine,core}}` at lines "
            f"{hits}. Use the canonical `supertrend_optimizer.*` namespace."
        )
