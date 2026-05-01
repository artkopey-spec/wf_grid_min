"""
WP3 — Unit tests: full-dataset ``ZigZagGlobalStats``.

Covers exactly the WP3 contract:

- close-only ZigZag pivot detection (plan §3.3, Appendix A v1.1 §3.4) on a
  shared fixture;
- ``high`` / ``low`` are not used at all (signature only takes ``close``);
- ``reversal_threshold`` is treated as a *fraction* of price, not a percent;
- ``global_median`` is computed on the full dataset, not on a train / WF
  slice;
- auto threshold materialisation uses ``np.quantile(..., method="linear")``;
- init failure raises ``ConfigError`` BEFORE WF execution (no confirmed
  legs / NaN ``global_median`` / auto + insufficient legs / NaN materialised
  threshold);
- explicit numeric threshold does not require ``min_legs_for_quantile``;
- ``candidate_trigger_source`` and ``metadata`` snapshot are populated.

Spec reference:  Appendix A v1.1 §3, §12, §15.1, §15.7, §16, §17.5, §17.6.
Plan reference:  WP3, §3.3, §6, §12.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import replace
from typing import List

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ConfirmedLeg,
    ZigZagGlobalStats,
    build_zigzag_global_stats,
    detect_confirmed_legs_close_only,
)
from supertrend_optimizer.utils.exceptions import ConfigError
from wf_grid.config.schema import (
    TradeFilterConfig,
    TradeFilterDiagnosticsConfig,
    TradeFilterLifecycleConfig,
    TradeFilterTriggerToggleConfig,
    TradeFilterTriggersConfig,
    TradeFilterZigZagConfig,
)
from wf_grid.tests.zigzag_st_close_only_fixture import (
    MANY_LEG_SAWTOOTH,
    SIMPLE_ZIGZAG,
    _FEW_LEGS_CLOSE,
    _FEW_LEGS_EXPECTED_HEIGHTS,
    _FEW_LEGS_R,
    _FLAT_CLOSE,
    _FLAT_R,
    CloseOnlyFixture,
    ExpectedLeg,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_filter_config(
    *,
    reversal_threshold: float = 0.02,
    candidate_trigger_threshold=0.05,
    candidate_trigger_quantile=None,
    local_window: int = 5,
) -> TradeFilterConfig:
    """Build a minimal enabled TradeFilterConfig for build_zigzag_global_stats.

    The build function is duck-typed; this helper just keeps the tests close
    to the real WP2 dataclasses to catch any drift between WP2 schema and
    WP3 consumption.
    """
    return TradeFilterConfig(
        enabled=True,
        type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            reversal_threshold=reversal_threshold,
            candidate_trigger_threshold=candidate_trigger_threshold,
            candidate_trigger_quantile=candidate_trigger_quantile,
            local_window=local_window,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(),
        diagnostics=TradeFilterDiagnosticsConfig(),
    )


def _assert_legs_match(actual: List[ConfirmedLeg], expected: List[ExpectedLeg]) -> None:
    assert len(actual) == len(expected), (
        f"leg count mismatch: got {len(actual)}, expected {len(expected)}"
    )
    for i, (a, e) in enumerate(zip(actual, expected)):
        assert a.start_bar == e.start_bar, f"leg #{i} start_bar"
        assert a.end_bar == e.end_bar, f"leg #{i} end_bar"
        assert a.confirm_bar == e.confirm_bar, f"leg #{i} confirm_bar"
        assert a.start_price == pytest.approx(e.start_price), f"leg #{i} start_price"
        assert a.end_price == pytest.approx(e.end_price), f"leg #{i} end_price"
        assert a.direction == e.direction, f"leg #{i} direction"
        assert a.height_pct == pytest.approx(e.height_pct), f"leg #{i} height_pct"
        # Causality and structural invariants from plan §3.2.
        assert a.confirm_bar > a.end_bar, f"leg #{i} confirm_bar > end_bar"
        assert a.end_bar > a.start_bar, f"leg #{i} end_bar > start_bar"
        assert a.height_pct > 0.0, f"leg #{i} height_pct > 0"
        assert a.direction in (+1, -1), f"leg #{i} direction in (+1, -1)"


# ---------------------------------------------------------------------------
# Close-only helper — shared fixtures, signature, fraction semantics
# ---------------------------------------------------------------------------

class TestCloseOnlyHelper:
    """detect_confirmed_legs_close_only(close, reversal_threshold)."""

    @pytest.mark.parametrize(
        "fixture",
        [SIMPLE_ZIGZAG, MANY_LEG_SAWTOOTH],
        ids=lambda f: f.name,
    )
    def test_shared_fixture_legs_match_expected(self, fixture: CloseOnlyFixture):
        legs = detect_confirmed_legs_close_only(fixture.close, fixture.reversal_threshold)
        _assert_legs_match(legs, fixture.expected_legs)

    def test_signature_takes_only_close_and_threshold(self):
        """Helper must NOT accept high / low / hlc3 / ohlc4 in Phase 1."""
        sig = inspect.signature(detect_confirmed_legs_close_only)
        params = list(sig.parameters)
        assert params == ["close", "reversal_threshold"], (
            f"signature must be close-only; got {params}"
        )

    def test_high_low_not_used_when_close_is_input(self):
        """The helper takes a single ``close`` array — there is no surface for
        high / low to leak into the ZigZag formula by definition.

        We additionally verify that scaling ``close`` (a degenerate "high/low"
        substitute) leaves leg shapes governed solely by close-relative ratios:
        the helper does not silently consume an alternative price stream.
        """
        legs_close = detect_confirmed_legs_close_only(
            SIMPLE_ZIGZAG.close, SIMPLE_ZIGZAG.reversal_threshold
        )
        scaled = SIMPLE_ZIGZAG.close * 7.0
        legs_scaled = detect_confirmed_legs_close_only(
            scaled, SIMPLE_ZIGZAG.reversal_threshold
        )
        # Scaling all close by a positive constant must preserve all
        # *relative* metrics (heights are ratios), so direction / bars /
        # heights are bit-identical even though absolute prices change.
        assert len(legs_close) == len(legs_scaled)
        for a, b in zip(legs_close, legs_scaled):
            assert a.start_bar == b.start_bar
            assert a.end_bar == b.end_bar
            assert a.confirm_bar == b.confirm_bar
            assert a.direction == b.direction
            assert a.height_pct == pytest.approx(b.height_pct)

    def test_reversal_threshold_is_fraction_not_percent(self):
        """0.005 means 0.5% of price; not 0.5 (i.e. 50%).

        With a 0.5% reversal threshold a synthetic 0.6% reversal must be
        detected, while a 0.4% reversal must NOT confirm a leg.
        """
        threshold = 0.005

        # 0.6% candidate up-leg, 0.6% reversal — must produce ≥ 1 confirmed
        # leg.
        close_06 = np.array(
            [100.0, 100.6, 100.0],  # up to +0.6%, then back to 100 — > threshold reversal from 100.6
            dtype=np.float64,
        )
        legs_06 = detect_confirmed_legs_close_only(close_06, threshold)
        assert len(legs_06) >= 1

        # 0.4% candidate up-leg, 0.4% reversal — must NOT confirm any leg.
        close_04 = np.array(
            [100.0, 100.4, 100.0],
            dtype=np.float64,
        )
        legs_04 = detect_confirmed_legs_close_only(close_04, threshold)
        assert legs_04 == []

    def test_threshold_outside_unit_interval_rejected(self):
        """``reversal_threshold`` must be a fraction in (0, 1)."""
        with pytest.raises(ConfigError):
            detect_confirmed_legs_close_only(SIMPLE_ZIGZAG.close, 0.0)
        with pytest.raises(ConfigError):
            detect_confirmed_legs_close_only(SIMPLE_ZIGZAG.close, 1.0)
        with pytest.raises(ConfigError):
            detect_confirmed_legs_close_only(SIMPLE_ZIGZAG.close, float("nan"))

    def test_empty_or_short_close_returns_no_legs(self):
        assert detect_confirmed_legs_close_only(np.array([], dtype=np.float64), 0.01) == []
        assert detect_confirmed_legs_close_only(np.array([100.0]), 0.01) == []

    def test_flat_close_returns_no_legs(self):
        assert detect_confirmed_legs_close_only(_FLAT_CLOSE, _FLAT_R) == []


# ---------------------------------------------------------------------------
# build_zigzag_global_stats — happy paths
# ---------------------------------------------------------------------------

class TestBuildGlobalStatsHappyPath:

    def test_explicit_numeric_threshold_basic(self):
        """Explicit numeric threshold materialises with source ``"explicit"``."""
        cfg = _make_filter_config(
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            candidate_trigger_threshold=0.05,
            candidate_trigger_quantile=None,
            local_window=5,
        )
        stats = build_zigzag_global_stats(SIMPLE_ZIGZAG.close, cfg)

        assert isinstance(stats, ZigZagGlobalStats)
        assert stats.reversal_threshold == pytest.approx(SIMPLE_ZIGZAG.reversal_threshold)
        assert stats.global_stats_source == "full_dataset"
        assert stats.leg_height_mode == "pct"
        assert stats.n_legs_total == len(SIMPLE_ZIGZAG.expected_legs)
        np.testing.assert_allclose(
            stats.confirmed_heights_pct, SIMPLE_ZIGZAG.expected_heights_pct
        )
        assert stats.global_median == pytest.approx(SIMPLE_ZIGZAG.expected_global_median)
        assert stats.candidate_trigger_threshold == pytest.approx(0.05)
        assert stats.candidate_trigger_source == "explicit"
        assert stats.candidate_trigger_quantile is None
        assert stats.insufficient_data is False
        assert stats.fail_closed_reason is None

    def test_global_median_uses_full_dataset_not_train_slice(self):
        """``global_median`` is computed over confirmed legs from the FULL
        input series, not from a train / WF slice (Appendix A v1.1 §12,
        plan §4.3)."""
        cfg = _make_filter_config(
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            candidate_trigger_threshold=0.05,
            candidate_trigger_quantile=None,
        )
        full_close = SIMPLE_ZIGZAG.close
        train_close = full_close[:5]  # would yield only the first leg

        # Build on full → uses the full leg population.
        stats_full = build_zigzag_global_stats(full_close, cfg)

        # Compute a (hypothetical) train-only median for reference.
        train_legs = detect_confirmed_legs_close_only(
            train_close, SIMPLE_ZIGZAG.reversal_threshold
        )
        train_heights = np.array([leg.height_pct for leg in train_legs], dtype=np.float64)

        # Sanity: the train slice produces a strictly smaller leg population
        # than the full dataset; the medians therefore differ.
        assert stats_full.n_legs_total > len(train_legs)
        if train_heights.size > 0:
            train_median = float(np.median(train_heights))
            assert stats_full.global_median != pytest.approx(train_median)

        # The full-dataset median matches the directly computed reference.
        np.testing.assert_allclose(
            stats_full.confirmed_heights_pct, SIMPLE_ZIGZAG.expected_heights_pct
        )
        assert stats_full.global_median == pytest.approx(
            SIMPLE_ZIGZAG.expected_global_median
        )

    def test_heights_and_median_are_fractions_not_percents(self):
        """All height metrics must be in the [0, 1] fraction scale."""
        cfg = _make_filter_config(
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            candidate_trigger_threshold=0.05,
        )
        stats = build_zigzag_global_stats(SIMPLE_ZIGZAG.close, cfg)
        assert (stats.confirmed_heights_pct < 1.0).all()
        assert (stats.confirmed_heights_pct > 0.0).all()
        assert 0.0 < stats.global_median < 1.0

    def test_explicit_threshold_not_subject_to_min_legs_gate(self):
        """Explicit numeric threshold must succeed with any positive leg count;
        ``min_legs_for_quantile`` only applies to the auto branch."""
        cfg = _make_filter_config(
            reversal_threshold=_FEW_LEGS_R,
            candidate_trigger_threshold=0.012,  # explicit numeric
            candidate_trigger_quantile=None,
            local_window=5,
        )
        stats = build_zigzag_global_stats(_FEW_LEGS_CLOSE, cfg)
        assert stats.n_legs_total == 1
        np.testing.assert_allclose(
            stats.confirmed_heights_pct, _FEW_LEGS_EXPECTED_HEIGHTS
        )
        assert stats.candidate_trigger_threshold == pytest.approx(0.012)
        assert stats.candidate_trigger_source == "explicit"
        assert stats.metadata["min_legs_for_quantile"] is None

    def test_auto_threshold_uses_np_quantile_method_linear(self):
        """Auto threshold materialises via numpy ``method="linear"``."""
        quantile = 0.80
        cfg = _make_filter_config(
            reversal_threshold=MANY_LEG_SAWTOOTH.reversal_threshold,
            candidate_trigger_threshold="auto",
            candidate_trigger_quantile=quantile,
            local_window=5,
        )
        stats = build_zigzag_global_stats(MANY_LEG_SAWTOOTH.close, cfg)

        expected = float(
            np.quantile(
                MANY_LEG_SAWTOOTH.expected_heights_pct,
                q=quantile,
                method="linear",
            )
        )
        assert stats.candidate_trigger_threshold == pytest.approx(expected)
        # The scalar must match numpy's linear-interpolation quantile bit-by-bit
        # at float64 precision.
        assert stats.candidate_trigger_threshold == expected
        assert stats.candidate_trigger_source == "quantile"
        assert stats.candidate_trigger_quantile == pytest.approx(quantile)


# ---------------------------------------------------------------------------
# build_zigzag_global_stats — init-failure paths (Appendix A v1.1 §12.3)
# ---------------------------------------------------------------------------

class TestInitFailures:

    def test_no_confirmed_legs_raises_config_error(self):
        cfg = _make_filter_config(
            reversal_threshold=_FLAT_R,
            candidate_trigger_threshold=0.05,
        )
        with pytest.raises(ConfigError, match="no confirmed legs"):
            build_zigzag_global_stats(_FLAT_CLOSE, cfg)

    def test_global_median_nan_raises_config_error(self, monkeypatch):
        """Defensive guard for ``global_median`` non-finite (Appendix A §12.3).

        The legitimate height computation always yields finite values, so we
        force ``np.median`` to return NaN to exercise the guard.
        """
        cfg = _make_filter_config(
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            candidate_trigger_threshold=0.05,
        )
        import supertrend_optimizer.core.zigzag_st_filter as zzmod

        monkeypatch.setattr(
            zzmod.np, "median", lambda *a, **k: float("nan")
        )
        with pytest.raises(ConfigError, match="global_median"):
            build_zigzag_global_stats(SIMPLE_ZIGZAG.close, cfg)

    def test_auto_threshold_insufficient_legs_raises(self):
        """Auto + n_legs < max(local_window, 10) → ConfigError before WF."""
        cfg = _make_filter_config(
            reversal_threshold=_FEW_LEGS_R,
            candidate_trigger_threshold="auto",
            candidate_trigger_quantile=0.80,
            local_window=5,
        )
        with pytest.raises(ConfigError, match="auto candidate_trigger_threshold"):
            build_zigzag_global_stats(_FEW_LEGS_CLOSE, cfg)

    def test_auto_min_legs_uses_max_local_window_and_ten(self):
        """``min_legs_for_quantile`` = ``max(local_window, 10)``.

        Many-leg sawtooth has 13 legs.  With local_window=20 the gate is 20
        and must trigger ConfigError.  With local_window=5 the gate is 10
        and 13 legs are sufficient.
        """
        cfg_high = _make_filter_config(
            reversal_threshold=MANY_LEG_SAWTOOTH.reversal_threshold,
            candidate_trigger_threshold="auto",
            candidate_trigger_quantile=0.80,
            local_window=20,
        )
        with pytest.raises(ConfigError, match="local_window=20"):
            build_zigzag_global_stats(MANY_LEG_SAWTOOTH.close, cfg_high)

        cfg_low = replace(
            cfg_high,
            zigzag=replace(cfg_high.zigzag, local_window=5),
        )
        stats = build_zigzag_global_stats(MANY_LEG_SAWTOOTH.close, cfg_low)
        assert stats.metadata["min_legs_for_quantile"] == 10

    def test_materialized_threshold_nan_raises(self, monkeypatch):
        """Defensive guard for materialised threshold non-finite."""
        cfg = _make_filter_config(
            reversal_threshold=MANY_LEG_SAWTOOTH.reversal_threshold,
            candidate_trigger_threshold="auto",
            candidate_trigger_quantile=0.80,
            local_window=5,
        )
        import supertrend_optimizer.core.zigzag_st_filter as zzmod

        monkeypatch.setattr(
            zzmod.np, "quantile", lambda *a, **k: float("nan")
        )
        with pytest.raises(ConfigError, match="materialized"):
            build_zigzag_global_stats(MANY_LEG_SAWTOOTH.close, cfg)

    def test_missing_reversal_threshold_raises(self):
        cfg = _make_filter_config(reversal_threshold=0.02)
        # Force the field to None to simulate WP2 schema slip; build_*
        # must defend even against a malformed config.
        bad_zigzag = replace(cfg.zigzag, reversal_threshold=None)
        bad_cfg = replace(cfg, zigzag=bad_zigzag)
        with pytest.raises(ConfigError, match="reversal_threshold"):
            build_zigzag_global_stats(SIMPLE_ZIGZAG.close, bad_cfg)

    def test_missing_candidate_trigger_threshold_raises(self):
        cfg = _make_filter_config(reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold)
        bad_zigzag = replace(cfg.zigzag, candidate_trigger_threshold=None)
        bad_cfg = replace(cfg, zigzag=bad_zigzag)
        with pytest.raises(ConfigError, match="candidate_trigger_threshold"):
            build_zigzag_global_stats(SIMPLE_ZIGZAG.close, bad_cfg)

    def test_auto_without_quantile_raises(self):
        cfg = _make_filter_config(
            reversal_threshold=MANY_LEG_SAWTOOTH.reversal_threshold,
            candidate_trigger_threshold="auto",
            candidate_trigger_quantile=None,
        )
        with pytest.raises(ConfigError, match="candidate_trigger_quantile"):
            build_zigzag_global_stats(MANY_LEG_SAWTOOTH.close, cfg)


# ---------------------------------------------------------------------------
# Source / metadata population
# ---------------------------------------------------------------------------

class TestSourceAndMetadata:

    def test_explicit_metadata_snapshot(self):
        cfg = _make_filter_config(
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            candidate_trigger_threshold=0.012,
            candidate_trigger_quantile=None,
            local_window=5,
        )
        stats = build_zigzag_global_stats(SIMPLE_ZIGZAG.close, cfg)
        meta = stats.metadata

        assert meta["candidate_trigger_source"] == "explicit"
        assert meta["candidate_trigger_threshold_mode"] == "explicit"
        assert meta["candidate_trigger_quantile"] is None
        assert meta["min_legs_for_quantile"] is None
        assert meta["n_legs_total"] == stats.n_legs_total

        snap = meta["config_snapshot"]
        assert snap["reversal_threshold"] == pytest.approx(
            SIMPLE_ZIGZAG.reversal_threshold
        )
        assert snap["local_window"] == 5
        assert snap["global_stats_source"] == "full_dataset"
        assert snap["leg_height_mode"] == "pct"
        assert snap["candidate_trigger_threshold"] == pytest.approx(0.012)
        assert snap["candidate_trigger_quantile"] is None

    def test_auto_metadata_snapshot(self):
        cfg = _make_filter_config(
            reversal_threshold=MANY_LEG_SAWTOOTH.reversal_threshold,
            candidate_trigger_threshold="auto",
            candidate_trigger_quantile=0.80,
            local_window=5,
        )
        stats = build_zigzag_global_stats(MANY_LEG_SAWTOOTH.close, cfg)
        meta = stats.metadata

        assert meta["candidate_trigger_source"] == "quantile"
        assert meta["candidate_trigger_threshold_mode"] == "auto"
        assert meta["candidate_trigger_quantile"] == pytest.approx(0.80)
        assert meta["min_legs_for_quantile"] == max(5, 10)
        assert meta["n_legs_total"] == stats.n_legs_total

        snap = meta["config_snapshot"]
        assert snap["candidate_trigger_threshold"] == "auto"
        assert snap["candidate_trigger_quantile"] == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# Anti-drift gates: WP3 must not silently advance into WP4+ behaviours.
# ---------------------------------------------------------------------------

class TestAntiDrift:

    def test_module_does_not_export_runtime_artifacts_yet(self):
        """WP3 ships confirmed legs and global stats; WP4 adds the causal
        per-bar engine; WP5 adds FSM and ``apply(...)``.  Runtime backtest
        artifacts remain deferred to WP7+.
        """
        import supertrend_optimizer.core.zigzag_st_filter as zzmod

        forbidden_until_wp7_plus = {
            "RawBacktestArtifacts",
        }
        public = {name for name in dir(zzmod) if not name.startswith("_")}
        assert public.isdisjoint(forbidden_until_wp7_plus), (
            f"Module must not yet expose: {public & forbidden_until_wp7_plus}"
        )

    def test_module_does_not_grep_high_or_low(self):
        """Plan §3.3 / §8.3.1 prohibit ``high`` / ``low`` in pivot/height path.

        WP3 does not yet have a per-bar engine, so this is a forward-looking
        guard on the close-only formula module.  The grep gate is restricted
        to *executable* lines (whitespace / docstring / comment lines are
        excluded) so legitimate references in module docstrings remain
        permitted.
        """
        from pathlib import Path

        import supertrend_optimizer.core.zigzag_st_filter as zzmod

        text = Path(zzmod.__file__).read_text(encoding="utf-8")
        # Strip both single-line comments and the module-level docstring so
        # that the grep gate only inspects executable code.
        # We keep the check token-based (whole word) to avoid catching e.g.
        # ``highlight`` substrings.
        import re
        import io
        import tokenize

        code_only_tokens: list[str] = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                tok_type, tok_str, _, _, _ = tok
                if tok_type in (tokenize.COMMENT,):
                    continue
                if tok_type == tokenize.STRING:
                    # Skip string literals (incl. docstrings) — they may
                    # legitimately mention "high" / "low" in spec citations.
                    continue
                code_only_tokens.append(tok_str)
        except tokenize.TokenError:  # pragma: no cover - defensive
            code_only_tokens = re.split(r"\W+", text)

        for forbidden in ("high", "low", "hlc3", "ohlc4"):
            assert forbidden not in code_only_tokens, (
                f"close-only contract violated: token {forbidden!r} found in "
                "executable code of zigzag_st_filter.py"
            )
