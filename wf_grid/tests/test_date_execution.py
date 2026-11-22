import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.date_execution import DateExecution, executed_legs, project_window


C = 0.0000667


def sample(opens, closes, positions, boundary, start=0):
    opens, closes, positions = map(np.asarray, (opens, closes, positions))
    boundary = np.asarray(boundary, dtype=bool)
    dates = DateExecution(~boundary, boundary)
    legs = executed_legs(positions, dates, opens, closes,
                         pd.date_range("2024-01-01", periods=len(opens)),
                         np.ones(len(opens)), 50, C)
    returns, _ = project_window(legs, opens, closes, C, start)
    return legs, returns


@pytest.mark.parametrize("side", [-1, 1])
@pytest.mark.parametrize("following", [90, 190])
def test_observed_boundary(side, following):
    legs, returns = sample([100, 102, following], [101, 105, following],
                           [side, side, 0], [0, 1, 0])
    assert list(legs.exit_phase) == ["close"]
    assert list(legs.exit_global_index) == [51]
    assert legs.iloc[0].net_pnl_pct == side * 5 - 0.01334
    np.testing.assert_allclose(returns, [side * .02 - C, side * (105/102-1) - C],
                               atol=1e-12, rtol=0)


def test_terminal_boundary():
    _, returns = sample([100, 102], [99, 105], [1, 1], [0, 1])
    assert len(returns) == 1
    assert 1 + returns[0] == pytest.approx((1.02-C) * (105/102-C), abs=1e-12)


@pytest.mark.parametrize("start", [1, 2])
def test_prepend_close_excluded(start):
    _, returns = sample([100, 102, 105, 90], [100, 102, 106, 90],
                        [1, 1, 1, 0], [0, 0, 1, 0], start)
    np.testing.assert_array_equal(returns, 0)


def test_prepend_reversal_and_pending_final_entry():
    legs, returns = sample([100, 105, 104], [99, 100, 90], [1, -1, 1], [0]*3, 1)
    assert list(legs.commission_sides) == [2, 2, 1]
    assert list(legs.exit_phase) == ["open", "open", "mark_open"]
    assert list(returns) == pytest.approx([1-104/105-3*C], abs=1e-12)


def masks(allowed):
    allowed = np.asarray(allowed, dtype=bool)
    boundary = np.zeros_like(allowed)
    boundary[:-1] = allowed[:-1] & ~allowed[1:]
    return DateExecution(allowed & ~boundary, boundary)


def engine(monkeypatch, trend, dates, opens, closes, mode="both", diagnostics=False, index=None):
    from supertrend_optimizer.core import backtest
    from supertrend_optimizer.core.trade_filter_config import build_trade_filter_config_from_raw
    from supertrend_optimizer.engine.run import run_single_backtest
    trend = np.asarray(trend)
    monkeypatch.setattr(backtest, "calculate_supertrend", lambda *args, **kw: (trend, trend))
    tf = build_trade_filter_config_from_raw({
        "enabled": True, "zigzag": {"enabled": False}, "date_filter": {"enabled": True}
    }) if dates is not None else None
    opens, closes = np.asarray(opens, dtype=float), np.asarray(closes, dtype=float)
    return run_single_backtest(
        open_prices=opens, high=np.maximum(opens, closes)+1, low=np.minimum(opens, closes)-1,
        close=closes, index=pd.date_range("2024-01-01", periods=len(trend)) if index is None else index,
        atr_period=2, multiplier=1, trade_mode=mode, trade_filter_config=tf,
        date_execution=dates, commission=C, early_exit_enabled=False,
        extract_trades_flag=True, collect_filter_diagnostics=diagnostics,
        min_trades_required=0, periods_per_year=252, warmup_period=0,
    )


