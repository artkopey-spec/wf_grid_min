"""Fix O(n^2) apply in _match_trade_to_signal_row."""
with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'rb') as f:
    content = f.read()

# exact mojibake variant found in file
moj = b'\xd0\xb2\xd0\x82\xe2\x80\x9d'

OLD = (
    b'    # Step 1 ' + moj + b' exec price matches entry price\n'
    b'    price_hits = before[\n'
    b'        before.apply(\n'
    b'            lambda r: _price_matches_exec_entry(r.get("exec_price"), entry_price),\n'
    b'            axis=1,\n'
    b'        )\n'
    b'    ]\n'
    b'    if len(price_hits) > 0:\n'
    b'        best_idx = price_hits["signal_time"].idxmax()\n'
    b'        return price_hits.loc[best_idx]\n'
    b'\n'
    b'    # Step 2 ' + moj + b' nearest preceding (max signal_time)\n'
    b'    best_idx = before["signal_time"].idxmax()\n'
    b'    return before.loc[best_idx]'
)

NEW = (
    b'    # Step 1 \xe2\x80\x94 exec price matches entry price (vectorized, replaces per-row apply)\n'
    b'    price_hits = before.iloc[:0]  # empty default\n'
    b'    if (\n'
    b'        "exec_price" in before.columns\n'
    b'        and entry_price is not None\n'
    b'        and not pd.isna(entry_price)\n'
    b'    ):\n'
    b'        try:\n'
    b'            ep_f = float(entry_price)\n'
    b'            exec_num = pd.to_numeric(before["exec_price"], errors="coerce")\n'
    b'            valid = exec_num.notna()\n'
    b'            close_mask = valid & np.isclose(\n'
    b'                exec_num.fillna(0.0).to_numpy(),\n'
    b'                ep_f,\n'
    b'                rtol=1e-5,\n'
    b'                atol=1e-8,\n'
    b'            )\n'
    b'            price_hits = before[close_mask]\n'
    b'        except (TypeError, ValueError):\n'
    b'            pass\n'
    b'    if len(price_hits) > 0:\n'
    b'        best_idx = price_hits["signal_time"].idxmax()\n'
    b'        return price_hits.loc[best_idx]\n'
    b'\n'
    b'    # Step 2 \xe2\x80\x94 nearest preceding (max signal_time)\n'
    b'    best_idx = before["signal_time"].idxmax()\n'
    b'    return before.loc[best_idx]'
)

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    print("OK replaced")
else:
    print("NOT FOUND")
    idx = content.find(b'Step 1')
    print(repr(content[idx:idx+40]))

with open(r'C:\3.1_wf_grid\donor\supertrend_optimizer\io\excel_tester.py', 'wb') as f:
    f.write(content)
print("Done.")
