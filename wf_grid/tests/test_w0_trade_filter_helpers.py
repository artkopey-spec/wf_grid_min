from types import SimpleNamespace

from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    is_trade_filter_enabled,
    is_volume_enabled,
    is_wakeup_volume_enabled,
    is_zigzag_enabled,
    resolve_zigzag_enabled_in_place,
)


def test_helpers_false_when_config_absent() -> None:
    assert is_trade_filter_enabled(None) is False
    assert is_zigzag_enabled(None) is False
    assert is_volume_enabled(None) is False
    assert is_wakeup_volume_enabled(None) is False


def test_helpers_false_when_root_disabled() -> None:
    tf = TradeFilterConfig(enabled=False)

    assert is_trade_filter_enabled(tf) is False
    assert is_zigzag_enabled(tf) is False
    assert is_volume_enabled(tf) is False
    assert is_wakeup_volume_enabled(tf) is False


def test_helpers_do_not_coerce_malformed_raw_values() -> None:
    tf = SimpleNamespace(
        enabled="true",
        zigzag=SimpleNamespace(enabled="true"),
        volume=SimpleNamespace(enabled="true"),
        wakeup_regime=SimpleNamespace(
            enabled="true",
            entry=SimpleNamespace(
                volume_expansion=SimpleNamespace(enabled="true"),
            ),
        ),
    )

    assert is_trade_filter_enabled(tf) is False
    assert is_zigzag_enabled(tf) is False
    assert is_volume_enabled(tf) is False
    assert is_wakeup_volume_enabled(tf) is False


def test_legacy_zigzag_materialized_by_resolver_is_enabled() -> None:
    tf = TradeFilterConfig(enabled=True, type="zigzag_st_mode")
    resolve_zigzag_enabled_in_place(tf, frozenset({
        ("trade_filter", "enabled"),
        ("trade_filter", "type"),
    }))

    assert is_trade_filter_enabled(tf) is True
    assert is_zigzag_enabled(tf) is True


def test_missing_volume_block_is_disabled() -> None:
    tf = TradeFilterConfig(enabled=True)

    assert is_volume_enabled(tf) is False
    assert is_wakeup_volume_enabled(tf) is False


def test_explicit_subfilter_flags_are_strict() -> None:
    tf = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(enabled=False),
        volume=SimpleNamespace(enabled=True),
    )

    assert is_zigzag_enabled(tf) is False
    assert is_volume_enabled(tf) is True


def test_wakeup_volume_expansion_flag_is_strict() -> None:
    tf = SimpleNamespace(
        enabled=True,
        wakeup_regime=SimpleNamespace(
            enabled=True,
            entry=SimpleNamespace(
                volume_expansion=SimpleNamespace(enabled=True),
            ),
        ),
    )
    malformed = SimpleNamespace(
        enabled=True,
        wakeup_regime=SimpleNamespace(
            enabled=True,
            entry=SimpleNamespace(
                volume_expansion=SimpleNamespace(enabled="true"),
            ),
        ),
    )

    assert is_wakeup_volume_enabled(tf) is True
    assert is_wakeup_volume_enabled(malformed) is False
