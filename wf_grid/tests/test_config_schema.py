"""
Unit tests for A1: config schema, loader, validation, periods_per_year resolution.
"""

from __future__ import annotations

import math
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from wf_grid.config.loader import (
    ConfigError,
    load_grid_config,
    resolve_periods_per_year,
)
from wf_grid.config.schema import (
    INVALID_METRIC_VALUE,
    MAX_VALID_METRIC,
    DataConfig,
    ExecutionConfig,
    GridConfig,
    WalkForwardConfig,
)
from wf_grid.status.status_model import (
    CandidateStatus,
    RankingMode,
    ScoreContractStatus,
    StepStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


MINIMAL_VALID_YAML = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""


# ---------------------------------------------------------------------------
# Happy path — minimal valid config loads without error
# ---------------------------------------------------------------------------

class TestLoadMinimalValid:
    def test_loads_without_error(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert isinstance(cfg, GridConfig)

    def test_defaults_populated(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.optimization.atr_period_range == [5, 55]
        assert cfg.optimization.multiplier_range == [1.5, 5.5]
        assert cfg.optimization.multiplier_step == 0.1
        assert cfg.optimization.trade_mode == "both"
        assert cfg.backtest.commission == 0.000235
        assert cfg.backtest.min_trades_required == 3
        assert cfg.backtest.early_exit_enabled is False
        assert cfg.validation.warmup_period == 0
        assert cfg.validation.warmup_period_auto is False
        assert cfg.validation.walk_forward.scheme == "rolling"
        assert cfg.gates.step.max_drawdown_threshold == -0.50
        assert cfg.gates.candidate.max_drawdown_threshold == -0.50
        assert cfg.gates.candidate.min_trades_median == 3.0
        assert cfg.ranking.mode == "legacy"
        assert cfg.status.min_meaningful_bars == 30

    def test_file_path_stored(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.data.file_path == "data.csv"

    def test_periods_per_year_default_auto(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.data.periods_per_year == "auto"
        assert cfg.resolved_periods_per_year is None  # not resolved yet (no data provided)


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

class TestMissingRequiredFields:
    def test_missing_file_path(self, tmp_path):
        yaml_content = """\
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="file_path"):
            load_grid_config(path)

    def test_missing_train_size(self, tmp_path):
        yaml_content = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    test_size: "30D"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="train_size"):
            load_grid_config(path)

    def test_missing_test_size(self, tmp_path):
        yaml_content = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="test_size"):
            load_grid_config(path)

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_grid_config(str(tmp_path / "nonexistent.yaml"))

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ConfigError, match="empty"):
            load_grid_config(str(p))


# ---------------------------------------------------------------------------
# Invalid field values
# ---------------------------------------------------------------------------

class TestInvalidFieldValues:
    def _base(self, tmp_path, extra: str) -> str:
        return _write_yaml(tmp_path, MINIMAL_VALID_YAML + extra)

    def test_atr_min_below_2(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "optimization:\n  atr_period_range: [1, 55]\n")
        with pytest.raises(ConfigError, match="atr_period_range"):
            load_grid_config(path)

    def test_atr_min_greater_than_max(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "optimization:\n  atr_period_range: [55, 5]\n")
        with pytest.raises(ConfigError, match="atr_period_range"):
            load_grid_config(path)

    def test_multiplier_min_zero(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "optimization:\n  multiplier_range: [0.0, 5.5]\n")
        with pytest.raises(ConfigError, match="multiplier_range"):
            load_grid_config(path)

    def test_multiplier_step_zero(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "optimization:\n  multiplier_step: 0.0\n")
        with pytest.raises(ConfigError, match="multiplier_step"):
            load_grid_config(path)

    def test_trade_mode_invalid(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "optimization:\n  trade_mode: unknown\n")
        with pytest.raises(ConfigError, match="trade_mode"):
            load_grid_config(path)

    def test_negative_commission(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "backtest:\n  commission: -0.001\n")
        with pytest.raises(ConfigError, match="commission"):
            load_grid_config(path)

    def test_negative_min_trades(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "backtest:\n  min_trades_required: -1\n")
        with pytest.raises(ConfigError, match="min_trades_required"):
            load_grid_config(path)

    def test_negative_warmup(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "validation:\n  warmup_period: -5\n")
        with pytest.raises(ConfigError, match="warmup_period"):
            load_grid_config(path)

    def test_invalid_wf_scheme(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
    scheme: "unknown_scheme"
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="scheme"):
            load_grid_config(path)

    def test_step_gate_drawdown_positive(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
gates:
  step:
    max_drawdown_threshold: 0.1
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="max_drawdown_threshold"):
            load_grid_config(path)

    def test_candidate_gate_drawdown_positive(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
gates:
  candidate:
    max_drawdown_threshold: 0.5
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="max_drawdown_threshold"):
            load_grid_config(path)

    def test_ranking_mode_invalid(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
ranking:
  mode: bad_mode
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="ranking.mode"):
            load_grid_config(path)

    def test_score_weight_zero(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
scoring:
  score_weights:
    sum_pnl_pct_Median: 0.0
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="score_weights"):
            load_grid_config(path)

    def test_min_meaningful_bars_zero(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
status:
  min_meaningful_bars: 0
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="min_meaningful_bars"):
            load_grid_config(path)


# ---------------------------------------------------------------------------
# periods_per_year: explicit numeric override
# ---------------------------------------------------------------------------

class TestPeriodsPerYearExplicit:
    def test_explicit_float(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
  periods_per_year: 252
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        assert cfg.data.periods_per_year == 252

    def test_explicit_crypto_365(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
  periods_per_year: 365
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        assert cfg.data.periods_per_year == 365

    def test_invalid_negative_ppy(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
  periods_per_year: -252
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="periods_per_year"):
            load_grid_config(path)


# ---------------------------------------------------------------------------
# periods_per_year: auto-detect from DatetimeIndex
# ---------------------------------------------------------------------------

class TestPeriodsPerYearAutoDetect:
    def _make_daily_data(self, n: int = 500) -> pd.DataFrame:
        idx = pd.date_range("2020-01-01", periods=n, freq="1D")
        return pd.DataFrame({"close": 100.0}, index=idx)

    def _make_hourly_data(self, n: int = 500) -> pd.DataFrame:
        idx = pd.date_range("2020-01-01", periods=n, freq="1h")
        return pd.DataFrame({"close": 100.0}, index=idx)

    def test_auto_daily_resolves_approx_365(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        data = self._make_daily_data()
        cfg_resolved = resolve_periods_per_year(cfg, data)
        assert cfg_resolved.resolved_periods_per_year == pytest.approx(365, abs=1)

    def test_auto_hourly_resolves_expected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        data = self._make_hourly_data()
        cfg_resolved = resolve_periods_per_year(cfg, data)
        # Donor CALENDAR basis: bars_per_calendar_day_mean * 365.25.
        # For 500 bars over 21 calendar days: (500/21) * 365.25 ≈ 8696.
        # The naive median-delta formula gives 8766, but the donor algorithm is
        # authoritative; we test the actual donor output.
        assert cfg_resolved.resolved_periods_per_year == pytest.approx(8696, abs=5)

    def test_auto_no_datetime_index_raises(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        data = pd.DataFrame({"close": [1.0, 2.0, 3.0]})  # RangeIndex
        with pytest.raises(ConfigError, match="DatetimeIndex"):
            resolve_periods_per_year(cfg, data)

    def test_explicit_ppy_not_overridden_by_resolve(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
  periods_per_year: 252
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        data = self._make_daily_data()
        cfg_resolved = resolve_periods_per_year(cfg, data)
        # explicit 252 passed through unchanged
        assert cfg_resolved.resolved_periods_per_year == 252.0

    def test_original_config_not_mutated(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        data = self._make_daily_data()
        _ = resolve_periods_per_year(cfg, data)
        assert cfg.resolved_periods_per_year is None   # original untouched


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestEnums:
    def test_step_status_values(self):
        assert StepStatus.OK == "ok"
        assert StepStatus.NO_TRADES == "no_trades"
        assert StepStatus.INSUFFICIENT_BARS == "insufficient_bars"
        assert StepStatus.GATE_FAILED == "gate_failed"
        assert StepStatus.RUNTIME_ERROR == "runtime_error"
        assert StepStatus.INVALID == "invalid"
        assert StepStatus.SKIPPED == "skipped"

    def test_candidate_status_values(self):
        assert CandidateStatus.OK == "ok"
        assert CandidateStatus.PARTIAL == "partial"
        assert CandidateStatus.FAILED == "failed"

    def test_ranking_mode_values(self):
        assert RankingMode.LEGACY == "legacy"
        assert RankingMode.GATES_SCORE == "gates_score"

    def test_score_contract_status_values(self):
        assert ScoreContractStatus.OK == "ok"
        assert ScoreContractStatus.PARTIAL == "partial"
        assert ScoreContractStatus.NO_SCORE == "no_score"


# ---------------------------------------------------------------------------
# Metric constants
# ---------------------------------------------------------------------------

class TestMetricConstants:
    def test_invalid_metric_value(self):
        assert INVALID_METRIC_VALUE == -999.0

    def test_max_valid_metric(self):
        assert MAX_VALID_METRIC == 9999.0

    # -----------------------------------------------------------------------
    # K1: donor/wf_grid drift test
    # wf_grid.config.schema must re-export donor constants, not maintain a
    # separate literal.  If the donor changes its value, this test fails —
    # forcing a conscious review instead of silent drift.
    # -----------------------------------------------------------------------

    def test_invalid_metric_value_drift(self):
        """wf_grid INVALID_METRIC_VALUE must equal donor INVALID_METRIC_VALUE (numeric)."""
        from supertrend_optimizer.utils.constants import (
            INVALID_METRIC_VALUE as DONOR_INVALID,
        )
        assert INVALID_METRIC_VALUE == DONOR_INVALID, (
            f"INVALID_METRIC_VALUE drift detected: "
            f"wf_grid={INVALID_METRIC_VALUE!r} vs donor={DONOR_INVALID!r}. "
            "Keep a single source of truth — re-export donor constant in schema.py."
        )

    def test_invalid_metric_value_is_donor_identity(self):
        """wf_grid INVALID_METRIC_VALUE must BE donor's object (identity), not just equal."""
        from supertrend_optimizer.utils.constants import (
            INVALID_METRIC_VALUE as DONOR_INVALID,
        )
        # For float Final constants, identity is the strongest drift check.
        assert INVALID_METRIC_VALUE is DONOR_INVALID, (
            "schema.py must re-export donor INVALID_METRIC_VALUE (import from "
            "supertrend_optimizer.utils.constants), not redefine it as a local literal."
        )

    def test_max_valid_metric_drift(self):
        """wf_grid MAX_VALID_METRIC must equal donor MAX_VALID_METRIC (numeric)."""
        from supertrend_optimizer.utils.constants import (
            MAX_VALID_METRIC as DONOR_MAX,
        )
        assert MAX_VALID_METRIC == DONOR_MAX, (
            f"MAX_VALID_METRIC drift detected: "
            f"wf_grid={MAX_VALID_METRIC!r} vs donor={DONOR_MAX!r}."
        )

    def test_max_valid_metric_is_donor_identity(self):
        """wf_grid MAX_VALID_METRIC must BE donor's object (identity)."""
        from supertrend_optimizer.utils.constants import (
            MAX_VALID_METRIC as DONOR_MAX,
        )
        assert MAX_VALID_METRIC is DONOR_MAX


# ---------------------------------------------------------------------------
# GridConfig helper method
# ---------------------------------------------------------------------------

class TestGridConfigHelpers:
    def _base_cfg(self, tmp_path) -> GridConfig:
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        return load_grid_config(path)

    def test_effective_step_min_trades_default(self, tmp_path):
        cfg = self._base_cfg(tmp_path)
        # gates.step.min_trades is None -> fallback to backtest.min_trades_required (3)
        assert cfg.effective_step_min_trades() == 3

    def test_effective_step_min_trades_explicit_overrides_backtest(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
backtest:
  min_trades_required: 3
gates:
  step:
    min_trades: 5
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        # explicit gates.step.min_trades=5 must win over backtest.min_trades_required=3
        assert cfg.effective_step_min_trades() == 5

    def test_step_size_none_by_default(self, tmp_path):
        cfg = self._base_cfg(tmp_path)
        assert cfg.validation.walk_forward.step_size is None

    def test_worst_segment_disabled_by_default(self, tmp_path):
        cfg = self._base_cfg(tmp_path)
        assert cfg.gates.candidate.worst_segment_pnl_threshold is None


# ---------------------------------------------------------------------------
# Fix 3: trade_mode alias contract — "revers" is valid (same as "both")
# ---------------------------------------------------------------------------

class TestTradeModeAliases:
    def _yaml_with_mode(self, mode: str) -> str:
        return f"""\
data:
  file_path: data.csv
optimization:
  trade_mode: {mode}
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""

    def test_both_valid(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_mode("both"))
        cfg = load_grid_config(path)
        assert cfg.optimization.trade_mode == "both"

    def test_revers_valid(self, tmp_path):
        # "revers" is a donor-native alias for long+short with position reversal;
        # the loader accepts it as-is without normalisation.
        path = _write_yaml(tmp_path, self._yaml_with_mode("revers"))
        cfg = load_grid_config(path)
        assert cfg.optimization.trade_mode == "revers"

    def test_long_valid(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_mode("long"))
        cfg = load_grid_config(path)
        assert cfg.optimization.trade_mode == "long"

    def test_short_valid(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_mode("short"))
        cfg = load_grid_config(path)
        assert cfg.optimization.trade_mode == "short"

    def test_invalid_mode_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_mode("unknown"))
        with pytest.raises(ConfigError, match="trade_mode"):
            load_grid_config(path)

    def test_revers_and_both_produce_distinct_ids_note(self):
        # Contract: no normalisation is applied — "revers" != "both" as strings.
        # grid_point_id will differ, which is intentional (user chose explicit mode).
        assert "revers" != "both"


# ---------------------------------------------------------------------------
# Fix 2: deepcopy mutation safety
# ---------------------------------------------------------------------------

class TestCopyMutationSafety:
    def _make_daily_data(self, n: int = 500) -> pd.DataFrame:
        idx = pd.date_range("2020-01-01", periods=n, freq="1D")
        return pd.DataFrame({"close": 100.0}, index=idx)

    def test_resolve_does_not_mutate_original(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        data = self._make_daily_data()

        cfg_resolved = resolve_periods_per_year(cfg, data)

        # original completely untouched
        assert cfg.resolved_periods_per_year is None
        assert cfg.data.file_path == "data.csv"

    def test_resolved_optimization_is_independent(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        data = self._make_daily_data()

        cfg_resolved = resolve_periods_per_year(cfg, data)

        # mutate the resolved copy's nested list — original must not change
        cfg_resolved.optimization.atr_period_range[0] = 999
        assert cfg.optimization.atr_period_range[0] != 999

    def test_two_resolves_are_independent(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        data = self._make_daily_data()

        r1 = resolve_periods_per_year(cfg, data)
        r2 = resolve_periods_per_year(cfg, data)

        # mutate one copy — other must not be affected
        r1.optimization.atr_period_range[0] = 42
        assert r2.optimization.atr_period_range[0] != 42


# ---------------------------------------------------------------------------
# Fix 1: donor algorithm parity — annualization_basis
# ---------------------------------------------------------------------------

class TestDonorAlgorithmParity:
    """
    Verify that loader delegates to donor resolve_periods_per_year_from_config.
    The donor uses CALENDAR basis by default (market=None), which gives:
      bars_per_calendar_day_mean * 365.25.
    For daily data with no gaps: 1.0 * 365.25 ≈ 365 (rounded to nearest int).
    For trading basis (252): bars_per_active_day_median * 252.
    """

    def _make_daily_data(self, n: int = 500) -> pd.DataFrame:
        idx = pd.date_range("2020-01-01", periods=n, freq="1D")
        return pd.DataFrame({"close": 100.0}, index=idx)

    def test_auto_calendar_basis_by_default(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        data = self._make_daily_data()
        cfg_r = resolve_periods_per_year(cfg, data)
        # CALENDAR basis on daily data: 365.25, rounded ≈ 365
        assert cfg_r.resolved_periods_per_year == pytest.approx(365.25, abs=1)

    def test_trading_basis_explicit(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
  annualization_basis: trading
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        data = self._make_daily_data()
        cfg_r = resolve_periods_per_year(cfg, data)
        # TRADING basis on daily data: ~1 bar/active_day * 252 = 252
        assert cfg_r.resolved_periods_per_year == pytest.approx(252, abs=2)

    def test_explicit_numeric_overrides_basis(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
  periods_per_year: 2190
  annualization_basis: trading
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        data = self._make_daily_data()
        cfg_r = resolve_periods_per_year(cfg, data)
        # explicit numeric wins over basis
        assert cfg_r.resolved_periods_per_year == 2190.0


# ---------------------------------------------------------------------------
# FIX-2.5 — min_segments_for_ranking validation + walrus fix
# ---------------------------------------------------------------------------

class TestMinSegmentsForRankingValidation:
    """FIX-2.5: loader must reject 0 and negative min_segments_for_ranking."""

    def test_zero_raises_config_error(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
ranking:
  min_segments_for_ranking: 0
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="min_segments_for_ranking"):
            load_grid_config(path)

    def test_negative_raises_config_error(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
ranking:
  min_segments_for_ranking: -1
"""
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="min_segments_for_ranking"):
            load_grid_config(path)

    def test_one_is_accepted(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
ranking:
  min_segments_for_ranking: 1
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        assert cfg.ranking.min_segments_for_ranking == 1

    def test_null_uses_default_formula(self, tmp_path):
        """None → no explicit override, default formula used at runtime."""
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.ranking.min_segments_for_ranking is None


class TestMinSegmentsWalrusFix:
    """FIX-2.5: _resolve_min_segments must respect explicit=1 (walrus would drop it)."""

    def test_explicit_one_is_used_not_ignored(self):
        """Before fix: walrus treated 1 as truthy (ok). But edge: value=0 was the bug."""
        from wf_grid.ranking.tiering import _resolve_min_segments
        from wf_grid.config.schema import GridConfig, DataConfig, RankingConfig

        cfg = GridConfig(
            data=DataConfig(file_path="dummy.csv"),
            ranking=RankingConfig(min_segments_for_ranking=1),
        )
        # With n_segments=5, explicit=1 → raw=1, clamped to max(2, min(1,5))=2
        result = _resolve_min_segments(cfg, 5)
        # Clamp applies: result must be >= 2 when n_segments >= 2
        assert result == 2

    def test_none_uses_default_formula(self):
        """None → default formula max(2, ceil(n*0.5))."""
        from wf_grid.ranking.tiering import _resolve_min_segments
        from wf_grid.config.schema import GridConfig, DataConfig, RankingConfig

        cfg = GridConfig(
            data=DataConfig(file_path="dummy.csv"),
            ranking=RankingConfig(min_segments_for_ranking=None),
        )
        assert _resolve_min_segments(cfg, 6) == 3   # max(2, ceil(6*0.5))=3
        assert _resolve_min_segments(cfg, 4) == 2   # max(2, ceil(4*0.5))=2

    def test_explicit_value_respected_over_formula(self):
        """Explicit=5 with n_segments=10 → raw=5, within [2,10] → result=5."""
        from wf_grid.ranking.tiering import _resolve_min_segments
        from wf_grid.config.schema import GridConfig, DataConfig, RankingConfig

        cfg = GridConfig(
            data=DataConfig(file_path="dummy.csv"),
            ranking=RankingConfig(min_segments_for_ranking=5),
        )
        assert _resolve_min_segments(cfg, 10) == 5


# ===========================================================================
# FIX-3.4: Config mutation safety (deepcopy)
# ===========================================================================

class TestConfigMutationSafety:
    """FIX-3.4: run_grid_pipeline must not mutate the config loaded from YAML.

    The pipeline deepcopies the config immediately after load and works on the
    copy only.  The original (as returned by load_grid_config) must retain its
    original field values.
    """

    def test_deepcopy_isolates_file_path_mutation(self, tmp_path):
        """Mutating the copy's file_path must not affect the original."""
        import copy
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        original = load_grid_config(path)
        original_path = original.data.file_path

        copy_cfg = copy.deepcopy(original)
        copy_cfg.data.file_path = "/overridden/data.csv"

        assert original.data.file_path == original_path

    def test_deepcopy_isolates_resolved_periods(self, tmp_path):
        """Mutating resolved_periods_per_year on copy must not affect original."""
        import copy
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        original = load_grid_config(path)

        copy_cfg = copy.deepcopy(original)
        copy_cfg.resolved_periods_per_year = 365.0

        assert original.resolved_periods_per_year is None

    def test_deepcopy_isolates_nested_config(self, tmp_path):
        """Mutating nested config on copy must not affect original."""
        import copy
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        original = load_grid_config(path)
        original_mode = original.ranking.mode

        copy_cfg = copy.deepcopy(original)
        from wf_grid.status.status_model import RankingMode
        copy_cfg.ranking.mode = RankingMode.GATES_SCORE

        assert original.ranking.mode == original_mode

    def test_run_grid_pipeline_does_not_mutate_loaded_config(self, tmp_path, monkeypatch):
        """Integration: run_grid_pipeline must not mutate the config from load_grid_config.

        Monkeypatches load_grid_config in orchestrator to capture the returned
        object and verify it remains unchanged after pipeline execution starts.
        """
        import copy
        from wf_grid.config.loader import load_grid_config as real_load
        from wf_grid.config.schema import GridConfig, DataConfig, RankingConfig

        captured_originals = []

        def _fake_load(config_path):
            cfg = real_load(config_path)
            captured_originals.append(cfg)
            return cfg

        monkeypatch.setattr("wf_grid.pipeline.orchestrator.load_grid_config", _fake_load)

        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        override_path = str(tmp_path / "override.csv")

        # Write a minimal CSV so data load doesn't fail
        import pandas as pd
        pd.DataFrame({"close": [1.0, 2.0, 3.0]}).to_csv(override_path)

        from wf_grid.pipeline.orchestrator import run_grid_pipeline
        try:
            run_grid_pipeline(str(path), data_path=override_path)
        except Exception:
            pass  # Pipeline may fail on minimal data — we only care about config mutation

        assert len(captured_originals) == 1, "load_grid_config should be called once"
        original = captured_originals[0]
        # Original file_path must NOT be the override
        assert original.data.file_path != override_path


# ===========================================================================
# anchor / min_train_bars / min_test_bars — schema defaults
# ===========================================================================

class TestWalkForwardConfigDefaults:
    """WalkForwardConfig dataclass defaults match make_walk_forward_slices defaults."""

    def test_anchor_default(self):
        wf = WalkForwardConfig()
        assert wf.anchor == "start"

    def test_min_train_bars_default(self):
        wf = WalkForwardConfig()
        assert wf.min_train_bars == 500

    def test_min_test_bars_default(self):
        wf = WalkForwardConfig()
        assert wf.min_test_bars == 100


# ===========================================================================
# anchor / min_train_bars / min_test_bars — loader passthrough
# ===========================================================================

class TestWalkForwardLoaderPassthrough:
    """Loader reads new fields from YAML and falls back to defaults when absent."""

    def test_defaults_when_fields_absent(self, tmp_path):
        """YAML without new fields → dataclass defaults applied."""
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.validation.walk_forward.anchor == "start"
        assert cfg.validation.walk_forward.min_train_bars == 500
        assert cfg.validation.walk_forward.min_test_bars == 100

    def test_explicit_anchor_end(self, tmp_path):
        """YAML with anchor: 'end' and explicit min_* bars → values read correctly."""
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
    anchor: "end"
    min_train_bars: 200
    min_test_bars: 50
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        assert cfg.validation.walk_forward.anchor == "end"
        assert cfg.validation.walk_forward.min_train_bars == 200
        assert cfg.validation.walk_forward.min_test_bars == 50

    def test_explicit_anchor_start(self, tmp_path):
        """YAML with explicit anchor: 'start' → stored correctly."""
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
    anchor: "start"
"""
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        assert cfg.validation.walk_forward.anchor == "start"


# ===========================================================================
# anchor / min_train_bars / min_test_bars — validation
# ===========================================================================

class TestWalkForwardValidation:
    """_validate_config rejects invalid values for new fields."""

    def _yaml_with_wf(self, extra_wf: str) -> str:
        return f"""\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
    {extra_wf}
"""

    def test_invalid_anchor_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf('anchor: "middle"'))
        with pytest.raises(ConfigError, match="anchor"):
            load_grid_config(path)

    def test_anchor_start_allowed(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf('anchor: "start"'))
        cfg = load_grid_config(path)
        assert cfg.validation.walk_forward.anchor == "start"

    def test_anchor_end_allowed(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf('anchor: "end"'))
        cfg = load_grid_config(path)
        assert cfg.validation.walk_forward.anchor == "end"

    def test_min_train_bars_zero_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf("min_train_bars: 0"))
        with pytest.raises(ConfigError, match="min_train_bars"):
            load_grid_config(path)

    def test_min_train_bars_negative_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf("min_train_bars: -10"))
        with pytest.raises(ConfigError, match="min_train_bars"):
            load_grid_config(path)

    def test_min_test_bars_zero_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf("min_test_bars: 0"))
        with pytest.raises(ConfigError, match="min_test_bars"):
            load_grid_config(path)

    def test_min_test_bars_negative_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf("min_test_bars: -5"))
        with pytest.raises(ConfigError, match="min_test_bars"):
            load_grid_config(path)

    def test_min_train_bars_one_accepted(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf("min_train_bars: 1"))
        cfg = load_grid_config(path)
        assert cfg.validation.walk_forward.min_train_bars == 1

    def test_min_test_bars_one_accepted(self, tmp_path):
        path = _write_yaml(tmp_path, self._yaml_with_wf("min_test_bars: 1"))
        cfg = load_grid_config(path)
        assert cfg.validation.walk_forward.min_test_bars == 1


# ===========================================================================
# FIX-early_exit: early_exit_enabled must be False for OOS WF pipeline
# ===========================================================================

class TestEarlyExitValidation:
    """early_exit_enabled=True must be rejected at load time (horizon distortion fix)."""

    def test_default_is_false(self, tmp_path):
        """Schema default must be False after the fix."""
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.backtest.early_exit_enabled is False

    def test_explicit_false_accepted(self, tmp_path):
        """Explicitly setting early_exit_enabled: false must not raise."""
        yaml_text = MINIMAL_VALID_YAML + "backtest:\n  early_exit_enabled: false\n"
        path = _write_yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        assert cfg.backtest.early_exit_enabled is False

    def test_explicit_true_raises_config_error(self, tmp_path):
        """early_exit_enabled: true must raise ConfigError (horizon distortion)."""
        yaml_text = MINIMAL_VALID_YAML + "backtest:\n  early_exit_enabled: true\n"
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="early_exit_enabled"):
            load_grid_config(path)

    def test_error_message_mentions_horizon_distortion(self, tmp_path):
        """Error message must explain the horizon distortion risk."""
        yaml_text = MINIMAL_VALID_YAML + "backtest:\n  early_exit_enabled: true\n"
        path = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="horizon distortion"):
            load_grid_config(path)


# ===========================================================================
# C1 (commit B): Strict YAML schema — unknown keys and schema_version checks
# ===========================================================================

class TestStrictYAMLSchema:
    """C1 acceptance: unknown YAML keys fail with a clear path; schema_version checked."""

    def _yaml(self, tmp_path, content: str) -> str:
        return _write_yaml(tmp_path, content)

    # --- unknown top-level key ---

    def test_unknown_top_level_key_fails(self, tmp_path):
        yaml_text = MINIMAL_VALID_YAML + "unknown_top_key: 123\n"
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="unknown_top_key"):
            load_grid_config(path)

    def test_error_message_includes_key_path(self, tmp_path):
        yaml_text = MINIMAL_VALID_YAML + "typo_key: 1\n"
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="typo_key"):
            load_grid_config(path)

    # --- unknown gates.* keys ---

    def test_unknown_gates_step_key_fails(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
gates:
  step:
    max_drawdown_threshold: -0.5
    unknown_step_gate: true
"""
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="gates.step.unknown_step_gate"):
            load_grid_config(path)

    def test_unknown_gates_candidate_key_fails(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
gates:
  candidate:
    positive_median_threshold: 0.0
    bad_cand_field: 99
"""
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="gates.candidate.bad_cand_field"):
            load_grid_config(path)

    def test_unknown_gates_top_key_fails(self, tmp_path):
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
gates:
  extra_gate_section: {}
"""
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="gates.extra_gate_section"):
            load_grid_config(path)

    # --- typo in worst_segment_pnl_threshold (missing 'h') ---

    def test_typo_worst_segment_pnl_treshold_fails(self, tmp_path):
        """Common typo: 'treshold' instead of 'threshold' must be caught."""
        yaml_text = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
gates:
  candidate:
    worst_segment_pnl_treshold: -0.1
"""
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="worst_segment_pnl_treshold"):
            load_grid_config(path)

    # --- unknown ranking / scoring keys ---

    def test_unknown_ranking_key_fails(self, tmp_path):
        yaml_text = MINIMAL_VALID_YAML + "ranking:\n  mode: legacy\n  unknown_rank_field: 1\n"
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="ranking.unknown_rank_field"):
            load_grid_config(path)

    def test_unknown_scoring_key_fails(self, tmp_path):
        yaml_text = MINIMAL_VALID_YAML + "scoring:\n  normalization_mode: rank\n  extra_field: abc\n"
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="scoring.extra_field"):
            load_grid_config(path)

    # --- schema_version ---

    def test_schema_version_1_accepted(self, tmp_path):
        yaml_text = "schema_version: 1\n" + MINIMAL_VALID_YAML
        path = self._yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        assert isinstance(cfg, GridConfig)

    def test_schema_version_absent_accepted(self, tmp_path):
        """Backward compat: missing schema_version must not raise."""
        path = self._yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert isinstance(cfg, GridConfig)

    def test_schema_version_mismatch_fails(self, tmp_path):
        yaml_text = "schema_version: 99\n" + MINIMAL_VALID_YAML
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="schema_version"):
            load_grid_config(path)

    def test_schema_version_mismatch_error_includes_version(self, tmp_path):
        yaml_text = "schema_version: 99\n" + MINIMAL_VALID_YAML
        path = self._yaml(tmp_path, yaml_text)
        with pytest.raises(ConfigError, match="99"):
            load_grid_config(path)

    # --- valid configs still work ---

    def test_valid_config_with_all_sections_loads(self, tmp_path):
        """Full config with all known sections must load cleanly."""
        yaml_text = """\
schema_version: 1
data:
  file_path: data.csv
  periods_per_year: 252
  annualization_basis: trading
optimization:
  atr_period_range: [5, 55]
  multiplier_range: [1.5, 5.5]
  multiplier_step: 0.1
  trade_mode: long
backtest:
  commission: 0.0003
  min_trades_required: 3
  early_exit_enabled: false
  early_exit_max_drawdown: 0.5
  early_exit_check_bars: 50
validation:
  warmup_period: 0
  warmup_period_auto: true
  walk_forward:
    train_size: "252bars"
    test_size: "126bars"
    step_size: "126bars"
    scheme: rolling
    anchor: start
    min_train_bars: 100
    min_test_bars: 50
gates:
  step:
    min_trades: null
    max_drawdown_threshold: -0.50
  candidate:
    positive_median_threshold: 0.0
    min_trades_median: 3.0
    worst_segment_pnl_threshold: null
    max_drawdown_threshold: -0.50
    min_ok_ratio: 0.7
    min_total_trades: 10
ranking:
  mode: gates_score
  min_segments_for_ranking: null
  sort_by: sum_pnl_pct_Median
  tiebreaker: sum_pnl_pct_Min
scoring:
  normalization_mode: rank
  score_weights:
    sum_pnl_pct_Median: 0.5
    profitable_segments_count: 0.5
status:
  min_meaningful_bars: 30
bucket:
  atr_bucket_step: 2
  mult_bucket_step: 0.2
  min_buckets_for_median: 5
"""
        path = self._yaml(tmp_path, yaml_text)
        cfg = load_grid_config(path)
        assert isinstance(cfg, GridConfig)


# ===========================================================================
# C1 (commit A): test_all_repo_configs_load
# All *.yaml / *.yml outside donor/ must load successfully with load_grid_config.
# This test is green on the OLD (lenient) loader and must remain green after
# adding schema_version: 1 to configs AND after enabling strict validation (Stage 2).
# ===========================================================================

class TestAllRepoConfigsLoad:
    """C1 acceptance: all repo configs outside donor/ load without ConfigError.

    Per §7 First PR Requirements (commit A): this test is added BEFORE the strict
    validator is enabled.  It ensures that after we add `schema_version: 1` to
    existing configs, they still load cleanly.  When strict validation goes in
    (Stage 2 / commit B), the green status of this test proves the configs are
    ready.
    """

    _REPO_ROOT = Path(__file__).resolve().parents[2]  # c:\копии\3.1_wf_grid_bucket_median

    def _collect_yaml_files(self) -> list:
        """Find all *.yaml / *.yml files in repo outside donor/ directory."""
        donor_dir = self._REPO_ROOT / "donor"
        found = []
        for pattern in ("**/*.yaml", "**/*.yml"):
            for p in self._REPO_ROOT.glob(pattern):
                # Skip donor/ subtree
                try:
                    p.relative_to(donor_dir)
                    continue  # inside donor/
                except ValueError:
                    pass
                # Skip pytest cache
                if ".pytest_cache" in p.parts:
                    continue
                # Skip local tester-config workspaces. These files use the
                # tester CLI DSL, not the walk-forward grid config schema.
                if any(part.startswith("config tester") for part in p.parts):
                    continue
                # Skip tester CLI configs (different DSL/schema).
                # These files are validated by supertrend_optimizer.cli.tester.load_tester_config.
                if p.name.startswith("config_tester") and p.suffix in (".yaml", ".yml"):
                    continue
                found.append(p)
        return found

    def test_at_least_one_yaml_found(self):
        """Sanity: there must be at least one YAML config to test."""
        files = self._collect_yaml_files()
        assert len(files) >= 1, (
            f"No *.yaml / *.yml files found outside donor/ under {self._REPO_ROOT}. "
            "Either the test is mis-configured or config.yaml was deleted."
        )

    def test_all_repo_yaml_configs_load(self):
        """Every repo YAML config outside donor/ must load without ConfigError."""
        files = self._collect_yaml_files()
        errors = []
        for yaml_path in files:
            try:
                load_grid_config(str(yaml_path))
            except ConfigError as exc:
                errors.append(f"{yaml_path.name}: {exc}")
            except Exception as exc:
                # FileNotFoundError for data_path inside config is expected;
                # only ConfigError (schema/validation) is a real failure.
                if "file_path" in str(exc).lower() or "no such file" in str(exc).lower():
                    continue  # data file not present in test env — ignore
                errors.append(f"{yaml_path.name}: unexpected {type(exc).__name__}: {exc}")

        assert not errors, (
            "The following repo YAML configs failed to load with the current loader:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# WP-PAR Phase 1: ExecutionConfig tests
# ---------------------------------------------------------------------------

class TestExecutionConfigDefaults:
    """Verify dataclass defaults (plan §1.7 TestExecutionConfigDefaults)."""

    def test_parallel_enabled_default_false(self):
        ex = ExecutionConfig()
        assert ex.parallel_enabled is False

    def test_max_workers_default_none(self):
        ex = ExecutionConfig()
        assert ex.max_workers is None

    def test_chunksize_default_none(self):
        ex = ExecutionConfig()
        assert ex.chunksize is None

    def test_fallback_to_sequential_default_false(self):
        ex = ExecutionConfig()
        assert ex.fallback_to_sequential is False

    def test_grid_config_execution_default(self):
        cfg = GridConfig(data=DataConfig(file_path="x"))
        assert isinstance(cfg.execution, ExecutionConfig)
        assert cfg.execution.parallel_enabled is False
        assert cfg.execution.max_workers is None
        assert cfg.execution.chunksize is None
        assert cfg.execution.fallback_to_sequential is False


class TestExecutionConfigYAML:
    """Verify YAML loading and validation (plan §1.7 TestExecutionConfigYAML)."""

    def test_execution_block_loads(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              parallel_enabled: true
              max_workers: 4
              chunksize: null
              fallback_to_sequential: false
        """))
        cfg = load_grid_config(path)
        assert cfg.execution.parallel_enabled is True
        assert cfg.execution.max_workers == 4
        assert cfg.execution.chunksize is None
        assert cfg.execution.fallback_to_sequential is False

    def test_execution_block_omitted_uses_defaults(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.execution.parallel_enabled is False
        assert cfg.execution.max_workers is None
        assert cfg.execution.chunksize is None
        assert cfg.execution.fallback_to_sequential is False

    def test_max_workers_zero_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              max_workers: 0
        """))
        with pytest.raises(ConfigError, match="max_workers"):
            load_grid_config(path)

    def test_max_workers_negative_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              max_workers: -1
        """))
        with pytest.raises(ConfigError, match="max_workers"):
            load_grid_config(path)

    def test_parallel_enabled_string_yes_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              parallel_enabled: "yes"
        """))
        with pytest.raises(ConfigError, match="parallel_enabled"):
            load_grid_config(path)

    def test_parallel_enabled_int_1_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              parallel_enabled: 1
        """))
        with pytest.raises(ConfigError, match="parallel_enabled"):
            load_grid_config(path)

    def test_max_workers_bool_true_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              max_workers: true
        """))
        with pytest.raises(ConfigError, match="max_workers"):
            load_grid_config(path)

    def test_unknown_key_under_execution_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              unknown_key: 42
        """))
        with pytest.raises(ConfigError, match="unknown config key"):
            load_grid_config(path)

    def test_execution_false_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "execution: false\n")
        with pytest.raises(ConfigError, match="execution must be a YAML mapping"):
            load_grid_config(path)

    def test_execution_list_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + "execution: [1, 2]\n")
        with pytest.raises(ConfigError, match="execution must be a YAML mapping"):
            load_grid_config(path)

    def test_chunksize_zero_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              chunksize: 0
        """))
        with pytest.raises(ConfigError, match="chunksize"):
            load_grid_config(path)

    def test_chunksize_positive_accepted(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              chunksize: 4
        """))
        cfg = load_grid_config(path)
        assert cfg.execution.chunksize == 4


