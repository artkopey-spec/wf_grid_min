"""Add timing instrumentation to xlsx writing phase."""
with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'rb') as f:
    content = f.read()

# 1. Time the entire trades-per-period loop
OLD1 = (
    b'        # \xe2\x94\x80\xe2\x94\x80 Metrics and Trades sheets for each period \xe2\x94\x80\xe2\x94\x80\n'
    b'        for pr in period_results:\n'
)
NEW1 = (
    b'        # \xe2\x94\x80\xe2\x94\x80 Metrics and Trades sheets for each period \xe2\x94\x80\xe2\x94\x80\n'
    b'        # #region agent log\n'
    b'        _t_trades_loop = _time.perf_counter()\n'
    b'        # #endregion\n'
    b'        for pr in period_results:\n'
)

# 2. Time after trades loop / before signals
OLD2 = (
    b'        # \xe2\x94\x80\xe2\x94\x80 Signals sheet \xe2\x94\x80\xe2\x94\x80\n'
    b'        if export_signals and signals_df is not None:\n'
    b'            _write_signals_sheet(writer, "Signals", signals_df)\n'
)
NEW2 = (
    b'        # #region agent log\n'
    b'        _dbg("trades+metrics sheets written", {"n_periods": len(period_results), "elapsed_s": round(_time.perf_counter()-_t_trades_loop,3)}, "H-D")\n'
    b'        # #endregion\n'
    b'        # \xe2\x94\x80\xe2\x94\x80 Signals sheet \xe2\x94\x80\xe2\x94\x80\n'
    b'        # #region agent log\n'
    b'        _t_sig = _time.perf_counter()\n'
    b'        # #endregion\n'
    b'        if export_signals and signals_df is not None:\n'
    b'            _write_signals_sheet(writer, "Signals", signals_df)\n'
    b'        # #region agent log\n'
    b'        _dbg("signals sheet written", {"n_rows": len(signals_df) if signals_df is not None else 0, "elapsed_s": round(_time.perf_counter()-_t_sig,3)}, "H-D")\n'
    b'        # #endregion\n'
)

# 3. Time false start sheet
OLD3 = (
    b'        # \xe2\x94\x80\xe2\x94\x80 False start sheet \xe2\x94\x80\xe2\x94\x80\n'
    b'        trades_100_raw = pr_100.trades_df if pr_100 is not None else None\n'
    b'        if export_false_start:\n'
)
NEW3 = (
    b'        # \xe2\x94\x80\xe2\x94\x80 False start sheet \xe2\x94\x80\xe2\x94\x80\n'
    b'        # #region agent log\n'
    b'        _t_fs = _time.perf_counter()\n'
    b'        # #endregion\n'
    b'        trades_100_raw = pr_100.trades_df if pr_100 is not None else None\n'
    b'        if export_false_start:\n'
)

# 4. Time after false start / before filter sheets
OLD4 = (
    b'        # \xe2\x94\x80\xe2\x94\x80 Optional filter sheets (gated on enabled + diagnostics flags) \xe2\x94\x80\xe2\x94\x80\n'
)
NEW4 = (
    b'        # #region agent log\n'
    b'        _dbg("false_start sheet written", {"elapsed_s": round(_time.perf_counter()-_t_fs,3)}, "H-D")\n'
    b'        # #endregion\n'
    b'        # \xe2\x94\x80\xe2\x94\x80 Optional filter sheets (gated on enabled + diagnostics flags) \xe2\x94\x80\xe2\x94\x80\n'
)

replacements = [(OLD1, NEW1), (OLD2, NEW2), (OLD3, NEW3), (OLD4, NEW4)]
for i, (old, new) in enumerate(replacements, 1):
    if old in content:
        content = content.replace(old, new, 1)
        print(f"Replacement {i}: OK")
    else:
        print(f"Replacement {i}: NOT FOUND")
        idx = content.find(old[:40])
        print(f"  First 40 bytes search idx: {idx}")

with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'wb') as f:
    f.write(content)
print("Done.")
