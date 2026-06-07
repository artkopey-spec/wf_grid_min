from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.data.validator import validate_volume_filter_data
from supertrend_optimizer.utils.exceptions import DataValidationError
from wf_grid.pipeline.orchestrator import run_grid_pipeline


def _df(volume=None) -> pd.DataFrame:
    data = {
        "open": [10.0, 11.0, 12.0, 13.0],
        "high": [11.0, 12.0, 13.0, 14.0],
        "low": [9.0, 10.0, 11.0, 12.0],
        "close": [10.5, 11.5, 12.5, 13.5],
    }
    if volume is not None:
        data["volume"] = volume
    return pd.DataFrame(data, index=pd.date_range("2026-01-01", periods=4, freq="D"))


def _volume_enabled_cfg():
    return SimpleNamespace(
        enabled=True,
        volume=SimpleNamespace(enabled=True),
    )


def _volume_disabled_cfg():
    return SimpleNamespace(
        enabled=True,
        volume=SimpleNamespace(enabled=False),
    )


def _wakeup_volume_cfg(*, enabled: bool = True):
    return SimpleNamespace(
        enabled=True,
        wakeup_regime=SimpleNamespace(
            enabled=True,
            entry=SimpleNamespace(
                volume_expansion=SimpleNamespace(enabled=enabled),
            ),
        ),
    )


def _mode_d_wakeup_volume_cfg():
    return SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(enabled=True, mode="D"),
        lifecycle=SimpleNamespace(exit_off_mode="exit C"),
        wakeup_regime=SimpleNamespace(
            enabled=True,
            entry=SimpleNamespace(
                volume_expansion=SimpleNamespace(enabled=True),
            ),
        ),
    )


def test_disabled_volume_filter_is_noop_without_volume_column():
    df = _df()

    result = validate_volume_filter_data(df, _volume_disabled_cfg())

    assert result is df


def test_enabled_volume_filter_accepts_non_negative_numeric_volume_without_mutation():
    df = _df(volume=np.array([0, 10, 20, 30], dtype=np.int64))
    before = df.copy(deep=True)

    result = validate_volume_filter_data(df, _volume_enabled_cfg())

    assert result is df
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize(
    ("volume", "expected"),
    [
        (None, "requires a 'volume' column"),
        (["1", "2", "3", "4"], "must be numeric"),
        ([1.0, np.nan, 3.0, 4.0], "contains NaN"),
        ([1.0, np.inf, 3.0, 4.0], "contains inf"),
        ([1.0, -1.0, 3.0, 4.0], "contains negative"),
    ],
)
def test_enabled_volume_filter_rejects_bad_volume_data(volume, expected):
    df = _df(volume=volume) if volume is not None else _df()

    with pytest.raises(DataValidationError, match="trade_filter\\.volume") as exc:
        validate_volume_filter_data(df, _volume_enabled_cfg())

    assert expected in str(exc.value)


def test_enabled_wakeup_volume_filter_rejects_missing_volume_column():
    df = _df()

    with pytest.raises(
        DataValidationError,
        match="trade_filter\\.wakeup_regime\\.entry\\.volume_expansion",
    ) as exc:
        validate_volume_filter_data(df, _wakeup_volume_cfg())

    assert "requires a 'volume' column" in str(exc.value)


def test_mode_d_wakeup_volume_expansion_rejects_missing_volume_column():
    df = _df()

    with pytest.raises(
        DataValidationError,
        match="trade_filter\\.wakeup_regime\\.entry\\.volume_expansion",
    ) as exc:
        validate_volume_filter_data(df, _mode_d_wakeup_volume_cfg())

    assert "requires a 'volume' column" in str(exc.value)


def test_disabled_wakeup_volume_filter_is_noop_without_volume_column():
    df = _df()

    result = validate_volume_filter_data(df, _wakeup_volume_cfg(enabled=False))

    assert result is df


@pytest.mark.parametrize(
    ("volume", "expected"),
    [
        ([1.0, np.nan, 3.0, 4.0], "contains NaN"),
        ([1.0, np.inf, 3.0, 4.0], "contains inf"),
        ([1.0, -1.0, 3.0, 4.0], "contains negative"),
    ],
)
def test_enabled_wakeup_volume_filter_rejects_bad_volume_values(volume, expected):
    df = _df(volume=volume)

    with pytest.raises(
        DataValidationError,
        match="trade_filter\\.wakeup_regime\\.entry\\.volume_expansion",
    ) as exc:
        validate_volume_filter_data(df, _wakeup_volume_cfg())

    assert expected in str(exc.value)


def _write_volume_config(path, csv_path) -> None:
    path.write_text(
        f"""\
data:
  file_path: {csv_path}
validation:
  walk_forward:
    train_size: "3D"
    test_size: "1D"
trade_filter:
  enabled: true
  zigzag:
    enabled: false
  volume:
    enabled: true
    mode: volume_A
    short_window: 2
    baseline_window: 3
    threshold_ratio: 1.1
""",
        encoding="utf-8",
    )


def _write_tester_config(path) -> None:
    path.write_text(
        """\
supertrend:
  atr_period: 5
  multiplier: 2.0
trade_mode: revers
segmentation:
  mode: legacy
trade_filter:
  enabled: true
  zigzag:
    enabled: false
  volume:
    enabled: true
    mode: volume_A
    short_window: 2
    baseline_window: 3
    threshold_ratio: 1.1
""",
        encoding="utf-8",
    )


def _write_csv(path, *, include_volume: bool) -> None:
    df = _df(volume=[10, 20, 30, 40] if include_volume else None)
    out = df.reset_index(names="datetime")
    out.to_csv(path, index=False)


def test_wf_grid_pipeline_validates_volume_data_after_ohlc(tmp_path):
    csv_path = tmp_path / "data.csv"
    cfg_path = tmp_path / "config.yaml"
    _write_csv(csv_path, include_volume=False)
    _write_volume_config(cfg_path, csv_path)

    result = run_grid_pipeline(str(cfg_path))

    assert result.error is not None
    assert "trade_filter.volume" in result.error


def test_active_tester_cli_validates_volume_data_after_ohlc(tmp_path):
    from supertrend_optimizer.cli.tester import run_backtest

    csv_path = tmp_path / "data.csv"
    cfg_path = tmp_path / "tester.yaml"
    out_path = tmp_path / "out.xlsx"
    _write_csv(csv_path, include_volume=False)
    _write_tester_config(cfg_path)

    args = Namespace(
        csv=str(csv_path),
        out=str(out_path),
        config=str(cfg_path),
        atr=None,
        mult=None,
        mode=None,
        periods_per_year=None,
        annualization_basis=None,
        market=None,
        execution_model=None,
    )

    with pytest.raises(DataValidationError, match="trade_filter\\.volume"):
        run_backtest(args)
