from supertrend_optimizer.core import trade_filter_config as tfc
from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterWakeupRegimeConfig,
    collect_trade_filter_unknown_keys,
    build_trade_filter_config_from_raw,
)


def _raw_with_wakeup_regime():
    return {
        "enabled": True,
        "type": "zigzag_st_mode",
        "wakeup_regime": {
            "enabled": True,
            "entry": {
                "candidate_height": {"enabled": True, "quantile": 0.65},
                "candidate_age": {"enabled": True, "max_bars": 10},
                "atr_expansion": {
                    "enabled": True,
                    "short_window": 5,
                    "long_window": 60,
                    "min_ratio": 1.3,
                },
                "volume_expansion": {
                    "enabled": True,
                    "short_window": 5,
                    "baseline_window": 60,
                    "min_ratio": 1.3,
                },
            },
            "exit": {
                "ttl": {"enabled": True, "bars": 45},
                "no_fresh_candidate": {
                    "enabled": True,
                    "quantile": 0.60,
                    "max_age_bars": 15,
                    "timeout_bars": 20,
                },
                "action": {"mode": "block_new_entries"},
            },
        },
    }


def test_build_trade_filter_config_materializes_wakeup_regime_raw_values():
    cfg = build_trade_filter_config_from_raw(_raw_with_wakeup_regime())

    wakeup = cfg.wakeup_regime
    assert isinstance(wakeup, TradeFilterWakeupRegimeConfig)
    assert wakeup.enabled is True
    assert wakeup.entry.candidate_height.quantile == 0.65
    assert wakeup.entry.candidate_age.max_bars == 10
    assert wakeup.entry.atr_expansion.long_window == 60
    assert wakeup.entry.volume_expansion.baseline_window == 60
    assert wakeup.exit.ttl.bars == 45
    assert wakeup.exit.no_fresh_candidate.timeout_bars == 20
    assert wakeup.exit.action.mode == "block_new_entries"


def test_wakeup_regime_absent_stays_none_and_nested_defaults_are_structural():
    absent_cfg = build_trade_filter_config_from_raw({"enabled": False})
    assert absent_cfg.wakeup_regime is None

    present_cfg = build_trade_filter_config_from_raw({
        "enabled": False,
        "wakeup_regime": {},
    })

    wakeup = present_cfg.wakeup_regime
    assert isinstance(wakeup, TradeFilterWakeupRegimeConfig)
    assert wakeup.enabled is False
    assert wakeup.entry.candidate_height.enabled is False
    assert wakeup.entry.candidate_height.quantile is None
    assert wakeup.entry.candidate_age.max_bars is None
    assert wakeup.entry.atr_expansion.min_ratio is None
    assert wakeup.entry.volume_expansion.min_ratio is None
    assert wakeup.exit.ttl.bars is None
    assert wakeup.exit.no_fresh_candidate.quantile is None
    assert wakeup.exit.action.mode is None


def test_wakeup_dataclasses_are_exported():
    expected = {
        "TradeFilterWakeupRegimeConfig",
        "TradeFilterWakeupEntryConfig",
        "TradeFilterWakeupCandidateHeightConfig",
        "TradeFilterWakeupCandidateAgeConfig",
        "TradeFilterWakeupAtrExpansionConfig",
        "TradeFilterWakeupVolumeExpansionConfig",
        "TradeFilterWakeupExitConfig",
        "TradeFilterWakeupTtlExitConfig",
        "TradeFilterWakeupNoFreshCandidateExitConfig",
        "TradeFilterWakeupExitActionConfig",
    }

    assert expected <= set(tfc.__all__)


def test_shared_allowed_keys_accept_wakeup_regime_shape():
    errors = collect_trade_filter_unknown_keys(
        _raw_with_wakeup_regime(),
        "trade_filter",
    )

    assert errors == []


def test_shared_allowed_keys_reject_unknown_wakeup_subkeys():
    raw = _raw_with_wakeup_regime()
    raw["wakeup_regime"]["surprise"] = True
    raw["wakeup_regime"]["entry"]["candidate_height"]["surprise"] = True
    raw["wakeup_regime"]["entry"]["candidate_age"]["surprise"] = True
    raw["wakeup_regime"]["entry"]["atr_expansion"]["surprise"] = True
    raw["wakeup_regime"]["entry"]["volume_expansion"]["surprise"] = True
    raw["wakeup_regime"]["exit"]["ttl"]["surprise"] = True
    raw["wakeup_regime"]["exit"]["no_fresh_candidate"]["surprise"] = True
    raw["wakeup_regime"]["exit"]["action"]["surprise"] = True

    errors = collect_trade_filter_unknown_keys(raw, "trade_filter")

    assert set(errors) == {
        "unknown config key: 'trade_filter.wakeup_regime.surprise'",
        "unknown config key: "
        "'trade_filter.wakeup_regime.entry.candidate_height.surprise'",
        "unknown config key: "
        "'trade_filter.wakeup_regime.entry.candidate_age.surprise'",
        "unknown config key: "
        "'trade_filter.wakeup_regime.entry.atr_expansion.surprise'",
        "unknown config key: "
        "'trade_filter.wakeup_regime.entry.volume_expansion.surprise'",
        "unknown config key: 'trade_filter.wakeup_regime.exit.ttl.surprise'",
        "unknown config key: "
        "'trade_filter.wakeup_regime.exit.no_fresh_candidate.surprise'",
        "unknown config key: 'trade_filter.wakeup_regime.exit.action.surprise'",
    }
