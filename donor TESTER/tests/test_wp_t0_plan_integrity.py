"""
WP-T0 governance gate test.

Phase 2 implementation plan v0.5.2 §14 WP-T0 defines this work-package as
a *plan-review gate* with no code deliverable. This test makes the gate
executable: it asserts the structural invariants required for WP-T0 close.

Spec reference: Appendix A v1.1 §0, §11, §18
Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §14 WP-T0
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = REPO_ROOT / "docs" / "zigzag_st_tester_phase2_implementation_plan.txt"
SPEC_PATH = REPO_ROOT / "docs" / "zigzag_st_trade_filter_spec_v1_1.txt"


@pytest.fixture(scope="module")
def plan_text() -> str:
    assert PLAN_PATH.exists(), f"Phase 2 plan missing: {PLAN_PATH}"
    return PLAN_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def spec_text() -> str:
    assert SPEC_PATH.exists(), f"Appendix A v1.1 spec missing: {SPEC_PATH}"
    return SPEC_PATH.read_text(encoding="utf-8")


def test_spec_v1_1_present(spec_text: str) -> None:
    """Appendix A v1.1 must exist as the single source of truth."""
    assert "Appendix A — ZigZag ST Trade Filter Spec v1.1" in spec_text
    assert "## 17. Acceptance criteria" in spec_text
    assert "## 18. Правило против drift" in spec_text


def test_plan_version_v0_5_2(plan_text: str) -> None:
    """Plan must be published as v0.5.2 (latest internal-consistency pass)."""
    assert "Plan version: v0.5.2" in plan_text


def test_plan_anchors_appendix_a(plan_text: str) -> None:
    """Plan must explicitly anchor Appendix A v1.1 as source of truth."""
    assert "Source of truth: docs/zigzag_st_trade_filter_spec_v1_1.txt — Appendix A v1.1" in plan_text


def test_all_work_packages_present(plan_text: str) -> None:
    """All 10 work-packages WP-T0..WP-T9 must be defined as section headers."""
    missing = [n for n in range(10) if f"### WP-T{n}." not in plan_text]
    assert not missing, f"Plan is missing work-packages: WP-T{missing}"


def test_each_work_package_has_spec_reference(plan_text: str) -> None:
    """Each WP-T section must contain at least one Spec reference (§3 plan rule)."""
    wp_pattern = re.compile(r"^### WP-T(\d)\.[^\n]*\n(.*?)(?=^### WP-T\d|\Z)", re.MULTILINE | re.DOTALL)
    spec_ref_pattern = re.compile(r"^Spec reference: Appendix A v1\.1 §", re.MULTILINE)
    bad = []
    for match in wp_pattern.finditer(plan_text):
        wp_num = match.group(1)
        body = match.group(2)
        if not spec_ref_pattern.search(body):
            bad.append(f"WP-T{wp_num}")
    assert not bad, f"Work-packages missing Spec reference: {bad}"


def test_no_active_owner_questions(plan_text: str) -> None:
    """§15 must state that no active owner-approval questions remain."""
    assert "Активные вопросы (требуют owner approval): **НЕТ**" in plan_text


def test_owner_decisions_table_complete(plan_text: str) -> None:
    """All §15 #1, #2, #3, #4, #7, #8, #9 must be marked closed in shape header table."""
    expected_ids = ["#1", "#2", "#3", "#4", "#7", "#8", "#9"]
    for q_id in expected_ids:
        row_pattern = re.compile(rf"^\| {re.escape(q_id)} \|.*-> закрыт.*\|$", re.MULTILINE)
        assert row_pattern.search(plan_text), f"Owner decision {q_id} not closed in shape header table"


def test_implementation_order_section_present(plan_text: str) -> None:
    """§16 must define explicit implementation order WP-T0 -> ... -> WP-T9."""
    assert "## 16. Suggested implementation order" in plan_text
    for n in range(10):
        assert f"WP-T{n}" in plan_text


def test_legacy_only_scope_locked(plan_text: str) -> None:
    """Plan v0.4+ locks scope: enabled filter only with segmentation.mode=legacy."""
    assert "legacy-only scope" in plan_text.lower()
    assert "equal_blocks" in plan_text


def test_mode_c_locked_in_section_3_1(plan_text: str) -> None:
    """§3.1 must lock Mode C as the only viable code-reuse mode."""
    assert "### 3.1 Code reuse / synchronization model — Mode C is the only viable path" in plan_text