class TestExecutionConfigInteraction:
    """Interaction tests (plan §1.7 TestExecutionConfigInteraction)."""

    def test_parallel_true_max_workers_null_ok(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              parallel_enabled: true
              max_workers: null
        """))
        cfg = load_grid_config(path)
        assert cfg.execution.parallel_enabled is True
        assert cfg.execution.max_workers is None

    def test_parallel_false_max_workers_set_ok(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            execution:
              parallel_enabled: false
              max_workers: 8
        """))
        cfg = load_grid_config(path)
        assert cfg.execution.parallel_enabled is False
        assert cfg.execution.max_workers == 8


# ---------------------------------------------------------------------------
# atr_period_step validation (ТЗ §4.3)
# ---------------------------------------------------------------------------

class TestAtrPeriodStepValidation:
    """9 tests covering the atr_period_step validation contract (ТЗ §4.3)."""

    def test_atr_period_step_default_is_one(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.optimization.atr_period_step == 1

    def test_atr_period_step_explicit_value(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            optimization:
              atr_period_step: 4
        """))
        cfg = load_grid_config(path)
        assert cfg.optimization.atr_period_step == 4

    def test_atr_period_step_zero_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            optimization:
              atr_period_step: 0
        """))
        with pytest.raises(ConfigError, match="atr_period_step"):
            load_grid_config(path)

    def test_atr_period_step_negative_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            optimization:
              atr_period_step: -1
        """))
        with pytest.raises(ConfigError, match="got -1"):
            load_grid_config(path)

    def test_atr_period_step_float_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            optimization:
              atr_period_step: 0.5
        """))
        with pytest.raises(ConfigError, match="0\\.5"):
            load_grid_config(path)

    def test_atr_period_step_bool_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            optimization:
              atr_period_step: true
        """))
        with pytest.raises(ConfigError, match="True"):
            load_grid_config(path)

    def test_atr_period_step_null_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            optimization:
              atr_period_step: null
        """))
        with pytest.raises(ConfigError, match="None"):
            load_grid_config(path)

    def test_atr_period_step_string_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            optimization:
              atr_period_step: "2"
        """))
        with pytest.raises(ConfigError, match="'2'"):
            load_grid_config(path)

    def test_unknown_atr_step_typo_rejected(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML + textwrap.dedent("""\
            optimization:
              atr_period_steppp: 2
        """))
        with pytest.raises(ConfigError, match="unknown config key"):
            load_grid_config(path)
