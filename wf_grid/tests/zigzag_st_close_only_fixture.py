"""
Shared close-only ZigZag fixture (plan §3.3 step 8).

A single fixture defines ``close[]``, ``reversal_threshold``, expected
confirmed legs, ``confirmed_heights_pct`` and ``global_median``.  It is
intentionally reused across WP3 (this module's tests), WP4 (per-bar engine)
and WP5 (FSM unit tests).

All height metrics are stored as fractions of price.  Despite the ``_pct``
suffix v1.1 stores heights as fractions, NOT in the 0..100 scale
(Appendix A v1.1 §3.2 last paragraph).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np


@dataclass(frozen=True)
class ExpectedLeg:
    """Expected ConfirmedLeg fields for assertions."""
    start_bar: int
    end_bar: int
    confirm_bar: int
    start_price: float
    end_price: float
    direction: int
    height_pct: float


@dataclass(frozen=True)
class CloseOnlyFixture:
    """A reproducible close-only ZigZag scenario.

    Attributes
    ----------
    name : str
        Human-readable label.  Useful for parametrised tests.
    close : np.ndarray
        1-D close price array.
    reversal_threshold : float
        Numeric fraction in (0, 1).  This is the *single* threshold the
        helper uses to detect pivots; close-only by contract.
    expected_legs : list[ExpectedLeg]
        Confirmed legs in chronological order.
    expected_heights_pct : np.ndarray
        Same heights as ``expected_legs``, broken out for direct comparison
        with ``ZigZagGlobalStats.confirmed_heights_pct``.
    expected_global_median : float
        Median of ``expected_heights_pct``.
    """
    name: str
    close: np.ndarray
    reversal_threshold: float
    expected_legs: List[ExpectedLeg]
    expected_heights_pct: np.ndarray
    expected_global_median: float


# ---------------------------------------------------------------------------
# Fixture #1: simple zigzag with five confirmed legs.
# Walk-through (r = 0.02):
#   t=2  close=102 establishes UP from initial pivot at (0, 100)
#   t=3  close=99  reverses; emit leg #1 UP   (0..2,  height = 0.02)
#   t=5  close=105 reverses; emit leg #2 DOWN (2..3,  height = 3/102)
#   t=6  close=102 reverses; emit leg #3 UP   (3..5,  height = 6/99)
#   t=7  close=108 reverses; emit leg #4 DOWN (5..6,  height = 3/105)
#   t=8  close=103 reverses; emit leg #5 UP   (6..7,  height = 6/102)
# ---------------------------------------------------------------------------

_SIMPLE_CLOSE: np.ndarray = np.array(
    [100.0, 101.0, 102.0, 99.0, 100.0, 105.0, 102.0, 108.0, 103.0],
    dtype=np.float64,
)
_SIMPLE_R: float = 0.02

_SIMPLE_LEGS: List[ExpectedLeg] = [
    ExpectedLeg(0, 2, 3, 100.0, 102.0, +1, 2.0 / 100.0),
    ExpectedLeg(2, 3, 5, 102.0,  99.0, -1, 3.0 / 102.0),
    ExpectedLeg(3, 5, 6,  99.0, 105.0, +1, 6.0 / 99.0),
    ExpectedLeg(5, 6, 7, 105.0, 102.0, -1, 3.0 / 105.0),
    ExpectedLeg(6, 7, 8, 102.0, 108.0, +1, 6.0 / 102.0),
]
_SIMPLE_HEIGHTS: np.ndarray = np.array(
    [leg.height_pct for leg in _SIMPLE_LEGS], dtype=np.float64
)
_SIMPLE_MEDIAN: float = float(np.median(_SIMPLE_HEIGHTS))

SIMPLE_ZIGZAG: CloseOnlyFixture = CloseOnlyFixture(
    name="simple_zigzag",
    close=_SIMPLE_CLOSE,
    reversal_threshold=_SIMPLE_R,
    expected_legs=_SIMPLE_LEGS,
    expected_heights_pct=_SIMPLE_HEIGHTS,
    expected_global_median=_SIMPLE_MEDIAN,
)


# ---------------------------------------------------------------------------
# Fixture #2: many-leg sawtooth — used for auto / quantile materialisation.
# Repeating up/down with widening amplitudes on every other bar yields 13
# confirmed legs at r = 0.01.  Comfortably above max(local_window=5, 10).
# ---------------------------------------------------------------------------

_MANY_CLOSE: np.ndarray = np.array(
    [
        100.0, 105.0,  # leg #1 UP  (0..1, height 0.05)
        100.0,         # leg #2 DOWN (1..2, height 5/105)
        106.0,         # leg #3 UP   (2..3, height 6/100)
        100.0,         # leg #4 DOWN (3..4, height 6/106)
        107.0,         # leg #5 UP   (4..5, height 7/100)
        100.0,         # leg #6 DOWN (5..6, height 7/107)
        108.0,         # leg #7 UP   (6..7, height 8/100)
        100.0,         # leg #8 DOWN (7..8, height 8/108)
        109.0,         # leg #9 UP   (8..9, height 9/100)
        100.0,         # leg #10 DOWN (9..10, height 9/109)
        110.0,         # leg #11 UP  (10..11, height 10/100)
        100.0,         # leg #12 DOWN (11..12, height 10/110)
        111.0,         # leg #13 UP  (12..13, height 11/100)
        100.0,         # confirms leg #13 (13..14, height 11/111)
    ],
    dtype=np.float64,
)
_MANY_R: float = 0.01

# Manually computed expected legs walking the close-only formula at r=0.01.
# leg#1 UP   start=(0,100)   end=(1,105)  confirm=2  height=5/100
# leg#2 DOWN start=(1,105)   end=(2,100)  confirm=3  height=5/105
# leg#3 UP   start=(2,100)   end=(3,106)  confirm=4  height=6/100
# leg#4 DOWN start=(3,106)   end=(4,100)  confirm=5  height=6/106
# leg#5 UP   start=(4,100)   end=(5,107)  confirm=6  height=7/100
# leg#6 DOWN start=(5,107)   end=(6,100)  confirm=7  height=7/107
# leg#7 UP   start=(6,100)   end=(7,108)  confirm=8  height=8/100
# leg#8 DOWN start=(7,108)   end=(8,100)  confirm=9  height=8/108
# leg#9 UP   start=(8,100)   end=(9,109)  confirm=10 height=9/100
# leg#10 DOWN start=(9,109)  end=(10,100) confirm=11 height=9/109
# leg#11 UP  start=(10,100)  end=(11,110) confirm=12 height=10/100
# leg#12 DOWN start=(11,110) end=(12,100) confirm=13 height=10/110
# leg#13 UP  start=(12,100)  end=(13,111) confirm=14 height=11/100
_MANY_LEGS: List[ExpectedLeg] = [
    ExpectedLeg(0,  1,  2, 100.0, 105.0, +1,  5.0 / 100.0),
    ExpectedLeg(1,  2,  3, 105.0, 100.0, -1,  5.0 / 105.0),
    ExpectedLeg(2,  3,  4, 100.0, 106.0, +1,  6.0 / 100.0),
    ExpectedLeg(3,  4,  5, 106.0, 100.0, -1,  6.0 / 106.0),
    ExpectedLeg(4,  5,  6, 100.0, 107.0, +1,  7.0 / 100.0),
    ExpectedLeg(5,  6,  7, 107.0, 100.0, -1,  7.0 / 107.0),
    ExpectedLeg(6,  7,  8, 100.0, 108.0, +1,  8.0 / 100.0),
    ExpectedLeg(7,  8,  9, 108.0, 100.0, -1,  8.0 / 108.0),
    ExpectedLeg(8,  9, 10, 100.0, 109.0, +1,  9.0 / 100.0),
    ExpectedLeg(9, 10, 11, 109.0, 100.0, -1,  9.0 / 109.0),
    ExpectedLeg(10, 11, 12, 100.0, 110.0, +1, 10.0 / 100.0),
    ExpectedLeg(11, 12, 13, 110.0, 100.0, -1, 10.0 / 110.0),
    ExpectedLeg(12, 13, 14, 100.0, 111.0, +1, 11.0 / 100.0),
]
_MANY_HEIGHTS: np.ndarray = np.array(
    [leg.height_pct for leg in _MANY_LEGS], dtype=np.float64
)
_MANY_MEDIAN: float = float(np.median(_MANY_HEIGHTS))

MANY_LEG_SAWTOOTH: CloseOnlyFixture = CloseOnlyFixture(
    name="many_leg_sawtooth",
    close=_MANY_CLOSE,
    reversal_threshold=_MANY_R,
    expected_legs=_MANY_LEGS,
    expected_heights_pct=_MANY_HEIGHTS,
    expected_global_median=_MANY_MEDIAN,
)


# ---------------------------------------------------------------------------
# Fixture #3: insufficient-legs case — exactly one confirmed leg is detected.
# Used to test (a) "no legs" failure when r is huge, (b) auto + insufficient
# legs failure (1 < max(local_window, 10)), and (c) explicit numeric threshold
# succeeding even with a tiny leg count (no min_legs_for_quantile gate).
# Walk-through (r = 0.02):
#   close = [100, 100.5, 101, 99, 100, 105, 102]
#   t=5 close=105 establishes UP from bootstrap min at (3, 99)
#   t=6 close=102 reverses; emit single UP leg (3..5, height = 6/99)
# ---------------------------------------------------------------------------

_FEW_LEGS_CLOSE: np.ndarray = np.array(
    [100.0, 100.5, 101.0, 99.0, 100.0, 105.0, 102.0],
    dtype=np.float64,
)
_FEW_LEGS_R: float = 0.02
_FEW_LEGS_EXPECTED_HEIGHTS: np.ndarray = np.array([6.0 / 99.0], dtype=np.float64)


# Flat close — no reversals possible; used for the no-legs init failure path.
_FLAT_CLOSE: np.ndarray = np.array(
    [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
    dtype=np.float64,
)
_FLAT_R: float = 0.02


__all__ = [
    "CloseOnlyFixture",
    "ExpectedLeg",
    "SIMPLE_ZIGZAG",
    "MANY_LEG_SAWTOOTH",
    "_FEW_LEGS_CLOSE",
    "_FEW_LEGS_R",
    "_FEW_LEGS_EXPECTED_HEIGHTS",
    "_FLAT_CLOSE",
    "_FLAT_R",
]
