"""
Split-test pattern helpers (plan_exit_off_modes_v2.txt §9.4).

These two functions are the canonical gate for §9.4's MANDATORY split between
Group A (bit-identical) and Group B (sentinel/superset) assertions.

CI rule (§9.4): no test may compare the full filter_diagnostics dict as one unit.
Instead every test that touches backwards-compat MUST call these helpers.

Usage example::

    r_default = apply(..., cfg=_FilterCfgDefault())
    r_exit_a  = apply(..., cfg=_FilterCfgExitA())

    assert_baseline_fingerprint(r_default, r_exit_a)      # Group A: bit-identical
    assert_diagnostics_superset(r_default)                 # Group B: sentinels present

For exit B group B::

    assert_diagnostics_superset(
        r_exit_b,
        sentinel_map=EXIT_B_SENTINEL_MAP(count=3),
    )
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Group A: arrays that must be bit-identical between default and exit A configs
# (plan §9.1 / §9.4).
# ---------------------------------------------------------------------------
_GROUP_A_DIAG_KEYS: tuple[str, ...] = (
    "trade_filter_state",
    "filter_block_reason",
    "filter_allowed_entry",
    "confirmed_legs_since_start",
    "median_stop_triggered",
    "trade_filter_trigger_source",
    "state_at_bar_start",
    "held_pos_at_bar_start",
)

# ---------------------------------------------------------------------------
# Group B sentinel map for DEFAULT config (no exit-off keys in YAML).
# §9.2: exit_off_mode="exit A", exit_off_zz_leg_count=-1,
#       zz_legs_since_lifecycle_start=-1, zz_leg_stop_triggered=0.
# ---------------------------------------------------------------------------
_GROUP_B_SENTINELS_DEFAULT: Dict[str, Any] = {
    "exit_off_mode": "exit A",
    "exit_off_zz_leg_count": -1,
    "zz_legs_since_lifecycle_start": -1,
    "zz_leg_stop_triggered": 0,
}

# All four Group B keys that MUST exist in the keyset (plan §9.2 / §6).
GROUP_B_KEYS: tuple[str, ...] = tuple(_GROUP_B_SENTINELS_DEFAULT.keys())


def EXIT_B_SENTINEL_MAP(count: int) -> Dict[str, Any]:
    """Return the Group B sentinel map for an exit B config with given count.

    Values are 'sentinels' only in the sense that the echo arrays must be constant.
    Actual counter/trigger values are validated per-bar in the property tests.
    """
    return {
        "exit_off_mode": "exit B",
        "exit_off_zz_leg_count": count,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assert_baseline_fingerprint(
    result_default,
    result_reference,
    *,
    context: str = "",
    extra_diag_keys: Iterable[str] = (),
) -> None:
    """Assert Group A arrays are bit-identical between default and reference configs.

    Plan §9.4 Group A — bit-identical for all trading arrays and shared diagnostic
    arrays that must not change when exit-off keys are added.

    Args:
        result_default:   apply() result for the default config (no exit_off fields).
        result_reference: apply() result for explicit exit A config.
        context:          label prepended to error messages for easier tracing.
        extra_diag_keys:  additional filter_diagnostics keys to compare (optional).
    """
    prefix = f"[{context}] " if context else ""
    diag_def = result_default.filter_diagnostics
    diag_ref = result_reference.filter_diagnostics

    # positions (on the result object itself, not in filter_diagnostics)
    np.testing.assert_array_equal(
        np.asarray(result_default.positions),
        np.asarray(result_reference.positions),
        err_msg=(
            f"{prefix}Group A: 'positions' not bit-identical "
            "between default and exit A configs"
        ),
    )

    all_keys = list(_GROUP_A_DIAG_KEYS) + list(extra_diag_keys)
    for key in all_keys:
        if key not in diag_def or key not in diag_ref:
            continue
        arr_def = np.asarray(diag_def[key])
        arr_ref = np.asarray(diag_ref[key])
        np.testing.assert_array_equal(
            arr_def,
            arr_ref,
            err_msg=(
                f"{prefix}Group A: '{key}' not bit-identical "
                "between default and exit A configs"
            ),
        )

    # Optional full Group A coverage for richer result objects (e.g. BacktestResult):
    # compare trading arrays/frames/metrics when both sides provide them.
    if hasattr(result_default, "returns") and hasattr(result_reference, "returns"):
        np.testing.assert_array_equal(
            np.asarray(result_default.returns),
            np.asarray(result_reference.returns),
            err_msg=(
                f"{prefix}Group A: 'returns' not bit-identical "
                "between default and exit A configs"
            ),
        )

    if hasattr(result_default, "equity_curve") and hasattr(result_reference, "equity_curve"):
        np.testing.assert_array_equal(
            np.asarray(result_default.equity_curve),
            np.asarray(result_reference.equity_curve),
            err_msg=(
                f"{prefix}Group A: 'equity_curve' not bit-identical "
                "between default and exit A configs"
            ),
        )

    trades_def = getattr(result_default, "trades_df", None)
    trades_ref = getattr(result_reference, "trades_df", None)
    if trades_def is None or trades_ref is None:
        assert trades_def is None and trades_ref is None, (
            f"{prefix}Group A: trades_df presence mismatch "
            f"(default={trades_def is not None}, ref={trades_ref is not None})"
        )
    else:
        import pandas as pd

        pd.testing.assert_frame_equal(
            trades_def.reset_index(drop=True),
            trades_ref.reset_index(drop=True),
            check_dtype=True,
            check_like=False,
            obj=f"{prefix}Group A trades_df",
        )

    metrics_def = getattr(result_default, "metrics", None)
    metrics_ref = getattr(result_reference, "metrics", None)
    if metrics_def is not None and metrics_ref is not None:
        assert set(metrics_def.keys()) == set(metrics_ref.keys()), (
            f"{prefix}Group A: metrics keyset mismatch.\n"
            f"  default keys: {sorted(metrics_def.keys())}\n"
            f"  ref keys:     {sorted(metrics_ref.keys())}"
        )
        for key in sorted(metrics_def.keys()):
            v1 = metrics_def[key]
            v2 = metrics_ref[key]
            if isinstance(v1, float) or isinstance(v2, float):
                assert np.isclose(
                    float(v1), float(v2), rtol=0.0, atol=0.0, equal_nan=True
                ), (
                    f"{prefix}Group A: metrics[{key!r}] differs: {v1!r} vs {v2!r}"
                )
            else:
                assert v1 == v2, (
                    f"{prefix}Group A: metrics[{key!r}] differs: {v1!r} vs {v2!r}"
                )


def assert_diagnostics_superset(
    result,
    *,
    sentinel_map: Optional[Dict[str, Any]] = None,
    extra_keys: Iterable[str] = (),
    context: str = "",
) -> None:
    """Assert Group B arrays are present with expected sentinel/echo values.

    Plan §9.4 Group B — controlled superset: all new keys present, values match
    the sentinel table from §2.

    Args:
        result:       apply() result to inspect.
        sentinel_map: {key: expected_scalar} mapping. Each array must be ALL-EQUAL
                      to the given scalar. Defaults to _GROUP_B_SENTINELS_DEFAULT
                      (exit A / default config sentinels).
        extra_keys:   additional keys that must be present (value not checked).
        context:      label for error messages.

    Raises:
        AssertionError: if any key is missing or any array has unexpected values.
    """
    prefix = f"[{context}] " if context else ""
    smap: Dict[str, Any] = (
        sentinel_map if sentinel_map is not None else _GROUP_B_SENTINELS_DEFAULT
    )
    fd = result.filter_diagnostics

    # 1) All mandatory Group B keys must be present regardless of sentinel_map.
    for key in GROUP_B_KEYS:
        assert key in fd, (
            f"{prefix}Group B: mandatory key '{key}' missing from filter_diagnostics"
        )

    # 2) Extra keys must be present (no value check).
    for key in extra_keys:
        assert key in fd, (
            f"{prefix}Group B: expected key '{key}' missing from filter_diagnostics"
        )

    # 3) Value assertions per sentinel_map.
    for key, expected in smap.items():
        if key not in fd:
            continue
        arr = np.asarray(fd[key])
        if expected is None:
            continue
        if isinstance(expected, str):
            bad_vals = sorted({v for v in arr if v != expected})
            assert not bad_vals, (
                f"{prefix}Group B: '{key}' expected all='{expected}', "
                f"got unexpected values: {bad_vals}"
            )
        else:
            expected_int = int(expected)
            bad_vals = sorted({int(v) for v in arr if int(v) != expected_int})
            assert not bad_vals, (
                f"{prefix}Group B: '{key}' expected all={expected_int}, "
                f"got unexpected values: {bad_vals}"
            )
