"""
WP-PAR multiprocessing helpers (plan §2).

This module provides the worker-side primitives for parallel grid_point
execution.  All public symbols are top-level functions because Windows
spawn cannot import nested closures.

Contracts (plan §2.2 / §2.3):
  - pack_data / unpack_data is a LOSSLESS round-trip for any OHLC frame
    that satisfies validate_ohlc_data.  It preserves dtype, timezone,
    index name and index frequency.  DatetimeIndex storage is normalised
    to nanoseconds and records index_unit explicitly.

Worker state (plan §2.4):
  - _WORKER_STATE is a per-process dict populated by _init_worker.
    Each worker process is reused across many tasks, so the cache cuts
    OHLC unpack and zigzag freezing to a single cost per worker.

Hardening (plan §2.4 step 6):
  - ZigZagGlobalStats is frozen=True but its ``confirmed_legs`` list and
    ``confirmed_heights_pct`` ndarray are otherwise mutable.  Workers
    are reused, so per-task mutation would leak between tasks N and
    N+1.  We freeze both inside the worker process.

Test hooks (plan §2.4 step 2 / §2.5):
  - WF_GRID_FORCE_INIT_FAIL=1   -> ImportError inside _init_worker
  - WF_GRID_FORCE_WORKER_CRASH=exit
                                -> os._exit(17) (BrokenProcessPool)
  - WF_GRID_FORCE_TASK_RAISE=1  -> RuntimeError from worker task
  - WF_GRID_FORCE_DELAY_GP=<id> -> sleep(2) for one specific grid_point

These hooks are read at runtime; production runs leave them unset.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Worker-process state (plan §2.1)
# ---------------------------------------------------------------------------

# Populated by _init_worker once per worker process.  Tasks read from here.
_WORKER_STATE: dict = {}


# ---------------------------------------------------------------------------
# pack_data / unpack_data (plan §2.2 / §2.3) — lossless round-trip
# ---------------------------------------------------------------------------

def pack_data(full_data: pd.DataFrame) -> dict:
    """Convert OHLC+ DataFrame to a picklable dict of numpy arrays + metadata.

    ROUND-TRIP CONTRACT (plan §2.2):
        unpack_data(pack_data(df)) is observationally equal to df.copy()
        for any df that satisfies:
          - df.columns is a superset of {open, high, low, close};
          - df has a DatetimeIndex, tz-aware or tz-naive;
          - OHLC dtypes are numeric dtypes accepted by validate_ohlc_data.

    OHLC dtypes are NOT coerced to float64; index timezone, name and
    frequency are preserved.  DatetimeIndex storage is normalised to
    int64 nanoseconds with ``index_unit="ns"`` so pandas versions that
    materialise ``datetime64[us]`` indexes cannot be misread as ns later.
    All extra columns beyond OHLC are preserved with their original dtypes.
    """
    if not isinstance(full_data.index, pd.DatetimeIndex):
        raise TypeError(
            f"pack_data requires a DatetimeIndex, got "
            f"{type(full_data.index).__name__!r}"
        )

    for col in ("open", "high", "low", "close"):
        if col not in full_data.columns:
            raise KeyError(
                f"pack_data: required OHLC column missing: {col!r}"
            )

    # Preserve ALL columns (OHLC + any extras) with their dtypes and order.
    column_names: list[str] = list(full_data.columns)
    column_dtypes: dict[str, str] = {
        col: str(full_data[col].dtype) for col in column_names
    }
    arrays: dict[str, np.ndarray] = {
        col: full_data[col].to_numpy(copy=True) for col in column_names
    }

    idx = full_data.index
    # Store explicit nanoseconds, not whatever physical unit the pandas
    # DatetimeIndex happens to carry (ns/us/ms). This prevents a us payload
    # from being restored as ns and drifting into 1970.
    index_tz: Optional[str] = None
    if idx.tz is not None:
        idx_ns = idx.tz_convert("UTC").tz_localize(None).astype("datetime64[ns]")
        index_tz = str(idx.tz)
    else:
        idx_ns = idx.astype("datetime64[ns]")
    index_int64_ns_utc = idx_ns.asi8.copy()
    index_unit = "ns"

    index_name = idx.name
    # idx.freq may be a DateOffset; freqstr is the picklable string form.
    index_freq: Optional[str] = idx.freqstr if idx.freq is not None else None

    return {
        "column_names": column_names,
        "column_dtypes": column_dtypes,
        "arrays": arrays,
        "index_int64_ns_utc": index_int64_ns_utc,
        "index_unit": index_unit,
        "index_tz": index_tz,
        "index_name": index_name,
        "index_freq": index_freq,
    }


def unpack_data(d: dict) -> pd.DataFrame:
    """Inverse of pack_data (plan §2.3).

    Restores all columns (OHLC + extras) with original dtypes and column
    order; preserves DatetimeIndex tz, name and freq.
    """
    index_unit = d.get("index_unit", "ns")
    if d["index_tz"] is None:
        # tz-naive path: restore wall-clock instants using the recorded unit.
        idx = pd.DatetimeIndex(
            pd.to_datetime(d["index_int64_ns_utc"], unit=index_unit)
        )
    else:
        # tz-aware path: stored values are UTC; convert to original tz.
        idx = pd.to_datetime(
            d["index_int64_ns_utc"], unit=index_unit, utc=True
        ).tz_convert(d["index_tz"])

    idx.name = d["index_name"]
    # Plan C3-3 / A13: preserve freq exactly.  No defensive swallow here —
    # if the stored freqstr is no longer valid for the restored index we
    # want the round-trip to fail loudly so tests catch the regression.
    if d["index_freq"] is not None:
        idx.freq = pd.tseries.frequencies.to_offset(d["index_freq"])

    # Support both old format (OHLC-only keys at top level) and new format
    # (column_names / arrays keys) so that payload dicts produced by either
    # version of pack_data can be unpacked correctly.
    if "column_names" in d:
        # New format: all columns stored under d["arrays"].
        cols: dict[str, pd.Series] = {}
        for col in d["column_names"]:
            cols[col] = pd.Series(
                d["arrays"][col], index=idx, dtype=d["column_dtypes"][col],
            )
        return pd.DataFrame(cols, index=idx)[d["column_names"]]
    else:
        # Legacy format: only OHLC stored as top-level keys.
        cols = {}
        for c in ("open", "high", "low", "close"):
            cols[c] = pd.Series(
                d[c], index=idx, dtype=d["column_dtypes"][c],
            )
        return pd.DataFrame(cols, index=idx)


# ---------------------------------------------------------------------------
# _init_worker (plan §2.4)
# ---------------------------------------------------------------------------

def _init_worker(
    project_root: str,
    donor_path: str,
    data_dict: dict,
    wf_slices: list,
    config: Any,
    prepend_bars: int,
    zigzag_global_stats: Any,
) -> None:
    """ProcessPoolExecutor initializer (plan §2.4).

    Steps:
      1. Bootstrap sys.path (project_root + donor_path) BEFORE any heavy
         wf_grid / donor imports.  Required by Windows spawn.
      2. Optional ImportError test hook (WF_GRID_FORCE_INIT_FAIL=1).
      3. Heavy imports happen inside the function (deferred so step 1
         applies first).
      4. Configure worker loggers to WARNING (plan §2.4 step 4) — keeps
         per-step warnings observable without flooding parent stdout.
      5. Reconstruct full OHLC frame once via unpack_data; cache in
         _WORKER_STATE.
      6. Harden ZigZagGlobalStats against worker-side mutation
         (plan §2.4 step 6 / fix C3-2).
    """
    # Step 1: sys.path bootstrap (must happen before donor imports).
    for p in (donor_path, project_root):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    # Step 2: env-driven test hook for initializer failure (plan §2.4 step 2).
    # Production runs do not set this variable.
    if os.environ.get("WF_GRID_FORCE_INIT_FAIL") == "1":
        raise ImportError("simulated")

    # Step 3: heavy imports (deferred so the sys.path bootstrap above wins).
    # NOTE: imports below are not used directly in this function, but they
    # warm the import cache so subsequent task calls do not repay the cost.
    from wf_grid.wf import runner as _runner  # noqa: F401
    from wf_grid.wf import step_executor as _step  # noqa: F401

    # Step 4: configure worker logging (plan §2.4 step 4).
    # Do NOT use logging.disable(); per-step warnings remain observable.
    logging.getLogger("wf_grid").setLevel(logging.WARNING)
    logging.getLogger("supertrend_optimizer").setLevel(logging.WARNING)

    # Step 5: reconstruct full_data once.
    full_data = unpack_data(data_dict)

    # Step 6: harden ZigZagGlobalStats (plan §2.4 step 6, fix C3-2).
    # The dataclass is frozen=True, but its list field and ndarray field are
    # otherwise mutable; workers are reused, so an unfrozen stats object would
    # let task N mutate state visible to task N+1.  object.__setattr__ is
    # required because frozen=True forbids regular attribute assignment.
    if zigzag_global_stats is not None:
        zigzag_global_stats.confirmed_heights_pct.setflags(write=False)
        object.__setattr__(
            zigzag_global_stats,
            "confirmed_legs",
            tuple(zigzag_global_stats.confirmed_legs),
        )

    _WORKER_STATE.clear()
    _WORKER_STATE.update({
        "full_data": full_data,
        "wf_slices": wf_slices,
        "config": config,
        "prepend_bars": prepend_bars,
        "zigzag_global_stats": zigzag_global_stats,
    })


# ---------------------------------------------------------------------------
# _run_grid_point_both (plan §2.5)
# ---------------------------------------------------------------------------

def _run_grid_point_both(grid_point: Any) -> tuple[str, list, list]:
    """Worker task: run OOS + Train WF for a single grid_point.

    Returns (grid_point_id, oos_results, train_results).

    Test hooks (plan §2.5):
      - WF_GRID_FORCE_WORKER_CRASH=exit -> os._exit(17), triggers
        BrokenProcessPool.  Use this for hard-crash tests; raising
        RuntimeError does NOT break the pool.
      - WF_GRID_FORCE_TASK_RAISE=1      -> simulate normal task exception.
      - WF_GRID_FORCE_DELAY_GP=<id>     -> sleep(2) only for this gp.
    """
    # Hard crash hook (plan §2.5): cannot be caught by the pool.
    if os.environ.get("WF_GRID_FORCE_WORKER_CRASH") == "exit":
        os._exit(17)
    # Plain task-exception hook.
    if os.environ.get("WF_GRID_FORCE_TASK_RAISE") == "1":
        raise RuntimeError("simulated task error")
    # Plain task TypeError hook (distinct from pickle/init TypeError).
    if os.environ.get("WF_GRID_FORCE_TASK_TYPEERROR") == "1":
        raise TypeError("simulated task TypeError")
    # Per-grid-point delay hook (used by order-independence stress test).
    delay_gp = os.environ.get("WF_GRID_FORCE_DELAY_GP")
    if delay_gp is not None and delay_gp == grid_point.grid_point_id:
        time.sleep(2)

    # Deferred imports keep the module importable without donor/wf_grid on
    # the path (matches the bootstrap order in _init_worker).
    from wf_grid.wf.runner import (
        run_wf_for_grid_point,
        run_wf_train_for_grid_point,
    )

    state = _WORKER_STATE
    oos_results = run_wf_for_grid_point(
        grid_point=grid_point,
        wf_slices=state["wf_slices"],
        full_data=state["full_data"],
        config=state["config"],
        prepend_bars_requested=state["prepend_bars"],
        zigzag_global_stats=state["zigzag_global_stats"],
    )
    train_results = run_wf_train_for_grid_point(
        grid_point=grid_point,
        wf_slices=state["wf_slices"],
        full_data=state["full_data"],
        config=state["config"],
        zigzag_global_stats=state["zigzag_global_stats"],
    )
    # filter_diagnostics_oos holds ~21 per-bar numpy arrays per step
    # (~24K bars OOS, ~72K bars train).  The downstream consumer
    # (collect_oos_steps / collect_train_steps) only reads
    # filter_diagnostics_summary, which has already been derived at
    # StepResult construction time.  Keeping the per-bar arrays inflates
    # the IPC payload to ~100 MB/task and OOMs the queue on large grids.
    _strip_filter_diagnostics_arrays(oos_results)
    _strip_filter_diagnostics_arrays(train_results)
    return grid_point.grid_point_id, oos_results, train_results


def _strip_filter_diagnostics_arrays(step_results: list) -> None:
    """Null per-bar filter_diagnostics_oos on each StepResult in-place.

    Safe to call on any iterable of StepResult: filter_diagnostics_summary
    is preserved (it is the only field consumed by downstream collectors).
    """
    for sr in step_results:
        if getattr(sr, "filter_diagnostics_oos", None) is not None:
            sr.filter_diagnostics_oos = None