@pytest.mark.parametrize("side", [-1, 1])
@pytest.mark.parametrize("decision,phase,price", [("reverse", "open", 102), ("hold", "close", 105)])
def test_engine_boundary(monkeypatch, side, decision, phase, price):
    trend = [side, -side if decision == "reverse" else side, side, side, side]
    outputs = []
    for future, diagnostics in [(90, False), (190, True)]:
        result = engine(monkeypatch, trend, masks([1, 1, 1, 0, 0]),
                        [100, 100, 102, future, 101], [100, 101, 105, future, 101],
                        diagnostics=diagnostics)
        assert list(result.positions) == [0, side, side if decision == "hold" else 0, 0, 0]
        trade, = result.trades_df.itertuples()
        assert (trade.entry_index, trade.exit_index, trade.exit_phase, trade.exit_price,
                trade.commission_sides) == (1, 2, phase, price, 2)
        assert trade.exit_reason == ("date_filter_close" if phase == "close" else "st_flip")
        assert trade.net_pnl_pct == pytest.approx(side * (price-100) - 200*C, abs=1e-12)
        outputs.append(result)
    np.testing.assert_array_equal(outputs[0].equity_curve, outputs[1].equity_curve)


def test_base_reentry_and_isolated_date(monkeypatch):
    flags = [0, 1, 0, 0, 1, 1, 1, 0]
    baseline = engine(monkeypatch, [1]*8, masks(flags), [100]*8, [100]*8)
    assert list(baseline.positions) == [0, 0, 0, 0, 1, 1, 1, 0]
    assert list(baseline.trades_df.exit_phase) == ["close"]


@pytest.mark.parametrize("side", [-1, 1])
@pytest.mark.parametrize("boundary,expected", [(3, "cancelled"), (4, "confirmed")])
def test_reversal_owner(side, boundary, expected):
    from supertrend_optimizer.core.reversal_confirmation_filter import apply
    allowed = [1]*(boundary+1) + [0]*(7-boundary)
    result = apply(trend=side*np.array([1, 1, -1, -1, -1, -1, -1, -1]),
                   trade_mode="revers", confirm_bars=1, date_execution=masks(allowed),
                   index=pd.date_range("2024-01-01", periods=8))
    attempt, = result.attempts
    assert attempt.confirmation_status == expected
    assert list(result.positions[boundary+1:]) == [0]*(7-boundary)
    if expected == "confirmed":
        assert result.positions[boundary] == 0
        assert result.execution_metadata["reversal_confirmation_confirmed_open"][boundary] == 0
    else:
        assert attempt.confirmation_cancel_reason == "date_filter_close"


def test_wakeup_rejected_entry_preserves_cycle():
    from wf_grid.tests.test_wakeup_mode_d_entry import _cfg, _per_bar, _stats, apply
    result = apply(trend=np.ones(6, dtype=int), trade_mode="both",
                   trade_filter_config=_cfg(max_trades_enabled=True, max_trades=1),
                   zigzag_global_stats=_stats(), per_bar=_per_bar(t=1),
                   date_execution=masks([1, 1, 0, 0, 1, 1]))
    np.testing.assert_array_equal(result.positions, 0)
    diag = result.filter_diagnostics
    assert diag["wakeup_regime_active"][1] == 1
    assert diag["wakeup_cycle_trade_count"][1] == 0
    assert not diag["wakeup_exit_cycle_trade_limit_triggered"].any()


def test_all_dates_control_and_oos_fee(monkeypatch):
    prices = [100, 100, 101, 99, 103, 104]
    trend = [0, 1, 1, -1, -1, -1]
    old = engine(monkeypatch, trend, None, prices, prices)
    active = engine(monkeypatch, trend, masks([1]*6), prices, prices)
    pd.testing.assert_frame_equal(old.trades_df, active.trades_df[old.trades_df.columns],
                                  check_dtype=False)
    np.testing.assert_allclose(old.returns, active.returns, atol=1e-12, rtol=0)
    np.testing.assert_allclose(old.equity_curve, active.equity_curve, atol=1e-12, rtol=0)
    # OOS starts at index 2. The legacy entry fee sits in prepend slot 1.
    r, _ = project_window(active.trades_df, np.array(prices), np.array(prices), C, 2)
    np.testing.assert_allclose(r - old.returns[2:], [-C, 0, 0], atol=1e-12, rtol=0)


@pytest.mark.parametrize("case", ["single_entry", "single_flat", "all_forbidden",
                                  "profit", "breakeven", "below_min", "carry"])
