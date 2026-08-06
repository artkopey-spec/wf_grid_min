"""Stable tester run identity for active reversal confirmation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

import numpy as np
import pandas as pd

from supertrend_optimizer.core.trade_filter_config import (
    is_reversal_confirmation_enabled,
    trade_filter_effective_snapshot,
)


def canonical_serialize(value: Any) -> bytes:
    """Serialize JSON-compatible effective settings with stable ordering."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _input_data_identity(df: pd.DataFrame) -> str:
    price_columns = [
        "open" if "open" in df.columns else "Open",
        "high" if "high" in df.columns else "High",
        "low" if "low" in df.columns else "Low",
        "close" if "close" in df.columns else "Close",
    ]
    volume_column = (
        "volume"
        if "volume" in df.columns
        else "Volume"
        if "Volume" in df.columns
        else None
    )
    digest = hashlib.sha256()
    for column in price_columns:
        values = np.ascontiguousarray(
            df[column].to_numpy(dtype=np.float64)
        )
        digest.update(column.lower().encode("ascii"))
        digest.update(values.tobytes())
    if volume_column is None:
        digest.update(b"volume:absent")
    else:
        values = np.ascontiguousarray(
            df[volume_column].to_numpy(dtype=np.float64)
        )
        digest.update(b"volume")
        digest.update(values.tobytes())
    if isinstance(df.index, pd.DatetimeIndex):
        index_values = np.ascontiguousarray(df.index.asi8, dtype=np.int64)
        digest.update(str(df.index.tz).encode("utf-8"))
    else:
        index_values = np.ascontiguousarray(
            pd.util.hash_pandas_object(df.index, index=False).to_numpy(
                dtype=np.uint64
            )
        )
    digest.update(index_values.tobytes())
    return digest.hexdigest()


def build_tester_run_fingerprint(
    *,
    df: pd.DataFrame,
    atr_period: int,
    multiplier: float,
    trade_mode: str,
    commission: float,
    trade_filter_config: Any,
    period_splits: Iterable[tuple[str, float]],
    execution_model: str = "open_to_open",
    export_schema_version: int = 2,
) -> str:
    """Return the canonical active tester fingerprint."""
    effective_snapshot = (
        trade_filter_effective_snapshot(trade_filter_config)
        if is_reversal_confirmation_enabled(trade_filter_config)
        else {"effective_mode": "disabled"}
    )
    payload = {
        "input_data_identity": _input_data_identity(df),
        "strategy_parameters": {
            "atr_period": int(atr_period),
            "multiplier": float(multiplier),
            "trade_mode": str(trade_mode),
            "commission": float(commission),
        },
        "execution_model": execution_model,
        "effective_trade_filter_snapshot": effective_snapshot,
        "period_splits": [
            [str(label), float(fraction)] for label, fraction in period_splits
        ],
        "export_schema_version": int(export_schema_version),
    }
    return hashlib.sha256(canonical_serialize(payload)).hexdigest()


__all__ = ["build_tester_run_fingerprint", "canonical_serialize"]
