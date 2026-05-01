"""
WP-T2 — tester-side gate: ``load_tester_config`` parses + validates the
``trade_filter`` block via the shared validator from
``donor/supertrend_optimizer/core/trade_filter_config.py``.

These tests run the SAME validation surface that production tester would hit,
through the canonical Mode C runtime ``donor/supertrend_optimizer/cli/tester.py``
(packaging contract gated by ``test_wp_t2_packaging_smoke.py``).

Test groups (per owner approval list):
    1.  block absent
    2.  enabled=false + type=zigzag_st_mode
    3.  fully valid enabled config
    4.  unknown keys (top-level, zigzag, lifecycle, triggers child)
    5.  invalid reversal_threshold: None / 0 / 1 / "0.5%"
    6.  both triggers disabled when enabled=true
    7.  wrong lifecycle enum (stop_check, stopping_exit)
    8.  explicit numeric threshold + explicit quantile simultaneously
    9.  freeze_confirmed_legs < local_window MUST be accepted
    10. segmentation.mode=equal_blocks + trade_filter.enabled=true MUST reject
        (plan §5.5 fail-fast contract — WP-T5 gate brought forward into WP-T2)

Spec reference: Appendix A v1.1 §11–§11.3, §15.6, §17.2
Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §14 WP-T2 + §5.5
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from supertrend_optimizer.cli.tester import load_tester_config
from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
)
from supertrend_optimizer.utils.exceptions import ConfigError


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

# Minimum non-trade-filter scaffold so load_tester_config can finish parsing
# the rest of the file. Trade_filter behaviour is the only thing under test.
_BASE_YAML = dedent(
    """
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
    """
).strip()


def _write_config(tmp_path: Path, trade_filter_block: str | None) -> Path:
    """Write a config file containing the base scaffold plus optional
    ``trade_filter`` YAML fragment, return its path.

    ``trade_filter_block`` is a YAML string starting with the literal
    ``trade_filter:`` line (or ``None`` to omit the block entirely).
    """
    parts = [_BASE_YAML]
    if trade_filter_block is not None:
        parts.append(trade_filter_block)
    text = "\n".join(parts) + "\n"
    cfg_path = tmp_path / "config_tester.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    return cfg_path


def _enabled_minimal_block(**overrides: object) -> str:
    """Return a fully-valid ``enabled: true`` trade_filter block as YAML.

    Any keyword override is rendered as YAML scalar replacement of the matching
    line. This keeps tests readable without dragging a YAML library in.
    """
    base = dedent(
        """
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
    ).strip()
    text = base
    for key, value in overrides.items():
        text = _replace_scalar_line(text, key, value)
    return text


def _replace_scalar_line(yaml_text: str, key: str, value: object) -> str:
    """Replace ``  key: <old>`` with ``  key: <value>`` in a YAML block.

    Only operates on the FIRST occurrence of the key — sufficient for the
    flat block layout used in these tests; the same key never appears twice.
    """
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif value is None:
        rendered = "null"
    else:
        rendered = repr(value) if isinstance(value, str) else str(value)

    out_lines: list[str] = []
    replaced = False
    for line in yaml_text.splitlines():
        stripped = line.lstrip()
        if not replaced and stripped.startswith(f"{key}:"):
            indent = line[: len(line) - len(stripped)]
            out_lines.append(f"{indent}{key}: {rendered}")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        raise AssertionError(f"_replace_scalar_line: key {key!r} not found")
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Group 1 — block absent
# ---------------------------------------------------------------------------

class TestTradeFilterAbsent:
    """trade_filter block omitted from YAML => cfg["trade_filter"] is None."""

    def test_block_absent_yields_none(self, tmp_path: Path) -> None:
        cfg_path = _write_config(tmp_path, trade_filter_block=None)
        cfg = load_tester_config(str(cfg_path))
        assert cfg["trade_filter"] is None, (
            "Disabled-baseline contract: absent trade_filter block must "
            "leave cfg['trade_filter'] = None (Appendix A v1.1 §11.1)."
        )


# ---------------------------------------------------------------------------
# Group 2 — enabled=false (explicit disabled)
# ---------------------------------------------------------------------------

