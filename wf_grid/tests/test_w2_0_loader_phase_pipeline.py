from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

from supertrend_optimizer.core.trade_filter_config import (
    resolve_volume_defaults_in_place,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
)


def _write_wf_grid_config(tmp_path: Path) -> Path:
    path = tmp_path / "wf_grid_config.yaml"
    path.write_text(
        dedent(
            """\
            data:
              file_path: data.csv
            validation:
              walk_forward:
                train_size: "90D"
                test_size: "30D"
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                reversal_threshold: 0.005
                candidate_trigger_threshold: 0.012
                local_window: 5
              triggers:
                candidate_threshold:
                  enabled: true
                confirmed_median:
                  enabled: true
              lifecycle:
                freeze_confirmed_legs: 3
                stop_check: confirm_bar_only
                stopping_exit: opposite_st_flip
            """
        ),
        encoding="utf-8",
    )
    return path


def _write_tester_config(tmp_path: Path) -> Path:
    path = tmp_path / "config_tester.yaml"
    path.write_text(
        dedent(
            """\
            supertrend:
              atr_period: 18
              multiplier: 1.5
            trade_mode: long
            commission: 0.0003
            warmup_period_auto: true
            periods_per_year: auto
            market: stocks
            segmentation:
              mode: legacy
              n_parts: 5
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                global_stats_source: full_dataset
                leg_height_mode: pct
                reversal_threshold: 0.005
                candidate_trigger_threshold: 0.012
                global_median: auto
                local_window: 5
              triggers:
                candidate_threshold:
                  enabled: true
                confirmed_median:
                  enabled: true
              lifecycle:
                freeze_confirmed_legs: 5
                stop_check: confirm_bar_only
                stopping_exit: opposite_st_flip
              diagnostics:
                export_state_columns: true
                export_trigger_columns: true
            """
        ),
        encoding="utf-8",
    )
    return path


def test_wf_grid_loader_phase_order(monkeypatch, tmp_path):
    import wf_grid.config.loader as loader

    calls: list[str] = []

    def wrap(name):
        original = getattr(loader, name)

        def _wrapped(*args, **kwargs):
            calls.append(name)
            return original(*args, **kwargs)

        return _wrapped

    phase_names = [
        "_read_yaml",
        "_validate_strict_schema",
        "_validate_trade_filter_unknown_keys",
        "_collect_raw_keys",
        "_build_config",
        "_resolve_zigzag_enabled_in_place",
        "_resolve_volume_enabled_in_place",
        "_validate_config",
        "_resolve_trade_filter_mode_in_place",
        "_resolve_exit_off_mode_in_place",
        "_resolve_exit_b_immediate_off_in_place",
        "_resolve_time_filter_in_place",
        "_resolve_volume_defaults_in_place",
    ]
    for name in phase_names:
        monkeypatch.setattr(loader, name, wrap(name))

    cfg = loader.load_grid_config(str(_write_wf_grid_config(tmp_path)))

    assert cfg.trade_filter is not None
    assert cfg.trade_filter.zigzag.mode == "A+B"
    assert calls == phase_names


def test_tester_trade_filter_phase_order(monkeypatch, tmp_path):
    import supertrend_optimizer.cli.tester as tester

    calls: list[str] = []

    def wrap(name):
        original = getattr(tester, name)

        def _wrapped(*args, **kwargs):
            calls.append(name)
            return original(*args, **kwargs)

        return _wrapped

    phase_names = [
        "collect_trade_filter_unknown_keys",
        "collect_raw_user_keys",
        "build_trade_filter_config_from_raw",
        "resolve_zigzag_enabled_in_place",
        "resolve_volume_enabled_in_place",
        "validate_trade_filter",
        "resolve_trade_filter_mode_in_place",
        "resolve_exit_off_mode_in_place",
        "resolve_exit_b_immediate_off_in_place",
        "resolve_time_filter_in_place",
        "resolve_volume_defaults_in_place",
    ]
    for name in phase_names:
        monkeypatch.setattr(tester, name, wrap(name))

    cfg = tester.load_tester_config(str(_write_tester_config(tmp_path)))

    assert cfg["trade_filter"] is not None
    assert cfg["trade_filter"].zigzag.mode == "A+B"
    assert calls == phase_names


def test_phase3_resolvers_preserve_malformed_values_for_validation() -> None:
    tf = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(enabled="yes"),
        volume=SimpleNamespace(enabled="yes"),
    )
    raw_user_keys = frozenset({
        ("trade_filter", "enabled"),
        ("trade_filter", "zigzag", "enabled"),
        ("trade_filter", "volume", "enabled"),
    })

    resolve_zigzag_enabled_in_place(tf, raw_user_keys)
    resolve_volume_enabled_in_place(tf, raw_user_keys)
    resolve_volume_defaults_in_place(tf, raw_user_keys)

    assert tf.zigzag.enabled == "yes"
    assert tf.volume.enabled == "yes"
