"""Add timing inside _build_false_start_sheet_df to count short trades."""
with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'rb') as f:
    content = f.read()

OLD = (
    b'    short_tr = trades_df_raw[trades_df_raw["bars_held"] < false_start_max_bars].copy()\n'
    b'    if len(short_tr) == 0:\n'
    b'        return empty\n'
    b'\n'
    b'    need_gross = "gross_pnl_pct" in short_tr.columns\n'
    b'\n'
    b'    signals_lookup = _prepare_signals_lookup(signals_df)\n'
    b'\n'
    b'    rows_out: List[dict] = []\n'
    b'    for _, trow in short_tr.iterrows():\n'
)

NEW = (
    b'    short_tr = trades_df_raw[trades_df_raw["bars_held"] < false_start_max_bars].copy()\n'
    b'    if len(short_tr) == 0:\n'
    b'        return empty\n'
    b'\n'
    b'    need_gross = "gross_pnl_pct" in short_tr.columns\n'
    b'\n'
    b'    signals_lookup = _prepare_signals_lookup(signals_df)\n'
    b'\n'
    b'    # #region agent log\n'
    b'    _t_fs_loop = _time.perf_counter()\n'
    b'    _dbg("false_start loop start", {"n_short_trades": len(short_tr), "n_signals": len(signals_lookup) if signals_lookup is not None else 0}, "H-D")\n'
    b'    # #endregion\n'
    b'    rows_out: List[dict] = []\n'
    b'    for _, trow in short_tr.iterrows():\n'
)

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    print("OK replaced")
else:
    print("NOT FOUND")

with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'wb') as f:
    f.write(content)
print("Done.")