class TestExplicitlyDisabled:
    """enabled=false + type=zigzag_st_mode => accept, return TradeFilterConfig."""

    def test_disabled_minimal_accepted(self, tmp_path: Path) -> None:
        block = dedent(
            """
            trade_filter:
              enabled: false
              type: zigzag_st_mode
            """
        ).strip()
        cfg = load_tester_config(str(_write_config(tmp_path, block)))
        tf = cfg["trade_filter"]
        assert isinstance(tf, TradeFilterConfig)
        assert tf.enabled is False
        assert tf.type == "zigzag_st_mode"

    def test_disabled_without_type_accepted(self, tmp_path: Path) -> None:
        """§11.1: disabled filter — type is OPTIONAL when enabled=false."""
        block = dedent(
            """
            trade_filter:
              enabled: false
            """
        ).strip()
        cfg = load_tester_config(str(_write_config(tmp_path, block)))
        tf = cfg["trade_filter"]
        assert isinstance(tf, TradeFilterConfig)
        assert tf.enabled is False

    def test_disabled_with_unsupported_type_rejected(
        self, tmp_path: Path
    ) -> None:
        block = dedent(
            """
            trade_filter:
              enabled: false
              type: not_a_real_type
            """
        ).strip()
        with pytest.raises(ConfigError, match="not supported for disabled filter"):
            load_tester_config(str(_write_config(tmp_path, block)))


# ---------------------------------------------------------------------------
# Group 3 — fully valid enabled config
# ---------------------------------------------------------------------------

class TestValidEnabled:
    """Fully-specified enabled config materialises a TradeFilterConfig."""

    def test_full_enabled_accepted(self, tmp_path: Path) -> None:
        block = _enabled_minimal_block()
        cfg = load_tester_config(str(_write_config(tmp_path, block)))
        tf = cfg["trade_filter"]
        assert isinstance(tf, TradeFilterConfig)
        assert tf.enabled is True
        assert tf.type == "zigzag_st_mode"
        assert tf.zigzag.reversal_threshold == 0.005
        assert tf.zigzag.candidate_trigger_threshold == 0.012
        assert tf.zigzag.candidate_trigger_quantile is None
        assert tf.zigzag.local_window == 5
        assert tf.triggers.candidate_threshold.enabled is True
        assert tf.triggers.confirmed_median.enabled is True
        assert tf.lifecycle.freeze_confirmed_legs == 5
        assert tf.lifecycle.stop_check == "confirm_bar_only"
        assert tf.lifecycle.stopping_exit == "opposite_st_flip"

    def test_auto_threshold_with_quantile_accepted(
        self, tmp_path: Path
    ) -> None:
        """auto candidate_trigger_threshold REQUIRES candidate_trigger_quantile."""
        block = dedent(
            """
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                reversal_threshold: 0.005
                candidate_trigger_threshold: auto
                candidate_trigger_quantile: 0.80
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
            """
        ).strip()
        cfg = load_tester_config(str(_write_config(tmp_path, block)))
        tf = cfg["trade_filter"]
        assert isinstance(tf, TradeFilterConfig)
        assert tf.zigzag.candidate_trigger_threshold == "auto"
        assert tf.zigzag.candidate_trigger_quantile == 0.80


# ---------------------------------------------------------------------------
# Group 4 — unknown keys
# ---------------------------------------------------------------------------

