from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from supertrend_optimizer.io.diagnostics_v2 import (
    _diagnostics_mode,
    build_diagnostics_v2_context,
    derive_trade_cycle_map,
)


def test_reversal_mode_is_explicit_and_cycle_mapping_is_unsupported() -> None:
    trades = pd.DataFrame([{"trade_id": 1, "entry_index": 1}])
    period = SimpleNamespace(
        trades_df=trades,
        filter_diagnostics={"time_filter_in_window": np.ones(3, dtype=np.int8)},
        filter_diagnostics_summary={},
        filter_config_snapshot={"mode": "reversal_only"},
    )
    ctx = build_diagnostics_v2_context(
        [period],
        pr_100=period,
        df=pd.DataFrame(index=pd.date_range("2026-01-01", periods=3)),
        signals_df=pd.DataFrame(),
        run_metadata={},
        trade_filter_config=SimpleNamespace(),
        config_yaml_snapshot=None,
    )
    assert _diagnostics_mode(ctx) == "reversal"
    assert ctx.cycle_map.loc[0, "mapping_status"] == "unsupported_mode"
    direct = derive_trade_cycle_map(
        period.filter_diagnostics,
        trades,
        mode="reversal",
    )
    assert direct.loc[0, "mapping_status"] == "unsupported_mode"
