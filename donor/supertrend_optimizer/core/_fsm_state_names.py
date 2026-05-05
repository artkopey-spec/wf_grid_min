"""Single source of truth for FSM state names (plan_exit_off_modes_v2.txt §7.4).

Both WF Grid (`step_executor`, `step_collector`) and Tester (`runner`) re-export
:data:`FSM_STATE_NAMES` and :data:`ACTIVE_LIFECYCLE_STATES` from this module.
A drift test (§14.4) asserts tuple-equality between the canonical constants
here and every re-export point — any rename of an FSM state becomes a single
edit + automatic CI detection of the divergence.

Tuple ordering follows the canonical order from plan §7.4 (lifecycle
sequence: OFF -> WAIT -> FREEZE -> MONITORING -> COUNTING_ZZ_LEGS -> STOPPING).
This canonical order is INDEPENDENT of :class:`ZigZagFSMState` integer values
in ``zigzag_st_filter.py``; consumers that need an enum-int -> name mapping
must build it locally via ``getattr`` over the shared tuple, not by tuple
indexing (see ``zigzag_st_filter._FSM_STATE_NAMES``).

Plan reference: docs/plan_exit_off_modes_v2.txt §7.4, §14.4
"""

from __future__ import annotations


FSM_STATE_NAMES: tuple[str, ...] = (
    "OFF",
    "WAIT_FIRST_ST_FLIP",
    "ST_ACTIVE_FREEZE",
    "ST_ACTIVE_MONITORING",
    "ST_COUNTING_ZZ_LEGS",
    "ST_STOPPING",
)

ACTIVE_LIFECYCLE_STATES: tuple[str, ...] = (
    "ST_ACTIVE_FREEZE",
    "ST_ACTIVE_MONITORING",
    "ST_COUNTING_ZZ_LEGS",
)


__all__ = [
    "FSM_STATE_NAMES",
    "ACTIVE_LIFECYCLE_STATES",
]
