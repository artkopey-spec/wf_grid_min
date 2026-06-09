from supertrend_optimizer.core import trade_filter_config as tfc
from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterWakeupPositionFreezeConfig,
    TradeFilterWakeupRegimeConfig,
    collect_raw_user_keys,
    collect_trade_filter_unknown_keys,
    build_trade_filter_config_from_raw,
    validate_trade_filter,
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
            "position_freeze": {
                "enabled": True,
                "min_hold_bars": 3,
                "apply_to": "internal_opposite_st_flip",
                "release_action": "apply_if_still_opposite",
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
    assert isinstance(wakeup.position_freeze, TradeFilterWakeupPositionFreezeConfig)
    assert wakeup.position_freeze.enabled is True
    assert wakeup.position_freeze.min_hold_bars == 3
    assert wakeup.position_freeze.apply_to == "internal_opposite_st_flip"
    assert wakeup.position_freeze.release_action == "apply_if_still_opposite"


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
    assert wakeup.position_freeze.enabled is False
    assert wakeup.position_freeze.min_hold_bars is None


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
        "TradeFilterWakeupPositionFreezeConfig",
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
    raw["wakeup_regime"]["position_freeze"]["surprise"] = True

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
        "unknown config key: "
        "'trade_filter.wakeup_regime.position_freeze.surprise'",
    }


def _valid_mode_d_raw_with_position_freeze() -> dict:
    raw = _raw_with_wakeup_regime()
    raw["zigzag"] = {
        "mode": "D",
        "reversal_threshold": 0.005,
        "candidate_trigger_threshold": 0.012,
        "local_window": 5,
    }
    raw["lifecycle"] = {
        "freeze_confirmed_legs": 0,
        "stop_check": "confirm_bar_only",
        "stopping_exit": "opposite_st_flip",
        "exit_off_mode": "exit C",
    }
    return raw


def _validation_errors(tf_raw: dict, caller: str = "tester") -> tuple[list[str], list[str]]:
    errors: list[str] = []
    keys: list[str] = []
    validate_trade_filter(
        build_trade_filter_config_from_raw(tf_raw),
        errors,
        collect_raw_user_keys({"trade_filter": tf_raw}),
        caller_pipeline=caller,
        error_keys=keys,
    )
    return errors, keys


def test_position_freeze_valid_mode_d_config_passes_validation():
    raw = _valid_mode_d_raw_with_position_freeze()
    errors, keys = _validation_errors(raw)

    assert errors == []
    assert keys == []


def test_position_freeze_enabled_requires_valid_min_hold_and_literals():
    raw = _valid_mode_d_raw_with_position_freeze()
    raw["wakeup_regime"]["position_freeze"].update({
        "min_hold_bars": 0,
        "apply_to": "other",
        "release_action": "other",
    })

    _errors, keys = _validation_errors(raw)

    assert {
        "position_freeze_min_hold_bars_invalid",
        "position_freeze_apply_to_invalid",
        "position_freeze_release_action_invalid",
    } <= set(keys)


def test_position_freeze_enabled_requires_wakeup_enabled_and_mode_d():
    raw = _valid_mode_d_raw_with_position_freeze()
    raw["wakeup_regime"]["enabled"] = False
    raw["zigzag"]["mode"] = "A"
    raw["lifecycle"]["exit_off_mode"] = "exit A"

    _errors, keys = _validation_errors(raw)

    assert "position_freeze_enabled_requires_wakeup_enabled" in keys
    assert "position_freeze_enabled_requires_mode_d" in keys


def test_position_freeze_false_is_harmless_inside_valid_wakeup_config():
    raw = _valid_mode_d_raw_with_position_freeze()
    raw["wakeup_regime"]["position_freeze"] = {"enabled": False}

    errors, keys = _validation_errors(raw)

    assert errors == []
    assert keys == []


def test_position_freeze_scalar_true_fails_validation_without_builder_crash():
    raw = _valid_mode_d_raw_with_position_freeze()
    raw["wakeup_regime"]["position_freeze"] = True

    _errors, keys = _validation_errors(raw)

    assert {
        "position_freeze_min_hold_bars_invalid",
        "position_freeze_apply_to_invalid",
        "position_freeze_release_action_invalid",
    } <= set(keys)


def test_position_freeze_scalar_non_bool_rejects_enabled_type():
    raw = _valid_mode_d_raw_with_position_freeze()
    raw["wakeup_regime"]["position_freeze"] = "yes"

    _errors, keys = _validation_errors(raw)

    assert "position_freeze_enabled_invalid_type" in keys
