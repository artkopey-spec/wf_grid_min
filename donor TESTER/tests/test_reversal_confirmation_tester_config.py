from __future__ import annotations

import pytest

from supertrend_optimizer.cli.tester import load_tester_config
from supertrend_optimizer.core.trade_filter_config import (
    resolve_trade_filter_mode,
)
from supertrend_optimizer.utils.exceptions import ConfigError


def _raw(*, confirm_bars=2, segmentation="legacy"):
    return {
        "segmentation": {"mode": segmentation, "n_parts": 5},
        "trade_filter": {
            "enabled": True,
            "zigzag": {"enabled": False},
            "volume": {"enabled": False},
            "time_filter": {"enabled": False},
            "reversal_confirmation": {
                "enabled": True,
                "confirm_bars": confirm_bars,
            },
        },
    }


def test_tester_yaml_builds_validated_reversal_config() -> None:
    loaded = load_tester_config("memory.yml", loaded_raw=_raw())
    config = loaded["trade_filter"]
    assert resolve_trade_filter_mode(config) == "reversal_only"
    assert config._reversal_validated_for == "tester"
    assert config.reversal_confirmation.confirm_bars == 2


def test_tester_yaml_rejects_equal_blocks() -> None:
    with pytest.raises(
        ConfigError,
        match="reversal_confirmation_equal_blocks_unsupported",
    ):
        load_tester_config(
            "memory.yml",
            loaded_raw=_raw(segmentation="equal_blocks"),
        )


@pytest.mark.parametrize("block", [1, "yes", []])
def test_non_mapping_reversal_block_is_rejected(block) -> None:
    raw = _raw()
    raw["trade_filter"]["reversal_confirmation"] = block
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_tester_config("memory.yml", loaded_raw=raw)


@pytest.mark.parametrize(
    ("wakeup", "expected_message"),
    [
        (
            {"enabled": True},
            "cannot be combined with wakeup_regime",
        ),
        (
            {
                "enabled": False,
                "position_freeze": {"enabled": True},
            },
            "cannot be combined with position_freeze",
        ),
    ],
)
def test_tester_yaml_rejects_reversal_with_wakeup_capabilities(
    wakeup,
    expected_message,
) -> None:
    raw = _raw()
    raw["trade_filter"]["wakeup_regime"] = wakeup
    with pytest.raises(ConfigError, match=expected_message):
        load_tester_config("memory.yml", loaded_raw=raw)
