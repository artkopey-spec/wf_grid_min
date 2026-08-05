with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'rb') as f:
    content = f.read()

em = b'\xd0\xb2\xd0\x82\xe2\x80\x9d'

old = (
    b'            # cycle ' + em + b' ZigZag path\n'
    b'            if export_cycle and zigzag_enabled and fd_100 is not None:\n'
    b'                cycle_df = _build_cycle_sheet_df(fd_100, df, trades_100_raw)\n'
    b'                _write_cycle_sheet(writer, cycle_df)\n'
    b'            # cycle ' + em + b' volume-only path (mutually exclusive with ZigZag path above)\n'
    b'            elif export_cycle and volume_enabled and not zigzag_enabled and fd_100 is not None:\n'
    b'                vol_cycle_df = _build_volume_cycle_sheet_df(fd_100, df, trades_100_raw)\n'
    b'                _write_cycle_sheet(writer, vol_cycle_df)\n'
    b'\n'
    b'        for payload in diagnostics_v2_payloads:\n'
    b'            _write_diagnostics_v2_sheet(writer, payload.name, payload.df)\n'
    b'\n'
    b'    return output_path'
)

new = (
    b'            # cycle ' + em + b' ZigZag path\n'
    b'            if export_cycle and zigzag_enabled and fd_100 is not None:\n'
    b'                # #region agent log\n'
    b'                _t_cyc = _time.perf_counter()\n'
    b'                # #endregion\n'
    b'                cycle_df = _build_cycle_sheet_df(fd_100, df, trades_100_raw)\n'
    b'                # #region agent log\n'
    b"                _dbg('_build_cycle_sheet_df done', {'n_rows': len(cycle_df), 'elapsed_s': round(_time.perf_counter()-_t_cyc,3), 'total_cycle_trades_calls': _cycle_trades_call_count, 'total_cycle_trades_s': round(_cycle_trades_total_s,3)}, 'H-A')\n"
    b'                # #endregion\n'
    b'                _write_cycle_sheet(writer, cycle_df)\n'
    b'            # cycle ' + em + b' volume-only path (mutually exclusive with ZigZag path above)\n'
    b'            elif export_cycle and volume_enabled and not zigzag_enabled and fd_100 is not None:\n'
    b'                vol_cycle_df = _build_volume_cycle_sheet_df(fd_100, df, trades_100_raw)\n'
    b'                _write_cycle_sheet(writer, vol_cycle_df)\n'
    b'\n'
    b'        # #region agent log\n'
    b'        _t_v2write = _time.perf_counter()\n'
    b'        # #endregion\n'
    b'        for payload in diagnostics_v2_payloads:\n'
    b'            _write_diagnostics_v2_sheet(writer, payload.name, payload.df)\n'
    b'        # #region agent log\n'
    b"        _dbg('diagnostics_v2 sheets written', {'n_sheets': len(diagnostics_v2_payloads), 'elapsed_s': round(_time.perf_counter()-_t_v2write,3)}, 'H-D')\n"
    b'        # #endregion\n'
    b'\n'
    b'    return output_path'
)

if old in content:
    content = content.replace(old, new, 1)
    with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'wb') as f:
        f.write(content)
    print('OK replaced')
else:
    print('NOT FOUND')
    idx = content.find(b'ZigZag path')
    print('ZigZag path idx:', idx)
    print(repr(content[idx-30:idx+300]))