def test_wf_window_metrics(monkeypatch, case):
    from supertrend_optimizer.core import backtest
    from supertrend_optimizer.core.trade_filter_config import build_trade_filter_config_from_raw
    from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE, MAX_VALID_METRIC
    from supertrend_optimizer.utils.time_utils import WFWindowSlice
    from wf_grid.status.status_model import assign_step_status
    from wf_grid.tests.test_w8_wf_volume_integration import _cfg, _grid_point
    from wf_grid.wf.step_executor import execute_oos_step
    cfg = _cfg()
    cfg.status.min_meaningful_bars = 2
    cfg.backtest.min_trades_required = 2 if case == "below_min" else 0
    cfg.backtest.commission = C if case == "single_entry" else 0
    cfg.trade_filter = build_trade_filter_config_from_raw({
        "enabled": True, "zigzag": {"enabled": False}, "date_filter": {"enabled": True}
    })
    single = case.startswith("single")
    start, end = 5, (6 if single else 12)
    prepend = 0 if case == "single_flat" else 5
    dates = masks(([0]*13) if case == "all_forbidden" else ([1]*end + [0]*(13-end)))
    if single:
        dates = masks([1]*13)
    trend = np.ones(end, dtype=int)
    if case != "carry":
        trend[:4] = 0
    monkeypatch.setattr(backtest, "calculate_supertrend", lambda *args, **kw: (trend, trend))
    opens = np.full(13, 100.)
    close = opens.copy()
    close[end-1] = 100 if case == "breakeven" else 105
    step = execute_oos_step(
        _grid_point(), WFWindowSlice(0, 0, start, start, end),
        opens, np.maximum(opens, close)+1, opens-1, close,
        pd.date_range("2024-01-01", periods=13), cfg, prepend, date_execution=dates)
    m = step.metrics
    status = assign_step_status(m, step.effective_oos_bars, cfg).value
    if single:
        assert (status, step.effective_oos_bars) == ("insufficient_bars", 0)
        assert m["max_drawdown"] == INVALID_METRIC_VALUE
        if case == "single_entry":
            row, = step.oos_trades_df.itertuples()
            assert (row.entry_index, row.exit_index, row.exit_phase, row.commission_sides,
                    row.net_pnl_pct) == (0, 0, "mark_open", 1, -0.00667)
        else:
            assert step.oos_trades_df.empty
    elif case in {"all_forbidden", "carry"}:
        assert status == "no_trades"
        assert (m["num_trades"], m["sum_pnl_pct"], m["win_rate"]) == (0, 0, 0)
        assert m["profit_factor"] == m["avg_trade"] == INVALID_METRIC_VALUE
        assert m["max_drawdown"] == 0
    else:
        assert m["num_trades"] == 1
        pnl = 0 if case == "breakeven" else 5
        assert (m["win_rate"], m["sum_pnl_pct"], m["avg_trade"]) == (100 if pnl else 0, pnl, pnl)
        assert m["profit_factor"] == (MAX_VALID_METRIC if pnl else INVALID_METRIC_VALUE)
        assert m["max_drawdown"] == 0
        if case == "below_min":
            assert m["sharpe"] == INVALID_METRIC_VALUE


def test_time_only_waits_for_event_after_date_close():
    from supertrend_optimizer.core._reset_events import TimeWindowSchedule, TimeFilterRuntime
    from supertrend_optimizer.core.time_only_filter import apply
    flags = [1, 1, 1, 0, 0, 1, 1, 1]
    n = len(flags)
    runtime = TimeFilterRuntime(n, TimeWindowSchedule(((0, 1440),)),
                                np.ones(n, dtype=bool), np.zeros(n, dtype=bool))
    result = apply(trend=np.array([1]*6+[-1]*2), trade_mode="both",
                   date_execution=masks(flags), time_filter_runtime=runtime)
    assert list(result.positions) == [0, 1, 1, 0, 0, 0, 0, -1]


