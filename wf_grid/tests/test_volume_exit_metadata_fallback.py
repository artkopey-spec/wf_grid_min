from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from wf_grid.baseline import compute_pipeline_fingerprint
from wf_grid.export.xlsx_writer import _config_to_dict


@dataclass
class _Config:
    trade_filter: Any


@dataclass
class _Result:
    config: Any


def _old_style_config() -> _Config:
    volume = SimpleNamespace(
        enabled=True,
        mode="volume_A",
        aggregation="median",
        daily_reset=False,
        short_window=30,
        baseline_window=1000,
        baseline_session=SimpleNamespace(enabled=False, window=None),
        threshold_ratio=2.2,
        regime_low_ratio=0.8,
        regime_high_ratio=1.2,
        direction_lookback_bars=10,
    )
    return _Config(
        trade_filter=SimpleNamespace(
            enabled=True,
            volume=volume,
        )
    )


def test_xlsx_config_snapshot_falls_back_for_old_volume_config_objects():
    config_dict = _config_to_dict(_old_style_config())

    snapshot = config_dict["filter_config_snapshot"]
    assert snapshot["volume_threshold_ratio"] == 2.2
    assert snapshot["volume_exit_hysteresis_ratio"] == 2.2
    assert snapshot["volume_exit_freeze_bars"] == 0


def test_fingerprint_metadata_falls_back_for_old_volume_config_objects():
    fp = compute_pipeline_fingerprint(_Result(config=_old_style_config()))

    assert fp.metadata["volume_threshold_ratio"] == 2.2
    assert fp.metadata["volume_exit_hysteresis_ratio"] == 2.2
    assert fp.metadata["volume_exit_freeze_bars"] == 0
