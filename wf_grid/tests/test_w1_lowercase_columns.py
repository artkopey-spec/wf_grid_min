import logging
import textwrap
from pathlib import Path

import pandas as pd

from supertrend_optimizer.data.loader import load_ohlc_csv
from supertrend_optimizer.data.validator import validate_ohlc_data
from wf_grid.tests.test_parallel_execution import (
    _build_zigzag_fixture_dataset,
    _write_tiny_yaml,
)


def _write_csv(tmp_path: Path, df: pd.DataFrame, name: str = "data.csv") -> Path:
    path = tmp_path / name
    df.to_csv(path)
    return path


def _write_grid_yaml(tmp_path: Path, csv_path: Path) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            data:
              file_path: {csv_path.as_posix()}
            validation:
              walk_forward:
                train_size: "90D"
                test_size: "30D"
            """
        ),
        encoding="utf-8",
    )
    return str(path)


def _valid_df(columns: dict) -> pd.DataFrame:
    return pd.DataFrame(
        columns,
        index=pd.date_range("2020-01-01", periods=3, freq="1D"),
    )


def test_wf_grid_mixed_case_ohlcv_valid_after_lowercase(monkeypatch, tmp_path):
    import wf_grid.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "make_walk_forward_slices", lambda **kwargs: [])
    monkeypatch.setattr(orch, "compute_prepend_bars", lambda data, cfg: 0)
    monkeypatch.setattr(orch, "resolve_periods_per_year", lambda cfg, data: cfg)

    df = _valid_df({
        "Open": [1.0, 1.1, 1.2],
        "High": [1.2, 1.3, 1.4],
        "Low": [0.9, 1.0, 1.1],
        "Close": [1.1, 1.2, 1.3],
        "Volume": [100, 110, 120],
    })
    csv_path = _write_csv(tmp_path, df)
    result = orch.run_grid_pipeline(
        _write_grid_yaml(tmp_path, csv_path),
        output_path=str(tmp_path / "out.xlsx"),
        parallel_enabled=False,
    )

    assert result.error is not None
    assert "No walk-forward slices" in result.error


def test_wf_grid_volume_case_collision_raises_before_overwrite(tmp_path):
    from wf_grid.pipeline.orchestrator import run_grid_pipeline

    df = _valid_df({
        "open": [1.0, 1.1, 1.2],
        "high": [1.2, 1.3, 1.4],
        "low": [0.9, 1.0, 1.1],
        "close": [1.1, 1.2, 1.3],
        "Volume": [100, 110, 120],
        "volume": [200, 210, 220],
    })
    csv_path = _write_csv(tmp_path, df)
    result = run_grid_pipeline(
        _write_grid_yaml(tmp_path, csv_path),
        output_path=str(tmp_path / "out.xlsx"),
        parallel_enabled=False,
    )

    assert result.error is not None
    assert "duplicate lowercase columns" in result.error
    assert "Volume" in result.error
    assert "volume" in result.error


def test_tester_loader_logs_lowercase_collision_and_continues(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="supertrend_optimizer.data.validator")

    df = pd.DataFrame({
        "datetime": pd.date_range("2020-01-01", periods=3, freq="1D"),
        "open": [1.0, 1.1, 1.2],
        "high": [1.2, 1.3, 1.4],
        "low": [0.9, 1.0, 1.1],
        "close": [1.1, 1.2, 1.3],
        "Volume": [100, 110, 120],
        "volume": [200, 210, 220],
    })
    csv_path = tmp_path / "tester.csv"
    df.to_csv(csv_path, index=False)

    loaded = load_ohlc_csv(str(csv_path), sep=",")

    assert list(loaded[["open", "high", "low", "close"]].columns) == [
        "open",
        "high",
        "low",
        "close",
    ]
    assert "duplicate lowercase columns" in caplog.text


def test_ohlc_only_data_remains_valid_without_volume() -> None:
    df = _valid_df({
        "open": [1.0, 1.1, 1.2],
        "high": [1.2, 1.3, 1.4],
        "low": [0.9, 1.0, 1.1],
        "close": [1.1, 1.2, 1.3],
    })

    result = validate_ohlc_data(df, strict=True)

    assert list(result.columns) == ["open", "high", "low", "close"]


def test_zigzag_only_parity_survives_lowercase_normalization(monkeypatch, tmp_path):
    import wf_grid.pipeline.orchestrator as orch

    monkeypatch.setattr(
        orch,
        "export_workbook",
        lambda *args, output_path, **kwargs: Path(output_path),
    )

    lower_csv = _build_zigzag_fixture_dataset(tmp_path, n_bars=900)
    mixed_csv = tmp_path / "zigzag_fixture_mixed_case.csv"
    mixed_df = pd.read_csv(lower_csv, index_col=0)
    mixed_df = mixed_df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
        }
    )
    mixed_df.to_csv(mixed_csv)

    lower_cfg = _write_tiny_yaml(
        tmp_path,
        lower_csv,
        parallel_enabled=False,
        trade_filter_mode="enabled_fixture",
        filename="lowercase_zigzag.yaml",
    )
    mixed_cfg = _write_tiny_yaml(
        tmp_path,
        mixed_csv,
        parallel_enabled=False,
        trade_filter_mode="enabled_fixture",
        filename="mixed_case_zigzag.yaml",
    )

    lower_result = orch.run_grid_pipeline(
        str(lower_cfg),
        output_path=str(tmp_path / "lower.xlsx"),
        parallel_enabled=False,
    )
    mixed_result = orch.run_grid_pipeline(
        str(mixed_cfg),
        output_path=str(tmp_path / "mixed.xlsx"),
        parallel_enabled=False,
    )

    assert lower_result.error is None
    assert mixed_result.error is None
    assert lower_result.config.trade_filter.enabled is True
    assert mixed_result.config.trade_filter.enabled is True

    pd.testing.assert_frame_equal(
        lower_result.step_oos_long,
        mixed_result.step_oos_long,
        check_exact=True,
    )
    pd.testing.assert_frame_equal(
        lower_result.step_train_long,
        mixed_result.step_train_long,
        check_exact=True,
    )
    pd.testing.assert_frame_equal(
        lower_result.trades_oos,
        mixed_result.trades_oos,
        check_exact=True,
    )
    pd.testing.assert_frame_equal(
        lower_result.trades_train,
        mixed_result.trades_train,
        check_exact=True,
    )
