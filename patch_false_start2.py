"""Full rewrite of _match_trade_to_signal_row and signal matching to O(log n)."""
with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'rb') as f:
    content = f.read()

# Find and replace the entire _match_trade_to_signal_row + _prepare_signals_lookup block
# We know _match_trade_to_signal_row starts at some known location.

# Find the start of _match_trade_to_signal_row
start_marker = b'def _match_trade_to_signal_row(\n'
end_marker = b'def _classify_false_start_type('

idx_start = content.find(start_marker)
idx_end = content.find(end_marker)

if idx_start == -1:
    print("START NOT FOUND")
elif idx_end == -1:
    print("END NOT FOUND")
else:
    print(f"Found block: {idx_start} to {idx_end}")
    
    NEW_BLOCK = b'''def _prepare_signals_lookup(signals_df):
    """Copy signals with naive signal_time for matching (internal column names)."""
    if signals_df is None or len(signals_df) == 0:
        return None
    df = signals_df.copy()
    if "signal_time" not in df.columns or "direction" not in df.columns:
        return None
    st = pd.to_datetime(df["signal_time"])
    if pd.api.types.is_datetime64_any_dtype(st):
        if st.dt.tz is not None:
            st = st.dt.tz_localize(None)
    df["signal_time"] = st
    return df


def _build_fast_signal_index(signals_prep):
    """Pre-sort signals by direction+time for O(log n) per-trade matching.

    Returns dict[direction -> {\'times_ns\', \'exec_prices\', \'rows\'}]
    Replaces per-trade pandas boolean filtering with binary search.
    """
    if signals_prep is None or len(signals_prep) == 0:
        return {}
    index = {}
    for direction, group in signals_prep.groupby("direction", sort=False):
        g = group.sort_values("signal_time").reset_index(drop=True)
        times_ns = g["signal_time"].view("int64").to_numpy(copy=True)
        has_exec = "exec_price" in g.columns
        exec_prices = (
            pd.to_numeric(g["exec_price"], errors="coerce").to_numpy(dtype=np.float64)
            if has_exec else np.full(len(g), np.nan, dtype=np.float64)
        )
        rows = g.to_dict("records")
        index[str(direction)] = {
            "times_ns": times_ns,
            "exec_prices": exec_prices,
            "rows": rows,
        }
    return index


def _match_signal_fast(direction, entry_t, entry_price, index):
    """O(log n) signal match using pre-sorted index. Returns dict or None."""
    data = index.get(str(direction))
    if data is None:
        return None
    try:
        entry_ns = int(pd.Timestamp(entry_t).value)
    except Exception:
        return None
    times_ns = data["times_ns"]
    hi = int(np.searchsorted(times_ns, entry_ns, side="right"))
    if hi == 0:
        return None
    exec_prices = data["exec_prices"][:hi]
    try:
        if entry_price is not None and not pd.isna(entry_price):
            ep_f = float(entry_price)
            if np.isfinite(ep_f):
                valid = np.isfinite(exec_prices)
                price_match = valid & np.isclose(exec_prices, ep_f, rtol=1e-5, atol=1e-8)
                if price_match.any():
                    best = int(np.flatnonzero(price_match)[-1])
                    return data["rows"][best]
    except (TypeError, ValueError):
        pass
    return data["rows"][hi - 1]


def _match_trade_to_signal_row(trade_row, signals_prep):
    """Legacy API shim \xe2\x80\x94 kept for external callers; not used in hot path."""
    if signals_prep is None or len(signals_prep) == 0:
        return None
    t_dir = trade_row.get("direction")
    if pd.isna(t_dir):
        return None
    entry_t = _normalize_timestamp_naive(trade_row.get("entry_time"))
    if pd.isna(entry_t):
        return None
    entry_price = trade_row.get("entry_price")
    idx = _build_fast_signal_index(signals_prep)
    result = _match_signal_fast(t_dir, entry_t, entry_price, idx)
    return pd.Series(result) if result is not None else None


'''

    content = content[:idx_start] + NEW_BLOCK + content[idx_end:]
    print(f"Replaced {idx_end - idx_start} bytes with {len(NEW_BLOCK)} bytes")

# Now also fix _build_false_start_sheet_df to use fast index + avoid iterrows
# Find the loop section
loop_start = b'    # #region agent log\n    _t_fs_loop = _time.perf_counter()\n    _dbg("false_start loop start"'
loop_end_marker = b'\n    rows_out: List[dict] = []\n    for _, trow in short_tr.iterrows():\n        gross = trow["gross_pnl_pct"] if need_gross else float("nan")\n        net = trow.get("net_pnl_pct")\n        bars = trow.get("bars_held")\n\n        matched = _match_trade_to_signal_row(trow, signals_lookup)\n'

idx_loop = content.find(loop_start)
if idx_loop == -1:
    print("LOOP START NOT FOUND")
else:
    # Find the end of the 'matched' assignment
    idx_after = content.find(loop_end_marker, idx_loop)
    if idx_after == -1:
        print("LOOP END NOT FOUND")
    else:
        end_of_patch = idx_after + len(loop_end_marker)
        old_loop = content[idx_loop:end_of_patch]
        new_loop = (
            b'    # #region agent log\n'
            b'    _t_fs_loop = _time.perf_counter()\n'
            b'    _dbg("false_start loop start", {"n_short_trades": len(short_tr), "n_signals": len(signals_lookup) if signals_lookup is not None else 0}, "H-D")\n'
            b'    # #endregion\n'
            b'    # Build fast O(log n) signal index once before the loop\n'
            b'    _fast_sig_index = _build_fast_signal_index(signals_lookup)\n'
            b'    rows_out: List[dict] = []\n'
            b'    for _, trow in short_tr.iterrows():\n'
            b'        gross = trow["gross_pnl_pct"] if need_gross else float("nan")\n'
            b'        net = trow.get("net_pnl_pct")\n'
            b'        bars = trow.get("bars_held")\n'
            b'\n'
            b'        matched = _match_signal_fast(trow.get("direction"), trow.get("entry_time"), trow.get("entry_price"), _fast_sig_index)\n'
        )
        content = content[:idx_loop] + new_loop + content[end_of_patch:]
        print("Loop patched OK")

with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'wb') as f:
    f.write(content)
print("Done.")
