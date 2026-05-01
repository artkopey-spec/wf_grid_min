"""
CI guard: no bare `assert` statements in production code.

Scans all .py files under wf_grid/ (excluding tests/) and
donor/supertrend_optimizer/ (excluding test files) to ensure that no
`assert` statement has leaked into runtime-critical code.

Rationale: `assert` is disabled by Python's -O flag, making it
unsafe for production invariant checks.  All invariants must use
explicit `raise ValueError` / `raise RuntimeError` instead.

If this test fails, replace the offending `assert expr, msg` with:
    if not expr:
        raise ValueError(msg)
"""

from __future__ import annotations

import ast
import pathlib
from typing import List, Tuple

import pytest

# ---------------------------------------------------------------------------
# Paths to scan
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # c:\3.0

_SCAN_ROOTS = [
    _REPO_ROOT / "wf_grid",
    _REPO_ROOT / "donor" / "supertrend_optimizer",
]

# Directories/files that are allowed to contain assert (test code only).
_ALLOWED_DIRS = {
    "tests",
    "test",
}

_ALLOWED_FILENAME_PREFIXES = (
    "test_",
    "conftest",
)


def _is_test_file(path: pathlib.Path) -> bool:
    """Return True if path is a test file that may legitimately use assert."""
    # Any file inside a 'tests' or 'test' directory
    for part in path.parts:
        if part.lower() in _ALLOWED_DIRS:
            return True
    # Any file whose name starts with 'test_' or is conftest.py
    return any(path.name.startswith(p) for p in _ALLOWED_FILENAME_PREFIXES)


def _collect_production_py_files() -> List[pathlib.Path]:
    files = []
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            if not _is_test_file(py_file):
                files.append(py_file)
    return sorted(files)


def _find_assert_statements(path: pathlib.Path) -> List[Tuple[int, str]]:
    """Return list of (lineno, line_text) for each assert statement found."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Unparseable file — skip AST check, fall back to line scan.
        hits = []
        for i, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("assert ") or stripped == "assert":
                hits.append((i, line.rstrip()))
        return hits

    hits = []
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            lineno = node.lineno
            line_text = lines[lineno - 1].rstrip() if lineno <= len(lines) else ""
            hits.append((lineno, line_text))
    return hits


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_no_production_asserts() -> None:
    """Fail if any production .py file contains an assert statement."""
    production_files = _collect_production_py_files()
    violations: List[str] = []

    for py_file in production_files:
        hits = _find_assert_statements(py_file)
        for lineno, line_text in hits:
            rel = py_file.relative_to(_REPO_ROOT)
            violations.append(f"  {rel}:{lineno}  {line_text.strip()}")

    if violations:
        msg = (
            "Found assert statements in production code.\n"
            "Replace each `assert expr, msg` with `if not expr: raise ValueError(msg)`.\n\n"
            "Violations:\n" + "\n".join(violations)
        )
        pytest.fail(msg)
