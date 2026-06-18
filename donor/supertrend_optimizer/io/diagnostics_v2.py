"""Opt-in Tester diagnostics v2 scaffolding.

This module is intentionally read-only: it only resolves export flags and
builds report payloads from already-produced tester/backtest outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from supertrend_optimizer.utils.exceptions import ConfigError


STATUS_VALUES = frozenset({"PASS", "WARN", "FAIL", "SKIP", "INFO"})

V2_SHEET_ORDER: tuple[str, ...] = (
    "Index",
    "Reproducibility",
    "Dashboard",
    "Run_Health",
    "Trade_Analytics",
    "Equity_Drawdown",
    "Filter_Funnel",
    "Filter_Attribution",
    "Cycle_Summary",
    "Cost_Sensitivity",
    "Remediation",
    "FilterDiagnostics_sampled",
)

PHASE_A_FLAG_TO_SHEET: dict[str, str] = {
    "index": "Index",
    "reproducibility": "Reproducibility",
    "dashboard": "Dashboard",
    "run_health": "Run_Health",
    "trade_analytics": "Trade_Analytics",
    "equity_drawdown": "Equity_Drawdown",
    "filter_funnel": "Filter_Funnel",
    "filter_attribution": "Filter_Attribution",
    "cycle_summary": "Cycle_Summary",
    "cost_sensitivity": "Cost_Sensitivity",
    "remediation": "Remediation",
    "filter_diagnostics_sampled": "FilterDiagnostics_sampled",
}

RESERVED_FLAGS: frozenset[str] = frozenset(
    {
        "returns_calendar",
        "exit_quality",
        "false_start_2",
        "data_dictionary",
        "robustness",
    }
)

VALID_DIAGNOSTICS_V2_FLAGS: frozenset[str] = frozenset(
    PHASE_A_FLAG_TO_SHEET
) | RESERVED_FLAGS

DIAGNOSTICS_V2_THRESHOLDS = pd.DataFrame(
    [
        ("pf_weak", "<", 1.1, "ratio", "Profit factor is weak."),
        ("median_negative", "<", 0.0, "pct", "Median trade return is negative."),
        ("false_start_high", ">", 30.0, "pct", "False-start share is high."),
        ("avg_trade_too_small", "<", 0.05, "pct", "Average trade is small."),
        ("cost_fragile", ">", 50.0, "pct", "Stressed PnL loss is high."),
        ("low_filter_coverage", "<", 15.0, "pct", "Filter diagnostics coverage is low."),
        ("dd_duration_high", ">", 50.0, "trades", "Drawdown duration is high."),
        ("cycle_overtrade", "<", 0.0, "pct", "Later cycle-trade median PnL is negative."),
        ("giveback_high", ">", 50.0, "pct", "Trade giveback proxy is high."),
    ],
    columns=["flag", "operator", "value", "unit", "description"],
)

CYCLE_MAP_COLUMNS: tuple[str, ...] = (
    "trade_id",
    "cycle_id",
    "trade_idx_in_cycle",
    "cycle_age_at_entry",
    "cycle_trade_count_at_entry",
    "is_in_cycle",
    "cycle_start_index",
    "cycle_end_index",
    "mapping_status",
)


@dataclass(frozen=True)
class DiagnosticsV2Context:
    period_results: list[Any]
    pr_100: Any
    df: pd.DataFrame
    trades_100: pd.DataFrame
    signals_df: pd.DataFrame
    fd_100: Mapping[str, Any] | None
    filter_diagnostics_summary: Mapping[str, Any] | None
    run_metadata: Mapping[str, Any]
    trade_filter_config: Any
    config_yaml_snapshot: Mapping[str, Any] | None
    cycle_map: pd.DataFrame
    thresholds: pd.DataFrame


@dataclass(frozen=True)
class DiagnosticsV2Sheet:
    name: str
    phase: str
    df: pd.DataFrame
    status: str
    primary_source: str
    notes: str


def resolve_diagnostics_v2_flags(
    diagnostics_v2_flags: Mapping[str, bool] | None,
) -> dict[str, bool]:
    """Resolve strict child-sheet flags for diagnostics v2."""
    raw_flags = diagnostics_v2_flags or {}
    if not isinstance(raw_flags, Mapping):
        raise ConfigError(
            "export.diagnostics_v2_flags must be a mapping of flag names to booleans"
        )

    unknown = sorted(str(k) for k in raw_flags if k not in VALID_DIAGNOSTICS_V2_FLAGS)
    if unknown:
        raise ConfigError(
            "export.diagnostics_v2_flags contains unknown key(s): "
            + ", ".join(repr(k) for k in unknown)
        )

    for key, value in raw_flags.items():
        if not isinstance(value, bool):
            raise ConfigError(
                f"export.diagnostics_v2_flags.{key} must be a boolean true/false, "
                f"got type {type(value).__name__!r} with value {value!r}"
            )
        if key in RESERVED_FLAGS and value is True:
            raise ConfigError(
                f"export.diagnostics_v2_flags.{key}=true is reserved for a "
                "future diagnostics v2 sheet and is not implemented yet"
            )

    resolved = {flag: True for flag in PHASE_A_FLAG_TO_SHEET}
    resolved.update({flag: False for flag in RESERVED_FLAGS})
    resolved.update(raw_flags)
    return resolved


def resolve_collect_filter_diagnostics(
    export_config: Mapping[str, Any],
    *,
    preserve_legacy_batch_default: bool = False,
) -> bool:
    """Return whether runner must collect filter diagnostics for export features."""
    needs_diagnostics = bool(
        export_config.get("diagnostics")
        or export_config.get("signals")
        or export_config.get("cycle")
        or export_config.get("trades")
        or export_config.get("diagnostics_v2")
    )
    if preserve_legacy_batch_default and not export_config.get("diagnostics_v2"):
        return True
    return needs_diagnostics


def _empty_cycle_map() -> pd.DataFrame:
    return pd.DataFrame(columns=CYCLE_MAP_COLUMNS)


def _trade_id_for_row(row: Any, fallback: int) -> Any:
    value = getattr(row, "trade_id", None)
    if value is None or pd.isna(value):
        return fallback
    return value


def _entry_signal_index(value: Any) -> tuple[int | None, str | None]:
    if pd.isna(value):
        return None, "missing_entry_index"
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None, "invalid_entry_index"
    if not np.isfinite(as_float) or int(as_float) != as_float:
        return None, "invalid_entry_index"
    return max(int(as_float) - 1, 0), None


def _cycle_mode_from_config(trade_filter_config: Any, explicit_mode: str | None) -> str:
    if explicit_mode:
        return explicit_mode
    volume = getattr(trade_filter_config, "volume", None)
    zigzag = getattr(trade_filter_config, "zigzag", None)
    if volume is not None and getattr(volume, "enabled", None) is True and (
        zigzag is None or getattr(zigzag, "enabled", None) is not True
    ):
        return "volume_only"
    return "zigzag"


def _diagnostics_mode(ctx: DiagnosticsV2Context) -> str:
    mode = _cycle_mode_from_config(ctx.trade_filter_config, None)
    if mode == "volume_only":
        return mode
    zigzag = getattr(ctx.trade_filter_config, "zigzag", None)
    zigzag_mode = str(getattr(zigzag, "mode", "") or "").upper()
    return "zigzag_d" if zigzag_mode == "D" else "zigzag"


def derive_trade_cycle_map(
    fd_100: Mapping[str, Any] | None,
    trades_100: pd.DataFrame,
    *,
    df: pd.DataFrame | None = None,
    mode: str | None = None,
) -> pd.DataFrame:
    """Map trades to legacy cycle segments without recomputing trading logic."""
    if trades_100 is None or len(trades_100) == 0:
        return _empty_cycle_map()

    rows: list[dict[str, Any]] = []
    trades = trades_100.reset_index(drop=True)

    def status_rows(status: str, *, prefer_entry_status: bool = False) -> pd.DataFrame:
        for idx, trade in enumerate(trades.itertuples(index=False), start=1):
            entry_signal_idx, entry_status = _entry_signal_index(
                getattr(trade, "entry_index", np.nan)
            )
            rows.append(
                {
                    "trade_id": _trade_id_for_row(trade, idx),
                    "cycle_id": np.nan,
                    "trade_idx_in_cycle": np.nan,
                    "cycle_age_at_entry": np.nan,
                    "cycle_trade_count_at_entry": np.nan,
                    "is_in_cycle": False,
                    "cycle_start_index": np.nan,
                    "cycle_end_index": np.nan,
                    "mapping_status": entry_status if prefer_entry_status and entry_status else status,
                }
            )
        return pd.DataFrame(rows, columns=CYCLE_MAP_COLUMNS)

    if fd_100 is None:
        return status_rows("missing_fd_100")
    if "entry_index" not in trades.columns:
        return status_rows("missing_entry_index")
    if "trade_filter_state" not in fd_100:
        return status_rows("missing_required_columns")

    state_arr = np.asarray(fd_100["trade_filter_state"])
    if state_arr.ndim == 0 or len(state_arr) == 0:
        return status_rows("missing_required_columns")

    resolved_mode = mode or "zigzag"
    if resolved_mode in {"zigzag", "zigzag_st_mode", "D", "A", "B", "C", "A+B", "C+B"}:
        from supertrend_optimizer.io.excel_tester import _completed_cycle_segments

        segments = [(start, end, end) for start, end in _completed_cycle_segments(state_arr)]
    elif resolved_mode in {"volume_only", "volume"}:
        from supertrend_optimizer.io.excel_tester import _completed_cycle_segments_with_exit

        segments = list(_completed_cycle_segments_with_exit(state_arr))
    else:
        return status_rows("unsupported_mode")

    if not segments:
        return status_rows("outside_cycle", prefer_entry_status=True)

    segment_starts = np.asarray([segment[0] for segment in segments], dtype=np.int64)
    segment_last_active = np.asarray([segment[1] for segment in segments], dtype=np.int64)
    signal_indices: list[int | None] = [
        _entry_signal_index(getattr(trade, "entry_index", np.nan))[0]
        for trade in trades.itertuples(index=False)
    ]
    for idx, trade in enumerate(trades.itertuples(index=False), start=1):
        signal_idx, status = _entry_signal_index(getattr(trade, "entry_index", np.nan))
        if status is not None:
            rows.append(
                {
                    "trade_id": _trade_id_for_row(trade, idx),
                    "cycle_id": np.nan,
                    "trade_idx_in_cycle": np.nan,
                    "cycle_age_at_entry": np.nan,
                    "cycle_trade_count_at_entry": np.nan,
                    "is_in_cycle": False,
                    "cycle_start_index": np.nan,
                    "cycle_end_index": np.nan,
                    "mapping_status": status,
                }
            )
            continue

        if signal_idx is None or signal_idx >= len(state_arr):
            rows.append(
                {
                    "trade_id": _trade_id_for_row(trade, idx),
                    "cycle_id": np.nan,
                    "trade_idx_in_cycle": np.nan,
                    "cycle_age_at_entry": np.nan,
                    "cycle_trade_count_at_entry": np.nan,
                    "is_in_cycle": False,
                    "cycle_start_index": np.nan,
                    "cycle_end_index": np.nan,
                    "mapping_status": "invalid_entry_index",
                }
            )
            continue

        seg_pos = int(np.searchsorted(segment_starts, signal_idx, side="right") - 1)
        match: tuple[int, int, int, int] | None = None
        if seg_pos >= 0 and signal_idx <= int(segment_last_active[seg_pos]):
            start, last_active, end = segments[seg_pos]
            match = (seg_pos + 1, start, last_active, end)

        if match is None:
            rows.append(
                {
                    "trade_id": _trade_id_for_row(trade, idx),
                    "cycle_id": np.nan,
                    "trade_idx_in_cycle": np.nan,
                    "cycle_age_at_entry": np.nan,
                    "cycle_trade_count_at_entry": np.nan,
                    "is_in_cycle": False,
                    "cycle_start_index": np.nan,
                    "cycle_end_index": np.nan,
                    "mapping_status": "outside_cycle",
                }
            )
            continue

        cycle_idx, start, last_active, end = match
        rows.append(
            {
                "trade_id": _trade_id_for_row(trade, idx),
                "cycle_id": cycle_idx,
                "trade_idx_in_cycle": np.nan,
                "cycle_age_at_entry": signal_idx - start,
                "cycle_trade_count_at_entry": np.nan,
                "is_in_cycle": True,
                "cycle_start_index": start,
                "cycle_end_index": end,
                "mapping_status": "mapped",
                "_signal_idx": signal_idx,
                "_row_idx": idx,
            }
        )

    cycle_ids = sorted(
        {
            row["cycle_id"]
            for row in rows
            if row.get("mapping_status") == "mapped" and pd.notna(row.get("cycle_id"))
        }
    )
    for cycle_id in cycle_ids:
        row_positions = [
            pos
            for pos, row in enumerate(rows)
            if row.get("mapping_status") == "mapped" and row.get("cycle_id") == cycle_id
        ]
        row_positions.sort(
            key=lambda pos: (rows[pos].get("_signal_idx", 0), rows[pos].get("_row_idx", 0))
        )
        for rank, pos in enumerate(row_positions, start=1):
            rows[pos]["trade_idx_in_cycle"] = rank
            rows[pos]["cycle_trade_count_at_entry"] = rank - 1
    for row in rows:
        row.pop("_signal_idx", None)
        row.pop("_row_idx", None)

    return pd.DataFrame(rows, columns=CYCLE_MAP_COLUMNS)


def _sheet(
    name: str,
    *,
    phase: str,
    df: pd.DataFrame,
    status: str,
    primary_source: str,
    notes: str = "",
) -> DiagnosticsV2Sheet:
    return DiagnosticsV2Sheet(
        name=name,
        phase=phase,
        df=df,
        status=status,
        primary_source=primary_source,
        notes=notes,
    )


def _safe_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if np.isfinite(result) else float("nan")


def _strip_tz_value(value: Any) -> Any:
    if isinstance(value, pd.Timestamp) and value.tz is not None:
        return value.tz_convert(None)
    return value


def _build_reproducibility_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    df = ctx.df
    metadata = ctx.run_metadata
    rows: list[dict[str, Any]] = []

    def add(field: str, value: Any, status: str, notes: str = "") -> None:
        rows.append({"Field": field, "Value": value, "Status": status, "Notes": notes})

    add("report_generator_version", "diagnostics_v2_phase_a", "INFO")
    add("rows_count", len(df), "INFO")
    if len(df.index) > 0:
        add("first_timestamp", _strip_tz_value(df.index[0]), "INFO")
        add("last_timestamp", _strip_tz_value(df.index[-1]), "INFO")
        tz = getattr(df.index, "tz", None)
        add("timezone", str(tz) if tz is not None else "missing", "INFO")
    else:
        add("first_timestamp", "missing", "SKIP", "df is empty")
        add("last_timestamp", "missing", "SKIP", "df is empty")
        add("timezone", "missing", "SKIP", "df is empty")

    for field in ("config_path", "csv_path"):
        value = metadata.get(field, "missing")
        add(field, value if value not in (None, "") else "missing", "INFO" if value else "SKIP")
    for field in ("data_hash", "config_hash", "git_commit", "dirty_worktree"):
        value = metadata.get(field, "missing")
        add(field, value if value not in (None, "") else "missing", "INFO" if value else "SKIP")

    result = pd.DataFrame(rows, columns=["Field", "Value", "Status", "Notes"])
    return _sheet(
        "Reproducibility",
        phase="A1",
        df=result,
        status="INFO",
        primary_source="run_metadata, df, config_yaml_snapshot",
    )


def _build_run_health_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    rows: list[dict[str, Any]] = []
    trades = ctx.trades_100
    df = ctx.df
    fd = ctx.fd_100
    metrics = getattr(ctx.pr_100, "metrics", {}) or {}

    def add(check: str, status: str, observed: Any = "", expected: Any = "", notes: str = "") -> None:
        rows.append(
            {
                "Check": check,
                "Status": status,
                "Observed": observed,
                "Expected": expected,
                "Notes": notes,
            }
        )

    if "net_pnl_pct" in trades.columns and len(trades) > 0:
        trade_sum = float(pd.to_numeric(trades["net_pnl_pct"], errors="coerce").sum())
        summary_sum = _safe_float(metrics.get("sum_pnl_pct"))
        if not np.isfinite(summary_sum):
            add(
                "Summary vs Trades net PnL",
                "SKIP",
                trade_sum,
                "metrics.sum_pnl_pct",
                "summary metric is missing; invariant cannot be proven",
            )
        else:
            delta = abs(trade_sum - summary_sum)
            add(
                "Summary vs Trades net PnL",
                "PASS" if delta <= 1e-9 else "FAIL",
                trade_sum,
                summary_sum,
                "compares metrics.sum_pnl_pct to trades_100.net_pnl_pct sum",
            )
    else:
        add("Summary vs Trades net PnL", "SKIP", "missing", "net_pnl_pct")

    if fd is None:
        add("Filter diagnostics array length consistency", "SKIP", "missing fd_100")
        add("Filter diagnostics all arrays same length", "SKIP", "missing fd_100")
    else:
        lengths = {key: len(np.asarray(value)) for key, value in fd.items()}
        expected_len = len(df) if len(df) > 0 else getattr(ctx.pr_100, "n_bars", None)
        if expected_len is None:
            add(
                "Filter diagnostics array length consistency",
                "SKIP",
                str(lengths),
                "missing df/pr_100.n_bars",
                "expected diagnostic length cannot be proven",
            )
        else:
            add(
                "Filter diagnostics array length consistency",
                "PASS" if all(length == expected_len for length in lengths.values()) else "FAIL",
                str(lengths),
                expected_len,
            )
        unique_lengths = sorted(set(lengths.values()))
        add(
            "Filter diagnostics all arrays same length",
            "PASS" if len(unique_lengths) <= 1 else "FAIL",
            str(unique_lengths),
            "single length",
        )

    if len(df.index):
        duplicate_count = int(df.index.duplicated().sum())
        add("Duplicate timestamps", "FAIL" if duplicate_count else "PASS", duplicate_count, 0)
    else:
        add("Duplicate timestamps", "SKIP", "missing df.index", "non-empty index")

    ohlcv_cols = [col for col in ("open", "high", "low", "close", "volume") if col in df.columns]
    if ohlcv_cols:
        nan_count = int(df[ohlcv_cols].isna().sum().sum())
        add("OHLCV NaN", "WARN" if nan_count else "PASS", nan_count, 0)
    else:
        add("OHLCV NaN", "SKIP", "missing OHLCV columns")

    add("Timezone consistency", "INFO", str(getattr(df.index, "tz", None)), "descriptive")

    if {"entry_index", "exit_index"}.issubset(trades.columns) and len(df) > 0:
        entry = pd.to_numeric(trades["entry_index"], errors="coerce")
        exit_ = pd.to_numeric(trades["exit_index"], errors="coerce")
        in_bounds = entry.between(0, len(df) - 1) & exit_.between(0, len(df) - 1)
        add("Trade index bounds", "PASS" if bool(in_bounds.all()) else "FAIL", int(in_bounds.sum()), len(trades))
        order_ok = exit_ >= entry
        add("Entry/exit index order", "PASS" if bool(order_ok.all()) else "FAIL", int(order_ok.sum()), len(trades))
    else:
        add("Trade index bounds", "SKIP", "missing entry_index/exit_index or df")
        add("Entry/exit index order", "SKIP", "missing entry_index/exit_index")

    if {"entry_index", "signal_bar_index"}.issubset(trades.columns):
        signal = pd.to_numeric(trades["signal_bar_index"], errors="coerce")
        entry = pd.to_numeric(trades["entry_index"], errors="coerce")
        ok = signal <= entry
        add("Signal before entry", "PASS" if bool(ok.all()) else "FAIL", int(ok.sum()), len(trades))
    else:
        add("Signal before entry", "SKIP", "signal index cannot be proven")

    if {"entry_index", "exit_index", "entry_price", "exit_price"}.issubset(trades.columns) and {
        "high",
        "low",
    }.issubset(df.columns):
        bad_prices = 0
        for row in trades.itertuples(index=False):
            for idx_col, price_col in (("entry_index", "entry_price"), ("exit_index", "exit_price")):
                idx = _safe_float(getattr(row, idx_col, np.nan))
                price = _safe_float(getattr(row, price_col, np.nan))
                if not np.isfinite(idx) or not np.isfinite(price):
                    continue
                idx_i = int(idx)
                if idx_i < 0 or idx_i >= len(df):
                    continue
                if price < _safe_float(df.iloc[idx_i]["low"]) or price > _safe_float(df.iloc[idx_i]["high"]):
                    bad_prices += 1
        add("Execution price sanity", "PASS" if bad_prices == 0 else "WARN", bad_prices, 0)
    else:
        add("Execution price sanity", "SKIP", "missing price/OHLC columns")

    if "commission_pct" in trades.columns:
        commission = pd.to_numeric(trades["commission_pct"], errors="coerce")
        bad = int((commission < 0).sum())
        add("Commission sanity", "PASS" if bad == 0 else "FAIL", bad, 0)
    else:
        add("Commission sanity", "SKIP", "missing commission_pct")

    if len(ctx.cycle_map) == 0:
        add("Cycle map coverage", "SKIP", "cycle_map empty")
    else:
        status_series = ctx.cycle_map["mapping_status"].astype(str)
        mapped = int((status_series == "mapped").sum())
        valid_or_proven_outside = status_series.isin({"mapped", "outside_cycle"})
        bad = int((~valid_or_proven_outside).sum())
        add(
            "Cycle map coverage",
            "PASS" if bad == 0 else "WARN",
            f"mapped={mapped}; outside_cycle={int((status_series == 'outside_cycle').sum())}; bad={bad}",
            len(ctx.cycle_map),
            "outside_cycle is a valid mapping outcome; WARN only for missing/invalid/source statuses",
        )

    add(
        "Warmup facts",
        "INFO",
        getattr(ctx.pr_100, "effective_warmup", "missing"),
        "descriptive",
        "warmup is reported as a fact, not a pass/fail invariant",
    )

    statuses = [row["Status"] for row in rows]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "WARN" in statuses:
        overall = "WARN"
    elif "PASS" in statuses:
        overall = "PASS"
    else:
        overall = "INFO"
    return _sheet(
        "Run_Health",
        phase="A1",
        df=pd.DataFrame(rows, columns=["Check", "Status", "Observed", "Expected", "Notes"]),
        status=overall,
        primary_source="period_results, df, trades_100, fd_100, cycle_map",
    )


def _build_filter_diagnostics_sampled_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    fd = ctx.fd_100
    if fd is None:
        result = pd.DataFrame(
            [{"sample_index": np.nan, "sample_source": "SKIP", "Status": "SKIP", "Notes": "fd_100 missing"}],
            columns=["sample_index", "sample_source", "Status", "Notes"],
        )
        return _sheet(
            "FilterDiagnostics_sampled",
            phase="A1",
            df=result,
            status="SKIP",
            primary_source="fd_100",
            notes="fd_100 missing",
        )

    lengths = [len(np.asarray(value)) for value in fd.values()]
    n = min(lengths) if lengths else 0
    edge_indices: set[int] = set(range(min(200, n)))
    edge_indices.update(range(max(0, n - 200), n))
    trade_indices: set[int] = set()
    for row in ctx.trades_100.itertuples(index=False):
        for col in ("entry_index", "exit_index"):
            idx = _safe_float(getattr(row, col, np.nan))
            if np.isfinite(idx):
                idx_i = int(idx)
                trade_indices.update(range(max(0, idx_i - 5), min(n, idx_i + 6)))
    ordered_edges = sorted(edge_indices)
    remaining = max(0, 2000 - len(ordered_edges))
    ordered = sorted(edge_indices | set(sorted(trade_indices - edge_indices)[:remaining]))

    arrays = {key: np.asarray(value) for key, value in fd.items()}
    rows: list[dict[str, Any]] = []
    for idx in ordered:
        row = {"sample_index": idx, "sample_source": "sampled"}
        for key, arr in arrays.items():
            row[key] = arr[idx] if idx < len(arr) else np.nan
        rows.append(row)
    result = pd.DataFrame(rows)
    return _sheet(
        "FilterDiagnostics_sampled",
        phase="A1",
        df=result,
        status="INFO",
        primary_source="fd_100",
        notes=f"sampled {len(result)} rows",
    )


def _direction_label(value: Any) -> str:
    text = str(value).lower()
    if text in {"long", "1", "1.0", "+"}:
        return "long"
    if text in {"short", "-1", "-1.0", "-"}:
        return "short"
    return "unsupported"


def _build_trade_analytics_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    trades = ctx.trades_100
    columns = [
        "trade_id",
        "quality_status",
        "direction",
        "entry_index",
        "exit_index",
        "net_pnl_pct",
        "mfe_pct",
        "mae_pct",
        "entry_time_bucket",
        "cycle_id",
        "trade_idx_in_cycle",
        "mapping_status",
    ]
    if len(trades) == 0:
        return _sheet(
            "Trade_Analytics",
            phase="A2",
            df=pd.DataFrame(columns=columns),
            status="SKIP",
            primary_source="trades_100, df, cycle_map",
            notes="trades_100 empty",
        )

    cycle_lookup = (
        ctx.cycle_map.set_index("trade_id").to_dict("index")
        if len(ctx.cycle_map) and "trade_id" in ctx.cycle_map.columns
        else {}
    )
    rows: list[dict[str, Any]] = []
    for idx, trade in enumerate(trades.itertuples(index=False), start=1):
        trade_id = _trade_id_for_row(trade, idx)
        entry_idx_f = _safe_float(getattr(trade, "entry_index", np.nan))
        exit_idx_f = _safe_float(getattr(trade, "exit_index", np.nan))
        direction = _direction_label(getattr(trade, "direction", ""))
        quality = "ok"
        mfe = float("nan")
        mae = float("nan")
        entry_bucket: Any = "missing"

        if not np.isfinite(entry_idx_f):
            quality = "missing_entry_index"
        elif entry_idx_f < 0 or entry_idx_f >= len(ctx.df):
            quality = "invalid_entry_index"
        elif not np.isfinite(exit_idx_f):
            quality = "missing_exit_index"
        elif exit_idx_f < 0:
            quality = "invalid_exit_index"
        elif exit_idx_f < entry_idx_f:
            quality = "exit_before_entry"
        else:
            entry_idx = int(entry_idx_f)
            exit_idx = int(exit_idx_f)
            if exit_idx >= len(ctx.df):
                exit_idx = len(ctx.df) - 1
                quality = "exit_clipped"
            if {"high", "low"}.issubset(ctx.df.columns):
                window = ctx.df.iloc[entry_idx : exit_idx + 1]
                entry_price = _safe_float(getattr(trade, "entry_price", np.nan))
                if not np.isfinite(entry_price) or entry_price == 0:
                    entry_price = _safe_float(ctx.df.iloc[entry_idx].get("close", np.nan))
                if np.isfinite(entry_price) and entry_price != 0 and direction in {"long", "short"}:
                    high = pd.to_numeric(window["high"], errors="coerce")
                    low = pd.to_numeric(window["low"], errors="coerce")
                    if direction == "long":
                        mfe = (float(high.max()) - entry_price) / entry_price * 100.0
                        mae = (float(low.min()) - entry_price) / entry_price * 100.0
                    else:
                        mfe = (entry_price - float(low.min())) / entry_price * 100.0
                        mae = (entry_price - float(high.max())) / entry_price * 100.0
                elif direction == "unsupported":
                    quality = "unsupported_direction"
            else:
                quality = "missing_ohlc"
            if len(ctx.df.index) > entry_idx:
                ts = ctx.df.index[entry_idx]
                entry_bucket = getattr(ts, "hour", "missing")

        cycle = cycle_lookup.get(trade_id, {})
        rows.append(
            {
                "trade_id": trade_id,
                "quality_status": quality,
                "direction": direction,
                "entry_index": getattr(trade, "entry_index", np.nan),
                "exit_index": getattr(trade, "exit_index", np.nan),
                "net_pnl_pct": getattr(trade, "net_pnl_pct", np.nan),
                "mfe_pct": mfe,
                "mae_pct": mae,
                "entry_time_bucket": entry_bucket,
                "cycle_id": cycle.get("cycle_id", np.nan),
                "trade_idx_in_cycle": cycle.get("trade_idx_in_cycle", np.nan),
                "mapping_status": cycle.get("mapping_status", "missing_cycle_map"),
            }
        )
    result = pd.DataFrame(rows, columns=columns)
    status = "PASS" if set(result["quality_status"]) == {"ok"} else "WARN"
    return _sheet("Trade_Analytics", phase="A2", df=result, status=status, primary_source="trades_100, df, cycle_map")


def _profit_factor(values: pd.Series) -> float:
    profits = values[values > 0].sum()
    losses = values[values < 0].sum()
    if losses == 0:
        return float("inf") if profits > 0 else float("nan")
    return float(profits / abs(losses))


def _build_equity_drawdown_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    trades = ctx.trades_100
    columns = [
        "Block",
        "trade_id",
        "net_pnl_pct",
        "equity",
        "running_max",
        "underwater",
        "episode_id",
        "start_trade_id",
        "bottom_trade_id",
        "recovery_trade_id",
        "depth_pct",
        "duration_trades",
        "recovery_trades",
        "rank",
        "status",
        "Notes",
    ]
    if "net_pnl_pct" not in trades.columns or len(trades) == 0:
        result = pd.DataFrame(
            [
                {
                    "Block": "Summary",
                    "status": "SKIP",
                    "Notes": "trades_100.net_pnl_pct missing or empty",
                }
            ],
            columns=columns,
        )
        return _sheet("Equity_Drawdown", phase="A2", df=result, status="SKIP", primary_source="trades_100")

    pnl = pd.to_numeric(trades["net_pnl_pct"], errors="coerce").fillna(0.0)
    equity = pnl.cumsum()
    peak = equity.cummax()
    dd = equity - peak
    trade_ids = (
        trades["trade_id"].tolist()
        if "trade_id" in trades.columns
        else list(range(1, len(trades) + 1))
    )
    rows: list[dict[str, Any]] = [
        {
            "Block": "Note",
            "status": "INFO",
            "Notes": "Trade-equity drawdown excludes intratrade mark-to-market drawdown and may differ from engine/bar-level MaxDD.",
        }
    ]
    for trade_id, pnl_value, eq, peak_value, dd_value in zip(trade_ids, pnl, equity, peak, dd):
        rows.append(
            {
                "Block": "Equity by trade",
                "trade_id": trade_id,
                "net_pnl_pct": float(pnl_value),
                "equity": float(eq),
                "running_max": float(peak_value),
                "underwater": float(dd_value),
                "status": "INFO",
                "Notes": "trade_equity = cumulative sum of trades_100.net_pnl_pct",
            }
        )

    in_episode = False
    start_pos = 0
    bottom_pos = 0
    depth = 0.0
    episodes: list[dict[str, Any]] = []
    for pos, dd_value in enumerate(dd, start=1):
        if dd_value < 0 and not in_episode:
            in_episode = True
            start_pos = pos
            bottom_pos = pos
            depth = float(dd_value)
        elif dd_value < 0:
            if float(dd_value) < depth:
                depth = float(dd_value)
                bottom_pos = pos
        elif in_episode:
            episodes.append(
                {
                    "start_pos": start_pos,
                    "bottom_pos": bottom_pos,
                    "recovery_pos": pos,
                    "depth": depth,
                    "status": "recovered",
                }
            )
            in_episode = False
    if in_episode:
        episodes.append(
            {
                "start_pos": start_pos,
                "bottom_pos": bottom_pos,
                "recovery_pos": None,
                "depth": depth,
                "status": "open",
            }
        )

    for episode_id, episode in enumerate(episodes, start=1):
        start_pos = episode["start_pos"]
        bottom_pos = episode["bottom_pos"]
        recovery_pos = episode["recovery_pos"]
        rows.append(
            {
                "Block": "Drawdown episodes",
                "episode_id": episode_id,
                "start_trade_id": trade_ids[start_pos - 1],
                "bottom_trade_id": trade_ids[bottom_pos - 1],
                "recovery_trade_id": (
                    trade_ids[recovery_pos - 1] if recovery_pos is not None else np.nan
                ),
                "depth_pct": episode["depth"],
                "duration_trades": (
                    recovery_pos - start_pos if recovery_pos is not None else len(dd) - start_pos + 1
                ),
                "recovery_trades": (
                    recovery_pos - bottom_pos if recovery_pos is not None else np.nan
                ),
                "status": episode["status"],
                "Notes": "",
            }
        )
    for rank, episode in enumerate(sorted(episodes, key=lambda item: item["depth"])[:10], start=1):
        rows.append(
            {
                "Block": "Worst 10 drawdowns",
                "episode_id": episodes.index(episode) + 1,
                "depth_pct": episode["depth"],
                "duration_trades": (
                    episode["recovery_pos"] - episode["start_pos"]
                    if episode["recovery_pos"] is not None
                    else len(dd) - episode["start_pos"] + 1
                ),
                "recovery_trades": (
                    episode["recovery_pos"] - episode["bottom_pos"]
                    if episode["recovery_pos"] is not None
                    else np.nan
                ),
                "rank": rank,
                "status": episode["status"],
                "Notes": "",
            }
        )

    return _sheet(
        "Equity_Drawdown",
        phase="A2",
        df=pd.DataFrame(rows, columns=columns),
        status="INFO",
        primary_source="trades_100",
    )


def _build_cycle_summary_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    if len(ctx.cycle_map) == 0:
        result = pd.DataFrame(
            [{"Block": "Cycle overview", "Metric": "Status", "Value": "SKIP", "Notes": "cycle_map empty"}],
            columns=["Block", "Metric", "Value", "Notes"],
        )
        return _sheet("Cycle_Summary", phase="A2", df=result, status="SKIP", primary_source="cycle_map, trades_100")

    mapped = ctx.cycle_map[ctx.cycle_map["mapping_status"] == "mapped"]
    rows = [
        {"Block": "Cycle overview", "Metric": "trades_total", "Value": len(ctx.cycle_map), "Notes": ""},
        {"Block": "Cycle overview", "Metric": "trades_mapped", "Value": len(mapped), "Notes": ""},
    ]
    if len(mapped) > 0:
        counts = mapped.groupby("cycle_id", dropna=True).size()
        rows.append({"Block": "Cycle overview", "Metric": "cycles_with_trades", "Value": len(counts), "Notes": ""})
        rows.append({"Block": "Cycle overview", "Metric": "avg_trades_per_cycle", "Value": float(counts.mean()), "Notes": ""})
        distribution = mapped.groupby("trade_idx_in_cycle", dropna=True).size()
        for trade_no, count in distribution.items():
            rows.append({"Block": "Trade number in cycle", "Metric": trade_no, "Value": int(count), "Notes": ""})
    return _sheet(
        "Cycle_Summary",
        phase="A2",
        df=pd.DataFrame(rows, columns=["Block", "Metric", "Value", "Notes"]),
        status="INFO",
        primary_source="cycle_map, trades_100, fd_100",
    )


def _build_cost_sensitivity_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    trades = ctx.trades_100
    if len(trades) == 0 or not ({"gross_pnl_pct", "net_pnl_pct"} & set(trades.columns)):
        result = pd.DataFrame(
            [{"scenario": "SKIP", "unit": "bps per side", "notes": "gross_pnl_pct/net_pnl_pct missing or trades empty"}]
        )
        return _sheet("Cost_Sensitivity", phase="A2", df=result, status="SKIP", primary_source="trades_100, run_metadata")

    net = (
        pd.to_numeric(trades["net_pnl_pct"], errors="coerce")
        if "net_pnl_pct" in trades.columns
        else None
    )
    if "gross_pnl_pct" in trades.columns:
        gross = pd.to_numeric(trades["gross_pnl_pct"], errors="coerce")
        model_status = "gross_available"
    else:
        gross = pd.to_numeric(trades["net_pnl_pct"], errors="coerce")
        model_status = "proxy_from_net"

    scenarios: list[tuple[str, float | None, str]] = [
        ("actual cost", None, "from trades_100.net_pnl_pct when available"),
        ("commission 0 bps", 0.0, "simplified per-trade cost stress"),
        ("commission 0.5 bps", 0.5, "simplified per-trade cost stress"),
        ("commission 1 bps", 1.0, "simplified per-trade cost stress"),
        ("commission 2 bps", 2.0, "simplified per-trade cost stress"),
        ("slippage 0.5 bps", 0.5, "simplified per-trade cost stress"),
        ("slippage 1 bps", 1.0, "simplified per-trade cost stress"),
        ("commission 1 bps + slippage 1 bps", 2.0, "simplified per-trade cost stress"),
    ]
    rows: list[dict[str, Any]] = []
    for scenario, bps, notes in scenarios:
        if bps is None and net is not None:
            stressed = net
        elif bps is None:
            stressed = gross
        else:
            stressed = gross - (2.0 * bps / 100.0)
        avg = float(stressed.mean()) if len(stressed) else float("nan")
        med = float(stressed.median()) if len(stressed) else float("nan")
        pf = _profit_factor(stressed.dropna())
        win_rate = float((stressed > 0).mean() * 100.0) if len(stressed) else float("nan")
        equity = stressed.fillna(0.0).cumsum()
        dd = equity - equity.cummax()
        std = float(stressed.std(ddof=0)) if len(stressed) else float("nan")
        sharpe = (
            float(stressed.mean() / std)
            if np.isfinite(std) and std != 0.0
            else float("nan")
        )
        rows.append(
            {
                "scenario": scenario,
                "unit": "bps per side",
                "net_pnl_pct": float(stressed.sum()),
                "avg_trade_pct": avg,
                "median_trade_pct": med,
                "profit_factor": pf,
                "win_rate": win_rate,
                "max_dd_trade_equity": float(dd.min()) if len(dd) else float("nan"),
                "per_trade_sharpe": sharpe,
                "breakeven_bps_per_side": max(avg, 0.0) * 100.0 / 2.0 if np.isfinite(avg) else np.nan,
                "cost_model_status": model_status,
                "notes": (
                    notes
                    + "; breakeven_bps_per_side is remaining additional bps per side "
                    "until mean trade reaches zero"
                ),
            }
        )
    if "tick_size" not in ctx.run_metadata:
        rows.append(
            {
                "scenario": "tick scenarios",
                "unit": "tick",
                "net_pnl_pct": np.nan,
                "avg_trade_pct": np.nan,
                "median_trade_pct": np.nan,
                "profit_factor": np.nan,
                "win_rate": np.nan,
                "max_dd_trade_equity": np.nan,
                "per_trade_sharpe": np.nan,
                "breakeven_bps_per_side": np.nan,
                "cost_model_status": "SKIP",
                "notes": "run_metadata.tick_size missing",
            }
        )
    return _sheet(
        "Cost_Sensitivity",
        phase="A2",
        df=pd.DataFrame(rows),
        status="INFO",
        primary_source="trades_100, run_metadata",
    )


def _fd_array(ctx: DiagnosticsV2Context, key: str) -> np.ndarray | None:
    if ctx.fd_100 is None or key not in ctx.fd_100:
        return None
    arr = np.asarray(ctx.fd_100[key])
    return arr if arr.ndim > 0 else arr.reshape(1)


def _non_empty_text_mask(values: np.ndarray) -> np.ndarray:
    result = []
    for value in values:
        text = "" if pd.isna(value) else str(value).strip()
        result.append(text not in {"", "none", "None", "nan", "NaN"})
    return np.asarray(result, dtype=bool)


def _build_filter_funnel_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    mode = _diagnostics_mode(ctx)
    gate_specs = [
        ("filter_allowed_entry", "filter_allowed_entry"),
        ("candidate_threshold_ok", "candidate_threshold_ok"),
        ("confirmed_median_ok", "confirmed_median_ok"),
        ("immediate_allowed", "immediate_allowed"),
        ("wakeup_entry_all_ok", "wakeup_entry_all_ok"),
        ("wakeup_entry_candidate_height_ok", "wakeup_entry_candidate_height_ok"),
        ("wakeup_entry_candidate_age_ok", "wakeup_entry_candidate_age_ok"),
        ("wakeup_entry_atr_ok", "wakeup_entry_atr_ok"),
        ("wakeup_entry_volume_ok", "wakeup_entry_volume_ok"),
        ("volume_condition_allowed", "volume_condition_allowed"),
        ("cycle_direction_gate_passed", "cycle_direction_gate_passed"),
    ]
    rows: list[dict[str, Any]] = []
    for gate, source_column in gate_specs:
        if source_column.startswith("wakeup_") and mode != "zigzag_d":
            rows.append(
                {
                    "gate": gate,
                    "source_column": source_column,
                    "passed_count": np.nan,
                    "failed_count": np.nan,
                    "denominator": 0,
                    "pass_pct_of_denominator": np.nan,
                    "status": "SKIP",
                    "notes": f"{source_column} is only expected for zigzag Mode D",
                }
            )
            continue
        if source_column in {"volume_condition_allowed", "cycle_direction_gate_passed"} and mode != "volume_only":
            rows.append(
                {
                    "gate": gate,
                    "source_column": source_column,
                    "passed_count": np.nan,
                    "failed_count": np.nan,
                    "denominator": 0,
                    "pass_pct_of_denominator": np.nan,
                    "status": "SKIP",
                    "notes": f"{source_column} is only expected for volume-only mode",
                }
            )
            continue
        if source_column in {"candidate_threshold_ok", "confirmed_median_ok", "immediate_allowed"} and mode == "volume_only":
            rows.append(
                {
                    "gate": gate,
                    "source_column": source_column,
                    "passed_count": np.nan,
                    "failed_count": np.nan,
                    "denominator": 0,
                    "pass_pct_of_denominator": np.nan,
                    "status": "SKIP",
                    "notes": f"{source_column} is not a volume-only gate",
                }
            )
            continue
        arr = _fd_array(ctx, source_column)
        if arr is None:
            rows.append(
                {
                    "gate": gate,
                    "source_column": source_column,
                    "passed_count": np.nan,
                    "failed_count": np.nan,
                    "denominator": 0,
                    "pass_pct_of_denominator": np.nan,
                    "status": "SKIP",
                    "notes": "source column missing; independent gate summary",
                }
            )
            continue
        numeric = pd.to_numeric(pd.Series(arr), errors="coerce")
        known = numeric.notna()
        passed = int((numeric[known] != 0).sum())
        denominator = int(known.sum())
        failed = denominator - passed
        rows.append(
            {
                "gate": gate,
                "source_column": source_column,
                "passed_count": passed,
                "failed_count": failed,
                "denominator": denominator,
                "pass_pct_of_denominator": (
                    passed / denominator * 100.0 if denominator else np.nan
                ),
                "status": "INFO" if denominator else "SKIP",
                "notes": "independent gate summary; not percent from previous gate",
            }
        )
    return _sheet(
        "Filter_Funnel",
        phase="A3",
        df=pd.DataFrame(
            rows,
            columns=[
                "gate",
                "source_column",
                "passed_count",
                "failed_count",
                "denominator",
                "pass_pct_of_denominator",
                "status",
                "notes",
            ],
        ),
        status="INFO",
        primary_source="fd_100",
    )


def compute_forward_returns(
    df: pd.DataFrame,
    event_index: int,
    horizons: tuple[int, ...] = (1, 3, 5, 10),
    price_col: str = "close",
) -> dict[int, float]:
    """Raw close-to-close forward returns, not realized PnL."""
    if price_col not in df.columns or event_index < 0 or event_index >= len(df):
        return {horizon: float("nan") for horizon in horizons}
    base = _safe_float(df.iloc[event_index][price_col])
    if not np.isfinite(base) or base == 0:
        return {horizon: float("nan") for horizon in horizons}
    result: dict[int, float] = {}
    for horizon in horizons:
        target = event_index + horizon
        if target >= len(df):
            result[horizon] = float("nan")
            continue
        close = _safe_float(df.iloc[target][price_col])
        result[horizon] = (close - base) / base * 100.0 if np.isfinite(close) else float("nan")
    return result


def _block_reason(value: Any) -> str:
    if pd.isna(value):
        return "unknown/other"
    text = str(value).strip()
    if text in {"", "none", "None", "nan", "NaN"}:
        return "unknown/other"
    return text


def _build_filter_attribution_sheet(ctx: DiagnosticsV2Context) -> DiagnosticsV2Sheet:
    mode = _diagnostics_mode(ctx)
    columns = [
        "Universe",
        "Reason",
        "Horizon",
        "Events",
        "Mean forward return pct",
        "Median forward return pct",
        "Status",
        "Notes",
    ]
    rows: list[dict[str, Any]] = [
        {
            "Universe": "disclaimer",
            "Reason": "",
            "Horizon": "",
            "Events": "",
            "Mean forward return pct": np.nan,
            "Median forward return pct": np.nan,
            "Status": "INFO",
            "Notes": "All attribution values are fixed-horizon close-to-close proxies. They are not realized PnL and do not prove saved or lost profit.",
        }
    ]
    event_groups: dict[tuple[str, str], list[int]] = {}
    if "entry_index" in ctx.trades_100.columns:
        for value in ctx.trades_100["entry_index"]:
            entry_idx = _safe_float(value)
            if np.isfinite(entry_idx) and int(entry_idx) == entry_idx:
                event_groups.setdefault(("allowed_entries", "allowed_entry"), []).append(
                    int(entry_idx)
                )

    if len(ctx.signals_df) > 0:
        reason_col = "filter_block_reason"
        decision_col = "filter_decision"
        index_col = "signal_bar_index"
        if index_col in ctx.signals_df.columns:
            for row in ctx.signals_df.itertuples(index=False):
                reason = _block_reason(getattr(row, reason_col, ""))
                decision = str(getattr(row, decision_col, "")).lower()
                if "block" not in decision and reason == "unknown/other":
                    continue
                event_idx = _safe_float(getattr(row, index_col, np.nan))
                if np.isfinite(event_idx):
                    event_groups.setdefault(("blocked_signal_events", reason), []).append(int(event_idx))

    if ctx.fd_100 is not None:
        reason_arr = _fd_array(ctx, "filter_block_reason")
        allowed_arr = _fd_array(ctx, "filter_allowed_entry")
        st_flip_arr = _fd_array(ctx, "st_flip_dir")
        if reason_arr is not None:
            reason_mask = _non_empty_text_mask(reason_arr)
            if allowed_arr is not None:
                allowed_numeric = pd.to_numeric(pd.Series(allowed_arr), errors="coerce").fillna(1).to_numpy()
                for idx in np.flatnonzero((allowed_numeric == 0) & reason_mask):
                    event_groups.setdefault(("blocked_fd_attempts", _block_reason(reason_arr[idx])), []).append(int(idx))
            if st_flip_arr is not None:
                flip_numeric = pd.to_numeric(pd.Series(st_flip_arr), errors="coerce").fillna(0).to_numpy()
                for idx in np.flatnonzero((flip_numeric != 0) & reason_mask):
                    event_groups.setdefault(("blocked_fd_attempts", _block_reason(reason_arr[idx])), []).append(int(idx))
            if mode == "zigzag_d":
                for key in (
                    "wakeup_entry_candidate_height_ok",
                    "wakeup_entry_candidate_age_ok",
                    "wakeup_entry_candidate_direction_ok",
                    "wakeup_entry_trade_mode_ok",
                    "wakeup_entry_atr_ok",
                    "wakeup_entry_volume_ok",
                ):
                    component_arr = _fd_array(ctx, key)
                    if component_arr is None:
                        continue
                    numeric = pd.to_numeric(pd.Series(component_arr), errors="coerce")
                    for idx in numeric[numeric == 0].index:
                        event_groups.setdefault(("wakeup_component_failures", key), []).append(int(idx))
            else:
                rows.append(
                    {
                        "Universe": "wakeup_component_failures",
                        "Reason": "unsupported_mode",
                        "Horizon": "",
                        "Events": 0,
                        "Mean forward return pct": np.nan,
                        "Median forward return pct": np.nan,
                        "Status": "SKIP",
                        "Notes": "wakeup component attribution is only expected for zigzag Mode D",
                    }
                )

    if "close" not in ctx.df.columns:
        rows.append(
            {
                "Universe": "all",
                "Reason": "missing_close",
                "Horizon": "",
                "Events": 0,
                "Mean forward return pct": np.nan,
                "Median forward return pct": np.nan,
                "Status": "SKIP",
                "Notes": "df.close missing",
            }
        )
    elif not event_groups:
        rows.append(
            {
                "Universe": "blocked_events",
                "Reason": "unknown/other",
                "Horizon": "",
                "Events": 0,
                "Mean forward return pct": np.nan,
                "Median forward return pct": np.nan,
                "Status": "SKIP",
                "Notes": "no explicit event universe available",
            }
        )
    else:
        for (universe, reason), event_indices in sorted(event_groups.items()):
            unique_indices = sorted(set(idx for idx in event_indices if 0 <= idx < len(ctx.df)))
            for horizon in (1, 3, 5, 10):
                values = [
                    compute_forward_returns(ctx.df, idx, horizons=(horizon,))[horizon]
                    for idx in unique_indices
                ]
                series = pd.Series(values, dtype="float64").dropna()
                rows.append(
                    {
                        "Universe": universe,
                        "Reason": reason,
                        "Horizon": horizon,
                        "Events": len(unique_indices),
                        "Mean forward return pct": float(series.mean()) if len(series) else np.nan,
                        "Median forward return pct": float(series.median()) if len(series) else np.nan,
                        "Status": "INFO" if len(unique_indices) else "SKIP",
                        "Notes": "fixed-horizon close-to-close proxy",
                    }
                )
    return _sheet(
        "Filter_Attribution",
        phase="A3",
        df=pd.DataFrame(rows, columns=columns),
        status="INFO",
        primary_source="trades_100, signals_df, fd_100, df",
    )


def _build_dashboard_sheet(
    payloads_by_name: Mapping[str, DiagnosticsV2Sheet],
) -> DiagnosticsV2Sheet:
    rows: list[dict[str, Any]] = []
    for name in (
        "Run_Health",
        "Trade_Analytics",
        "Equity_Drawdown",
        "Filter_Funnel",
        "Filter_Attribution",
        "Cycle_Summary",
        "Cost_Sensitivity",
    ):
        payload = payloads_by_name.get(name)
        if payload is None:
            rows.append(
                {
                    "Source sheet": name,
                    "Metric": "status",
                    "Value": "missing",
                    "Status": "SKIP",
                    "Notes": "source payload not built",
                }
            )
        else:
            rows.append(
                {
                    "Source sheet": name,
                    "Metric": "status",
                    "Value": payload.status,
                    "Status": payload.status if payload.status in STATUS_VALUES else "INFO",
                    "Notes": payload.notes,
                }
            )
            rows.append(
                {
                    "Source sheet": name,
                    "Metric": "rows",
                    "Value": len(payload.df),
                    "Status": "INFO",
                    "Notes": "from payload cache",
                }
            )
    return _sheet(
        "Dashboard",
        phase="A3",
        df=pd.DataFrame(rows, columns=["Source sheet", "Metric", "Value", "Status", "Notes"]),
        status="INFO",
        primary_source="payload cache",
        notes="summary only from diagnostics v2 payload cache",
    )


def _build_remediation_sheet(
    payloads_by_name: Mapping[str, DiagnosticsV2Sheet],
    thresholds: pd.DataFrame,
) -> DiagnosticsV2Sheet:
    rows: list[dict[str, Any]] = []
    health = payloads_by_name.get("Run_Health")
    if health is not None and {"Check", "Status"}.issubset(health.df.columns):
        for row in health.df.itertuples(index=False):
            status = getattr(row, "Status", "")
            if status not in {"FAIL", "WARN"}:
                continue
            symptom = str(getattr(row, "Check", ""))
            rows.append(
                {
                    "Priority": "medium" if status == "FAIL" else "low",
                    "Symptom": symptom,
                    "Detection metric": symptom,
                    "Observed": getattr(row, "Observed", ""),
                    "Threshold": getattr(row, "Expected", ""),
                    "Likely cause": "needs investigation",
                    "Parameter family": "data/export",
                    "Suggested action": "Review source sheet before changing parameters.",
                    "Source sheet": "Run_Health",
                    "Confidence": "low",
                    "Status": status,
                }
            )

    cost = payloads_by_name.get("Cost_Sensitivity")
    if cost is not None and "cost_model_status" in cost.df.columns:
        proxy_rows = cost.df[cost.df["cost_model_status"] == "proxy_from_net"]
        if len(proxy_rows) > 0:
            threshold = thresholds.loc[thresholds["flag"] == "cost_fragile", "value"]
            rows.append(
                {
                    "Priority": "low",
                    "Symptom": "cost model uses net-PnL proxy",
                    "Detection metric": "cost_model_status",
                    "Observed": "proxy_from_net",
                    "Threshold": threshold.iloc[0] if len(threshold) else "missing",
                    "Likely cause": "gross_pnl_pct unavailable",
                    "Parameter family": "cost/export",
                    "Suggested action": "Consider exporting gross_pnl_pct before relying on cost stress.",
                    "Source sheet": "Cost_Sensitivity",
                    "Confidence": "low",
                    "Status": "WARN",
                }
            )

    if not rows:
        rows.append(
            {
                "Priority": "low",
                "Symptom": "No supported remediation signal",
                "Detection metric": "payload cache",
                "Observed": "missing",
                "Threshold": "missing",
                "Likely cause": "source metrics are clean or unsupported",
                "Parameter family": "none",
                "Suggested action": "No parameter recommendation from available v2 metrics.",
                "Source sheet": "Dashboard",
                "Confidence": "low",
                "Status": "SKIP",
            }
        )

    return _sheet(
        "Remediation",
        phase="A3",
        df=pd.DataFrame(
            rows,
            columns=[
                "Priority",
                "Symptom",
                "Detection metric",
                "Observed",
                "Threshold",
                "Likely cause",
                "Parameter family",
                "Suggested action",
                "Source sheet",
                "Confidence",
                "Status",
            ],
        ),
        status="INFO",
        primary_source="payload cache, thresholds",
    )


def build_diagnostics_v2_context(
    period_results: list[Any],
    *,
    pr_100: Any,
    df: pd.DataFrame | None,
    signals_df: pd.DataFrame | None,
    run_metadata: Mapping[str, Any] | None,
    trade_filter_config: Any,
    config_yaml_snapshot: Mapping[str, Any] | None,
) -> DiagnosticsV2Context:
    """Build the read-only context from already-computed tester outputs."""
    trades_100 = getattr(pr_100, "trades_df", None)
    if trades_100 is None:
        trades_100 = pd.DataFrame()
    fd_100 = getattr(pr_100, "filter_diagnostics", None)
    summary = getattr(pr_100, "filter_diagnostics_summary", None)
    cycle_mode = _cycle_mode_from_config(trade_filter_config, None)
    cycle_map = derive_trade_cycle_map(
        fd_100,
        trades_100,
        df=df,
        mode=cycle_mode,
    )
    return DiagnosticsV2Context(
        period_results=list(period_results),
        pr_100=pr_100,
        df=df if df is not None else pd.DataFrame(),
        trades_100=trades_100,
        signals_df=signals_df if signals_df is not None else pd.DataFrame(),
        fd_100=fd_100,
        filter_diagnostics_summary=summary,
        run_metadata=dict(run_metadata or {}),
        trade_filter_config=trade_filter_config,
        config_yaml_snapshot=config_yaml_snapshot,
        cycle_map=cycle_map,
        thresholds=DIAGNOSTICS_V2_THRESHOLDS.copy(deep=True),
    )


def _placeholder_sheet(
    name: str,
    *,
    phase: str,
    primary_source: str,
    notes: str = "builder not implemented in this slice",
) -> DiagnosticsV2Sheet:
    df = pd.DataFrame(
        [
            {
                "Status": "SKIP",
                "Primary source": primary_source,
                "Notes": notes,
            }
        ],
        columns=["Status", "Primary source", "Notes"],
    )
    return DiagnosticsV2Sheet(
        name=name,
        phase=phase,
        df=df,
        status="SKIP",
        primary_source=primary_source,
        notes=notes,
    )


def _build_index_sheet(payloads: list[DiagnosticsV2Sheet]) -> DiagnosticsV2Sheet:
    purpose_by_name = {
        "Reproducibility": "Run metadata and input-data reproducibility facts",
        "Dashboard": "Summary built from diagnostics v2 payload cache",
        "Run_Health": "Verifiable consistency and data-quality checks",
        "Trade_Analytics": "Per-trade analytics derived from exported trades and OHLC",
        "Equity_Drawdown": "Trade-equity drawdown summary",
        "Filter_Funnel": "Independent filter gate summary",
        "Filter_Attribution": "Fixed-horizon close-to-close proxy attribution",
        "Cycle_Summary": "Cycle-map coverage and trade count summary",
        "Cost_Sensitivity": "Simplified per-trade cost stress model",
        "Remediation": "Conservative low-confidence review prompts from source sheets",
        "FilterDiagnostics_sampled": "Bounded sample of raw filter diagnostics arrays",
    }
    rows = [
        {
            "Sheet": payload.name,
            "Phase": payload.phase,
            "Purpose": purpose_by_name.get(payload.name, "Diagnostics v2 placeholder"),
            "Status": payload.status,
            "Primary source": payload.primary_source,
            "Notes": payload.notes,
        }
        for payload in payloads
    ]
    return DiagnosticsV2Sheet(
        name="Index",
        phase="A0",
        df=pd.DataFrame(
            rows,
            columns=["Sheet", "Phase", "Purpose", "Status", "Primary source", "Notes"],
        ),
        status="INFO",
        primary_source="payload cache",
        notes="built from enabled diagnostics v2 payload cache",
    )


def build_enabled_v2_sheets(
    ctx: DiagnosticsV2Context,
    flags: Mapping[str, bool],
) -> list[DiagnosticsV2Sheet]:
    """Build all enabled diagnostics v2 payloads before Excel writing."""
    payloads_by_name: dict[str, DiagnosticsV2Sheet] = {}
    builders = {
        "reproducibility": _build_reproducibility_sheet,
        "run_health": _build_run_health_sheet,
        "trade_analytics": _build_trade_analytics_sheet,
        "equity_drawdown": _build_equity_drawdown_sheet,
        "filter_funnel": _build_filter_funnel_sheet,
        "filter_attribution": _build_filter_attribution_sheet,
        "cycle_summary": _build_cycle_summary_sheet,
        "cost_sensitivity": _build_cost_sensitivity_sheet,
        "filter_diagnostics_sampled": _build_filter_diagnostics_sampled_sheet,
    }
    for flag, sheet_name in PHASE_A_FLAG_TO_SHEET.items():
        if flag == "index" or not flags.get(flag, False):
            continue
        if flag in {"dashboard", "remediation"}:
            continue
        builder = builders.get(flag)
        if builder is None:
            payloads_by_name[sheet_name] = _placeholder_sheet(
                sheet_name,
                phase="A3",
                primary_source="payload cache",
                notes="scheduled for WP6 interpretive sheet implementation",
            )
        else:
            payloads_by_name[sheet_name] = builder(ctx)

    if flags.get("dashboard", False):
        payloads_by_name["Dashboard"] = _build_dashboard_sheet(payloads_by_name)
    if flags.get("remediation", False):
        payloads_by_name["Remediation"] = _build_remediation_sheet(
            payloads_by_name,
            ctx.thresholds,
        )

    if flags.get("index", False):
        ordered_non_index = [
            payloads_by_name[name]
            for name in V2_SHEET_ORDER
            if name != "Index" and name in payloads_by_name
        ]
        payloads_by_name["Index"] = _build_index_sheet(ordered_non_index)

    return [payloads_by_name[name] for name in V2_SHEET_ORDER if name in payloads_by_name]
