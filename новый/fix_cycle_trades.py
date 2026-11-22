"""Patch _build_cycle_sheet_df to use O(n log n) trade lookup instead of O(n^2)."""
with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'rb') as f:
    content = f.read()

# ── locate the exact call site ──────────────────────────────────────────────
# Pattern: the single line "        cycle_trades = _cycle_trades_for_segment(trades_df, start_bar, end_bar)\n"
# inside _build_cycle_sheet_df (indented 8 spaces)

OLD_CALL = b'        cycle_trades = _cycle_trades_for_segment(trades_df, start_bar, end_bar)\n'

# We will insert a precompute block just before the main "for start_bar, end_bar" loop,
# and replace the per-segment call with a fast binary-search lookup.
#
# The anchor for the precompute insertion:
OLD_LOOP_START = (
    b'    index = df.iloc[:n].index\n'
    b'    rows: List[Dict[str, Any]] = []\n'
    b'    for start_bar, end_bar in _completed_cycle_segments(state_arr):\n'
)

NEW_LOOP_START = (
    b'    index = df.iloc[:n].index\n'
    b'    rows: List[Dict[str, Any]] = []\n'
    b'\n'
    b'    # --- Fast O(n log n) trade-index precomputation (fixes O(n^2) bottleneck) ---\n'
    b'    # Build a sorted array of entry_signal_idx values so each cycle segment\n'
    b'    # can be answered with two binary-search calls instead of a full scan.\n'
    b'    _entry_idx_sorted: Optional[np.ndarray] = None\n'
    b'    _pnl_sorted: Optional[np.ndarray] = None\n'
    b'    _rows_sorted: Optional[List[Any]] = None\n'
    b'    _has_net_pnl = (\n'
    b'        trades_df is not None\n'
    b'        and len(trades_df) > 0\n'
    b'        and "entry_index" in trades_df.columns\n'
    b'        and "net_pnl_pct" in trades_df.columns\n'
    b'    )\n'
    b'    if trades_df is not None and len(trades_df) > 0 and "entry_index" in trades_df.columns:\n'
    b'        _keys: List[int] = []\n'
    b'        _pnls: List[float] = []\n'
    b'        _trows: List[Any] = []\n'
    b'        for _row in trades_df.itertuples(index=False):\n'
    b'            _ei = getattr(_row, "entry_index", np.nan)\n'
    b'            try:\n'
    b'                if not pd.isna(_ei):\n'
    b'                    _keys.append(max(int(_ei) - 1, 0))\n'
    b'                    _pnl_v = getattr(_row, "net_pnl_pct", np.nan) if _has_net_pnl else np.nan\n'
    b'                    try:\n'
    b'                        _pnls.append(float(_pnl_v))\n'
    b'                    except (TypeError, ValueError):\n'
    b'                        _pnls.append(float("nan"))\n'
    b'                    _trows.append(_row)\n'
    b'            except (TypeError, ValueError):\n'
    b'                pass\n'
    b'        if _keys:\n'
    b'            _sort_order = np.argsort(_keys, kind="stable")\n'
    b'            _entry_idx_sorted = np.array(_keys, dtype=np.int64)[_sort_order]\n'
    b'            _pnl_sorted = np.array(_pnls, dtype=np.float64)[_sort_order]\n'
    b'            _rows_sorted = [_trows[i] for i in _sort_order]\n'
    b'    # --- end precompute ---\n'
    b'\n'
    b'    for start_bar, end_bar in _completed_cycle_segments(state_arr):\n'
)

NEW_CALL = (
    b'        # fast O(log n) segment lookup\n'
    b'        if _entry_idx_sorted is not None and _rows_sorted is not None:\n'
    b'            _lo = int(np.searchsorted(_entry_idx_sorted, start_bar, side="left"))\n'
    b'            _hi = int(np.searchsorted(_entry_idx_sorted, end_bar, side="right"))\n'
    b'            cycle_trades = _rows_sorted[_lo:_hi]\n'
    b'            # fast PnL aggregation (avoids re-iterating cycle_trades)\n'
    b'            if not cycle_trades:\n'
    b'                _cycle_sum_pnl: Any = float("nan")\n'
    b'                _cycle_pos_pct: Any = float("nan")\n'
    b'            elif _pnl_sorted is not None:\n'
    b'                _seg_pnl = _pnl_sorted[_lo:_hi]\n'
    b'                if not np.all(np.isfinite(_seg_pnl)):\n'
    b'                    _cycle_sum_pnl = float("nan")\n'
    b'                    _cycle_pos_pct = float("nan")\n'
    b'                else:\n'
    b'                    _cycle_sum_pnl = float(np.sum(_seg_pnl))\n'
    b'                    _n_pos = int(np.sum(_seg_pnl > 0.0))\n'
    b'                    _cycle_pos_pct = _n_pos / len(_seg_pnl) * 100.0\n'
    b'            else:\n'
    b'                _cycle_sum_pnl = float("nan")\n'
    b'                _cycle_pos_pct = float("nan")\n'
    b'        else:\n'
    b'            cycle_trades = _cycle_trades_for_segment(trades_df, start_bar, end_bar)\n'
    b'            _cycle_sum_pnl = _cycle_final_result_pct(trades_df, cycle_trades)\n'
    b'            _cycle_pos_pct = _cycle_positive_trades_pct(trades_df, cycle_trades)\n'
)

if OLD_LOOP_START not in content:
    print("ERROR: OLD_LOOP_START not found")
elif OLD_CALL not in content:
    print("ERROR: OLD_CALL not found")
else:
    content = content.replace(OLD_LOOP_START, NEW_LOOP_START, 1)
    content = content.replace(OLD_CALL, NEW_CALL, 1)
    print("Both patterns replaced OK")

# Also replace the two downstream calls that use cycle_trades for PnL
# from "_cycle_final_result_pct(trades_df, cycle_trades)" to just "_cycle_sum_pnl"
# and "_cycle_positive_trades_pct(trades_df, cycle_trades)" to "_cycle_pos_pct"
OLD_FIN = b'                _cycle_final_result_pct(trades_df, cycle_trades)\n'
NEW_FIN = b'                _cycle_sum_pnl\n'
OLD_POS = b'                _cycle_positive_trades_pct(trades_df, cycle_trades)\n'
NEW_POS = b'                _cycle_pos_pct\n'

if OLD_FIN in content and OLD_POS in content:
    content = content.replace(OLD_FIN, NEW_FIN, 1)
    content = content.replace(OLD_POS, NEW_POS, 1)
    print("PnL call sites replaced OK")
else:
    print("WARNING: PnL call sites not found, checking...")
    idx = content.find(b'_cycle_final_result_pct')
    print("  _cycle_final_result_pct idx:", idx)
    idx2 = content.find(b'_cycle_positive_trades_pct')
    print("  _cycle_positive_trades_pct idx:", idx2)

with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'wb') as f:
    f.write(content)
print("File written.")