@pytest.mark.parametrize("reset,expected_phase,reason", [
    (1, "open", "filter_time_reset"), (2, "close", "date_filter_close"),
])
def test_time_reset_reason_with_both_diagnostic_settings(monkeypatch, reset, expected_phase, reason):
    from supertrend_optimizer.core import backtest
    from supertrend_optimizer.core._reset_events import TimeFilterRuntime, TimeWindowSchedule
    from supertrend_optimizer.core.trade_filter_config import build_trade_filter_config_from_raw
    from supertrend_optimizer.engine.run import run_single_backtest
    n = 5
    trend = np.ones(n, dtype=int)
    monkeypatch.setattr(backtest, "calculate_supertrend", lambda *args, **kw: (trend, trend))
    tf = build_trade_filter_config_from_raw({
        "enabled": True, "zigzag": {"enabled": False}, "time_filter": {"enabled": True},
        "date_filter": {"enabled": True},
    })
    tf.time_filter._windows_minutes = ((0, 1440),)
    runtime = TimeFilterRuntime(n, TimeWindowSchedule(((0, 1440),)),
                                np.ones(n, dtype=bool), np.arange(n) == reset)
    prices = np.full(n, 100.)
    frames = []
    for collect in (False, True):
        result = run_single_backtest(
            open_prices=prices, high=prices+1, low=prices-1, close=prices,
            index=pd.date_range("2024-01-01", periods=n), atr_period=2, multiplier=1,
            trade_mode="both", commission=C, warmup_period=0, periods_per_year=252,
            min_trades_required=0, early_exit_enabled=False, extract_trades_flag=True,
            trade_filter_config=tf, date_execution=masks([1, 1, 1, 0, 0]),
            time_filter_runtime=runtime, collect_filter_diagnostics=collect)
        row, = result.trades_df.itertuples()
        assert (row.exit_index, row.exit_phase, row.exit_reason, row.commission_sides) == (
            2, expected_phase, reason, 2)
        frames.append(result.trades_df)
    pd.testing.assert_frame_equal(*frames)


def test_change_future_date_changes_boundary(monkeypatch, tmp_path):
    from wf_grid.wf.trade_calendar import load_trade_calendar
    source = tmp_path / "dates.csv"
    source.write_text("<DATE>\n20240301\n20240304")
    prices = [100, 100, 102, 90, 91]
    def run(next_bar):
        index = pd.to_datetime(["2024-03-01 10:00", "2024-03-01 11:00", "2024-03-04 10:00",
                                next_bar, "2024-03-06 10:00"])
        calendar = load_trade_calendar(str(source), index)
        return engine(monkeypatch, [1]*5, DateExecution(calendar.entry, calendar.boundary),
                      prices, [100, 100, 105, 90, 91], index=index)
    old = run("2024-03-05 10:00")
    new = run("2024-03-04 11:00")
    assert (old.trades_df.exit_index.item(), new.trades_df.exit_index.item()) == (2, 3)
    assert (old.trades_df.exit_price.item(), new.trades_df.exit_price.item()) == (105, 90)


def test_oos_risk_with_idle_slots_and_entry_on_first_open(monkeypatch):
    from supertrend_optimizer.core import backtest
    from supertrend_optimizer.core.trade_filter_config import build_trade_filter_config_from_raw
    from supertrend_optimizer.utils.time_utils import WFWindowSlice
    from wf_grid.tests.test_w8_wf_volume_integration import _cfg, _grid_point
    from wf_grid.wf.step_executor import execute_oos_step
    cfg = _cfg()
    cfg.backtest.commission = C
    cfg.resolved_periods_per_year = 12.
    cfg.trade_filter = build_trade_filter_config_from_raw({
        "enabled": True, "zigzag": {"enabled": False}, "date_filter": {"enabled": True}
    })
    signal = np.array([0]*4 + [1]*7)
    monkeypatch.setattr(backtest, "calculate_supertrend", lambda *args, **kw: (signal, signal))
    opens = np.array([100.]*6+[102., 101., 100., 90., 150.])
    closes = opens.copy()
    closes[8] = 103.
    step = execute_oos_step(
        _grid_point(), WFWindowSlice(0, 0, 5, 5, 11), opens,
        np.maximum(opens, closes)+1, opens-1, closes,
        pd.date_range("2024-01-01", periods=11), cfg, 5, date_execution=masks([1]*9+[0]*2))
    returns = np.array([.02-C, 101/102-1, 100/101-1, .03-C, 0.])
    equity = np.r_[1., np.cumprod(1+returns)]
    m = step.metrics
    expected = {
        "sharpe": returns.mean()/returns.std(ddof=1)*np.sqrt(12),
        "sortino": returns.mean()/np.sqrt(np.mean(np.minimum(returns, 0)**2))*np.sqrt(12),
        "max_drawdown": min(equity/np.maximum.accumulate(equity)-1),
        "cagr": equity[-1]**(12/5)-1,
        "sum_pnl_pct": 3-200*C,
    }
    for key, value in expected.items():
        assert m[key] == pytest.approx(value, abs=1e-12, rel=0)
