"""
Pytest configuration for donor TESTER/tests/.

Adds the tester package roots to sys.path so test modules can `import
supertrend_optimizer.*` without manual sys.path mutation in every file.

Order contract (BLOCKER B-2 unblocker, plan WP-T3 step 0a-0c authorized inside
WP-T2 only for cli/tester.py):
    sys.path[0] = donor/              (active donor — wins for top-level package)
    sys.path[1] = donor TESTER/       (legacy tester — only resolved if a name
                                       does NOT exist in donor/)

After WP-T3 hard-delete, `donor TESTER/` becomes empty/shim and the order
contract degenerates to "only donor/ matters". Until then, this file is the
ONE place the order is enforced.

NOTE: the previous `for path in (DONOR, TESTER): sys.path.insert(0, path)`
inverted the order (TESTER ended up at sys.path[0]). Fixed below by inserting
in reverse iteration so the LAST insert (DONOR) sits at index 0. We also
remove any pre-existing copies to make the contract idempotent across
reloads / nested pytest sessions.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DONOR_ROOT = REPO_ROOT / "donor"
TESTER_ROOT = REPO_ROOT / "donor TESTER"

for path in (str(TESTER_ROOT), str(DONOR_ROOT)):
    while path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)


# ---------------------------------------------------------------------------
# WP-T3 step 5 — runtime namespace assert (extended audit-fix v0.5.1 step 0d).
#
# Both `supertrend_optimizer` (top-level) AND `supertrend_optimizer.engine.run`
# MUST resolve from the active donor package. After WP-T3 dedup pass, the
# tester subtree no longer ships engine.run / engine.result / core.* — so the
# assert here additionally guarantees that nothing accidentally restores them.
#
# Fail-fast at conftest load time => any test session imports surface this
# regression immediately, BEFORE any test is parametrised or collected.
# ---------------------------------------------------------------------------

import supertrend_optimizer as _so_pkg  # noqa: E402
import supertrend_optimizer.engine.run as _so_run  # noqa: E402

_donor_so_root = (DONOR_ROOT / "supertrend_optimizer").resolve()

_top_level_path = Path(_so_pkg.__file__).resolve()
assert _top_level_path == (_donor_so_root / "__init__.py"), (
    "BLOCKER B-2 regression: supertrend_optimizer top-level resolved from "
    f"{_top_level_path}, expected {_donor_so_root / '__init__.py'}. "
    "Check sys.path order and donor/__init__.py existence."
)

_engine_run_path = Path(_so_run.__file__).resolve()
assert _donor_so_root in _engine_run_path.parents, (
    "WP-T3 regression: supertrend_optimizer.engine.run resolved from "
    f"{_engine_run_path}, expected under {_donor_so_root}. "
    "Check that donor TESTER/supertrend_optimizer/engine/run.py is NOT restored."
)