class TestUnknownKeys:
    """Strict schema — any unknown key in trade_filter subtree must reject."""

    def test_unknown_config_root_key(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config_tester.yaml"
        cfg_path.write_text(_BASE_YAML + "\nseg_mentation: typo\n", encoding="utf-8")

        with pytest.raises(ConfigError, match="seg_mentation"):
            load_tester_config(str(cfg_path))

    def test_unknown_top_level_key(self, tmp_path: Path) -> None:
        block = dedent(
            """
            trade_filter:
              enabled: false
              type: zigzag_st_mode
              not_a_real_key: 42
            """
        ).strip()
        with pytest.raises(ConfigError, match="trade_filter.not_a_real_key"):
            load_tester_config(str(_write_config(tmp_path, block)))

    def test_unknown_zigzag_key(self, tmp_path: Path) -> None:
        block = dedent(
            """
            trade_filter:
              enabled: false
              zigzag:
                bogus_field: 1
            """
        ).strip()
        with pytest.raises(ConfigError, match="trade_filter.zigzag.bogus_field"):
            load_tester_config(str(_write_config(tmp_path, block)))

    def test_unknown_lifecycle_key(self, tmp_path: Path) -> None:
        block = dedent(
            """
            trade_filter:
              enabled: false
              lifecycle:
                some_extra: yes
            """
        ).strip()
        with pytest.raises(
            ConfigError, match="trade_filter.lifecycle.some_extra"
        ):
            load_tester_config(str(_write_config(tmp_path, block)))

    def test_unknown_trigger_child_key(self, tmp_path: Path) -> None:
        block = dedent(
            """
            trade_filter:
              enabled: false
              triggers:
                candidate_threshold:
                  enabled: true
                  rogue: 1
            """
        ).strip()
        with pytest.raises(
            ConfigError,
            match="trade_filter.triggers.candidate_threshold.rogue",
        ):
            load_tester_config(str(_write_config(tmp_path, block)))


# ---------------------------------------------------------------------------
# Group 5 — invalid reversal_threshold
# ---------------------------------------------------------------------------

class TestReversalThreshold:
    """reversal_threshold must be a numeric fraction in (0, 1) (§15.6)."""

    def test_reject_none(self, tmp_path: Path) -> None:
        block = _enabled_minimal_block(reversal_threshold=None)
        with pytest.raises(
            ConfigError, match="reversal_threshold is required"
        ):
            load_tester_config(str(_write_config(tmp_path, block)))

    def test_reject_zero(self, tmp_path: Path) -> None:
        block = _enabled_minimal_block(reversal_threshold=0)
        with pytest.raises(ConfigError, match="reversal_threshold"):
            load_tester_config(str(_write_config(tmp_path, block)))

    def test_reject_one(self, tmp_path: Path) -> None:
        block = _enabled_minimal_block(reversal_threshold=1)
        with pytest.raises(ConfigError, match="reversal_threshold"):
            load_tester_config(str(_write_config(tmp_path, block)))

    def test_reject_percent_string(self, tmp_path: Path) -> None:
        """'0.5%' is a percent-formatted STRING, not a fraction — reject."""
        block = _enabled_minimal_block(reversal_threshold="0.5%")
        with pytest.raises(
            ConfigError,
            match="reversal_threshold.*numeric fraction",
        ):
            load_tester_config(str(_write_config(tmp_path, block)))


# ---------------------------------------------------------------------------
# Group 6 — both triggers disabled when enabled=true
# ---------------------------------------------------------------------------

class TestBothTriggersDisabled:
    """At least one of candidate_threshold/confirmed_median must be enabled."""

    def test_both_disabled_rejected(self, tmp_path: Path) -> None:
        block = dedent(
            """
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                reversal_threshold: 0.005
                candidate_trigger_threshold: 0.012
                local_window: 5
              triggers:
                candidate_threshold:
                  enabled: false
                confirmed_median:
                  enabled: false
              lifecycle:
                freeze_confirmed_legs: 5
                stop_check: confirm_bar_only
                stopping_exit: opposite_st_flip
            """
        ).strip()
        with pytest.raises(
            ConfigError, match="at least one trigger must be enabled"
        ):
            load_tester_config(str(_write_config(tmp_path, block)))


# ---------------------------------------------------------------------------
# Group 7 — wrong lifecycle enum
# ---------------------------------------------------------------------------

class TestLifecycleEnum:
    """stop_check / stopping_exit must match the literal whitelist."""

    def test_wrong_stop_check_rejected(self, tmp_path: Path) -> None:
        block = _enabled_minimal_block(stop_check="every_bar")
        with pytest.raises(
            ConfigError, match="trade_filter.lifecycle.stop_check"
        ):
            load_tester_config(str(_write_config(tmp_path, block)))

    def test_wrong_stopping_exit_rejected(self, tmp_path: Path) -> None:
        block = _enabled_minimal_block(stopping_exit="immediate")
        with pytest.raises(
            ConfigError, match="trade_filter.lifecycle.stopping_exit"
        ):
            load_tester_config(str(_write_config(tmp_path, block)))


# ---------------------------------------------------------------------------
# Group 8 — explicit numeric threshold + explicit quantile simultaneously
# ---------------------------------------------------------------------------

class TestThresholdQuantileMutualExclusion:
    """§11.3 / plan §6.4.1 — uses raw-key presence (not dataclass default)."""

    def test_numeric_threshold_with_explicit_quantile_rejected(
        self, tmp_path: Path
    ) -> None:
        """Numeric ctt + explicit ctq simultaneously => reject."""
        block = dedent(
            """
            trade_filter:
              enabled: true
              type: zigzag_st_mode
              zigzag:
                reversal_threshold: 0.005
                candidate_trigger_threshold: 0.012
                candidate_trigger_quantile: 0.80
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
            """
        ).strip()
        with pytest.raises(
            ConfigError,
            match="candidate_trigger_quantile must not be specified",
        ):
            load_tester_config(str(_write_config(tmp_path, block)))


# ---------------------------------------------------------------------------
# Group 9 — freeze_confirmed_legs < local_window MUST be accepted
# ---------------------------------------------------------------------------

class TestFreezeBelowLocalWindow:
    """Plan §6.5 Note 1: freeze_confirmed_legs < local_window is VALID;
    no warning, no error. Pinned because it's a recurring intuition trap.
    """

    def test_freeze_below_local_window_accepted(self, tmp_path: Path) -> None:
        block = _enabled_minimal_block(
            freeze_confirmed_legs=2, local_window=10
        )
        cfg = load_tester_config(str(_write_config(tmp_path, block)))
        tf = cfg["trade_filter"]
        assert isinstance(tf, TradeFilterConfig)
        assert tf.enabled is True
        assert tf.lifecycle.freeze_confirmed_legs == 2
        assert tf.zigzag.local_window == 10
        # Sanity: no diagnostics-style warning leaked into config — only the
        # validated TradeFilterConfig is exposed.

    def test_freeze_zero_with_default_local_window_accepted(
        self, tmp_path: Path
    ) -> None:
        """Boundary: freeze=0 (legitimate "no freeze") — accepted."""
        block = _enabled_minimal_block(freeze_confirmed_legs=0)
        cfg = load_tester_config(str(_write_config(tmp_path, block)))
        tf = cfg["trade_filter"]
        assert isinstance(tf, TradeFilterConfig)
        assert tf.lifecycle.freeze_confirmed_legs == 0


# ---------------------------------------------------------------------------
# Group 10 — segmentation.mode=equal_blocks + trade_filter.enabled=true reject
# ---------------------------------------------------------------------------

def _config_with_segmentation(
    tmp_path: Path,
    seg_mode: str,
    trade_filter_block: str,
) -> Path:
    """Like _write_config but with explicit segmentation.mode override."""
    base = _BASE_YAML.replace("mode: legacy", f"mode: {seg_mode}")
    text = base + "\n" + trade_filter_block + "\n"
    cfg_path = tmp_path / "config_tester.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    return cfg_path


class TestEqualBlocksEnabledRejection:
    """Plan §5.5 fail-fast contract: zigzag_st_mode is legacy-only in Phase 2.

    The check lives in ``load_tester_config`` (not in ``run_backtest``) per
    owner instruction — must reject BEFORE any stats / backtest work.
    """

    def test_equal_blocks_with_enabled_filter_rejected(
        self, tmp_path: Path
    ) -> None:
        block = _enabled_minimal_block()  # enabled: true, fully valid
        cfg_path = _config_with_segmentation(
            tmp_path, seg_mode="equal_blocks", trade_filter_block=block
        )
        with pytest.raises(
            ConfigError,
            match=(
                "zigzag_st_mode is supported only with "
                "segmentation.mode=legacy"
            ),
        ):
            load_tester_config(str(cfg_path))

    def test_equal_blocks_with_disabled_filter_accepted(
        self, tmp_path: Path
    ) -> None:
        """Negative control: equal_blocks + disabled filter must still work.

        Disabled-baseline contract (Appendix A v1.1 §11.1) — the filter is a
        no-op and equal_blocks is left untouched.
        """
        block = dedent(
            """
            trade_filter:
              enabled: false
              type: zigzag_st_mode
            """
        ).strip()
        cfg_path = _config_with_segmentation(
            tmp_path, seg_mode="equal_blocks", trade_filter_block=block
        )
        cfg = load_tester_config(str(cfg_path))
        assert cfg["segmentation"]["mode"] == "equal_blocks"
        assert isinstance(cfg["trade_filter"], TradeFilterConfig)
        assert cfg["trade_filter"].enabled is False

    def test_legacy_with_enabled_filter_accepted(
        self, tmp_path: Path
    ) -> None:
        """Positive control: legacy + enabled — the supported combination."""
        block = _enabled_minimal_block()
        cfg_path = _config_with_segmentation(
            tmp_path, seg_mode="legacy", trade_filter_block=block
        )
        cfg = load_tester_config(str(cfg_path))
        assert cfg["segmentation"]["mode"] == "legacy"
        assert cfg["trade_filter"].enabled is True

    def test_equal_blocks_without_filter_block_accepted(
        self, tmp_path: Path
    ) -> None:
        """Negative control: equal_blocks + NO trade_filter block at all.

        The gate must not fire when trade_filter is None — that's the
        existing pre-Phase-2 path used by the WP-T1 baseline.
        """
        cfg_path = _config_with_segmentation(
            tmp_path,
            seg_mode="equal_blocks",
            trade_filter_block="",  # no trade_filter block
        )
        cfg = load_tester_config(str(cfg_path))
        assert cfg["segmentation"]["mode"] == "equal_blocks"
        assert cfg["trade_filter"] is None
