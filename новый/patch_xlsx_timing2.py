"""Add timing instrumentation to xlsx writing phase (byte-correct version)."""
with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'rb') as f:
    content = f.read()

moj = b'\xd0\xb2\xe2\x80\x9d\xd0\x82\xd0\xb2\xe2\x80\x9d\xd0\x82'  # mojibake em-dash pair

# 1. Before the trades/metrics loop
OLD1 = b'   # ' + moj + b' Metrics and Trades sheets for each period ' + moj + b'\n        for pr in period_results:\n'
NEW1 = (b'   # ' + moj + b' Metrics and Trades sheets for each period ' + moj + b'\n'
        b'        # #region agent log\n'
        b'        _t_trades_loop = _time.perf_counter()\n'
        b'        # #endregion\n'
        b'        for pr in period_results:\n')

# 2. After trades loop, before Signals sheet
OLD2 = (b'\n        # ' + moj + b' Signals sheet ' + moj + b'\n'
        b'        if export_signals and signals_df is not None:\n'
        b'            _write_signals_sheet(writer, "Signals", signals_df)\n')
NEW2 = (b'\n'
        b'        # #region agent log\n'
        b'        _dbg("trades+metrics sheets written", {"n_periods": len(period_results), "elapsed_s": round(_time.perf_counter()-_t_trades_loop,3)}, "H-D")\n'
        b'        _t_sig = _time.perf_counter()\n'
        b'        # #endregion\n'
        b'        # ' + moj + b' Signals sheet ' + moj + b'\n'
        b'        if export_signals and signals_df is not None:\n'
        b'            _write_signals_sheet(writer, "Signals", signals_df)\n'
        b'        # #region agent log\n'
        b'        _dbg("signals sheet written", {"n_rows": len(signals_df) if signals_df is not None else 0, "elapsed_s": round(_time.perf_counter()-_t_sig,3)}, "H-D")\n'
        b'        # #endregion\n')

# 3. Before false start, after signals
OLD3 = (b'\n        # ' + moj + b' False start sheet ' + moj + b'\n'
        b'        trades_100_raw = pr_100.trades_df if pr_100 is not None else None\n')
NEW3 = (b'\n'
        b'        # #region agent log\n'
        b'        _t_fs = _time.perf_counter()\n'
        b'        # #endregion\n'
        b'        # ' + moj + b' False start sheet ' + moj + b'\n'
        b'        trades_100_raw = pr_100.trades_df if pr_100 is not None else None\n')

# 4. Before optional filter sheets
OLD4 = b'   # ' + moj + b' Optional filter sheets (gated on enabled + diagnostics flags) ' + moj + b'\n        if filter_enabl'
NEW4 = (b'   # #region agent log\n'
        b'        _dbg("false_start sheet written", {"elapsed_s": round(_time.perf_counter()-_t_fs,3)}, "H-D")\n'
        b'        # #endregion\n'
        b'   # ' + moj + b' Optional filter sheets (gated on enabled + diagnostics flags) ' + moj + b'\n        if filter_enabl')

replacements = [(OLD1, NEW1), (OLD2, NEW2), (OLD3, NEW3), (OLD4, NEW4)]
for i, (old, new) in enumerate(replacements, 1):
    if old in content:
        content = content.replace(old, new, 1)
        print(f"Replacement {i}: OK")
    else:
        print(f"Replacement {i}: NOT FOUND - first 60 bytes: {repr(old[:60])}")

with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'wb') as f:
    f.write(content)
print("Done.")
