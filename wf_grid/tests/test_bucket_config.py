"""
Unit tests for Этап 1: BucketConfig в schema.py + парсинг/валидация в loader.py.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest

from wf_grid.config.loader import ConfigError, load_grid_config
from wf_grid.config.schema import BucketConfig, GridConfig


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
# BucketConfig defaults
# ---------------------------------------------------------------------------

class TestBucketConfigDefaults:
    def test_bucket_config_defaults(self):
        bc = BucketConfig()
        assert bc.atr_bucket_step == 2
        assert bc.mult_bucket_step == 0.2
        assert bc.min_buckets_for_median == 5

    def test_grid_config_has_bucket_field(self):
        cfg = GridConfig(data=None)
        assert hasattr(cfg, "bucket")
        assert isinstance(cfg.bucket, BucketConfig)

    def test_grid_config_bucket_defaults(self):
        cfg = GridConfig(data=None)
        assert cfg.bucket.atr_bucket_step == 2
        assert cfg.bucket.mult_bucket_step == 0.2
        assert cfg.bucket.min_buckets_for_median == 5


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------

class TestBucketConfigFromYaml:
    def test_bucket_config_from_yaml(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  atr_bucket_step: 4
  mult_bucket_step: 0.5
  min_buckets_for_median: 3
"""
        path = _write_yaml(tmp_path, yaml_content)
        cfg = load_grid_config(path)
        assert cfg.bucket.atr_bucket_step == 4
        assert cfg.bucket.mult_bucket_step == 0.5
        assert cfg.bucket.min_buckets_for_median == 3

    def test_bucket_config_missing_yaml_uses_defaults(self, tmp_path):
        path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_grid_config(path)
        assert cfg.bucket.atr_bucket_step == 2
        assert cfg.bucket.mult_bucket_step == 0.2
        assert cfg.bucket.min_buckets_for_median == 5

    def test_partial_bucket_section_uses_defaults_for_missing(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  atr_bucket_step: 6
"""
        path = _write_yaml(tmp_path, yaml_content)
        cfg = load_grid_config(path)
        assert cfg.bucket.atr_bucket_step == 6
        assert cfg.bucket.mult_bucket_step == 0.2
        assert cfg.bucket.min_buckets_for_median == 5


# ---------------------------------------------------------------------------
# Validation — reject invalid values
# ---------------------------------------------------------------------------

class TestBucketConfigValidation:
    def test_atr_bucket_step_zero_rejected(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  atr_bucket_step: 0
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="atr_bucket_step"):
            load_grid_config(path)

    def test_atr_bucket_step_negative_rejected(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  atr_bucket_step: -1
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="atr_bucket_step"):
            load_grid_config(path)

    def test_mult_bucket_step_zero_rejected(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  mult_bucket_step: 0.0
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="mult_bucket_step"):
            load_grid_config(path)

    def test_mult_bucket_step_negative_rejected(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  mult_bucket_step: -0.1
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="mult_bucket_step"):
            load_grid_config(path)

    def test_min_buckets_for_median_zero_rejected(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  min_buckets_for_median: 0
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="min_buckets_for_median"):
            load_grid_config(path)

    def test_min_buckets_for_median_negative_rejected(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  min_buckets_for_median: -5
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="min_buckets_for_median"):
            load_grid_config(path)

    def test_atr_bucket_step_one_accepted(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  atr_bucket_step: 1
"""
        path = _write_yaml(tmp_path, yaml_content)
        cfg = load_grid_config(path)
        assert cfg.bucket.atr_bucket_step == 1

    def test_min_buckets_for_median_one_accepted(self, tmp_path):
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  min_buckets_for_median: 1
"""
        path = _write_yaml(tmp_path, yaml_content)
        cfg = load_grid_config(path)
        assert cfg.bucket.min_buckets_for_median == 1


# ---------------------------------------------------------------------------
# Compatibility warnings
# ---------------------------------------------------------------------------

class TestBucketStepCompatibilityWarnings:
    def test_warns_incompatible_steps(self, tmp_path, caplog):
        """mult_bucket_step=0.3 is not integer multiple of multiplier_step=0.2 → warning."""
        yaml_content = MINIMAL_VALID_YAML + """\
optimization:
  multiplier_step: 0.2
bucket:
  mult_bucket_step: 0.3
"""
        path = _write_yaml(tmp_path, yaml_content)
        with caplog.at_level(logging.WARNING, logger="wf_grid.config.loader"):
            cfg = load_grid_config(path)
        # caplog.text contains all log records as formatted strings
        assert "mult_bucket_step" in caplog.text or "integer multiple" in caplog.text, (
            f"Expected incompatibility warning, caplog.text={caplog.text!r}"
        )

    def test_compatible_steps_no_warning(self, tmp_path, caplog):
        """mult_bucket_step=0.2 is integer multiple of multiplier_step=0.1 → no warning."""
        yaml_content = MINIMAL_VALID_YAML + """\
optimization:
  multiplier_step: 0.1
bucket:
  mult_bucket_step: 0.2
"""
        path = _write_yaml(tmp_path, yaml_content)
        with caplog.at_level(logging.WARNING, logger="wf_grid.config.loader"):
            cfg = load_grid_config(path)
        # Should NOT emit incompatibility warning for compatible steps
        compat_warnings = [
            m for m in caplog.text.splitlines()
            if ("mult_bucket_step" in m or "integer multiple" in m) and "WARNING" in m
        ]
        assert not compat_warnings, f"Unexpected warning: {compat_warnings}"

    def test_warns_atr_bucket_step_larger_than_range(self, tmp_path, caplog):
        """atr_bucket_step > atr_period_range span → warning."""
        yaml_content = MINIMAL_VALID_YAML + """\
optimization:
  atr_period_range: [10, 12]
bucket:
  atr_bucket_step: 10
"""
        path = _write_yaml(tmp_path, yaml_content)
        with caplog.at_level(logging.WARNING, logger="wf_grid.config.loader"):
            cfg = load_grid_config(path)
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("atr_bucket_step" in m for m in warning_messages), (
            f"Expected ATR step warning, got: {warning_messages}"
        )


# ---------------------------------------------------------------------------
# atr_period_step + atr_bucket_step compatibility warnings (ТЗ §7.5)
# ---------------------------------------------------------------------------

class TestAtrPeriodStepBucketWarnings:

    def test_warns_atr_bucket_step_not_multiple_of_period_step(self, tmp_path, caplog):
        """step=3, bucket=4 → Warning A: not an integer multiple."""
        yaml_content = MINIMAL_VALID_YAML + """\
optimization:
  atr_period_step: 3
bucket:
  atr_bucket_step: 4
"""
        path = _write_yaml(tmp_path, yaml_content)
        with caplog.at_level(logging.WARNING, logger="wf_grid.config.loader"):
            load_grid_config(path)
        atr_step_warnings = [
            r.message for r in caplog.records
            if r.levelno == logging.WARNING and "atr_period_step" in r.message
        ]
        assert any("is not an integer multiple of" in m for m in atr_step_warnings), (
            f"Expected Warning A, got: {atr_step_warnings}"
        )

    def test_no_warning_when_period_step_is_one(self, tmp_path, caplog):
        """Default step=1, bucket=4 → Warning A must NOT fire (clean regression)."""
        yaml_content = MINIMAL_VALID_YAML + """\
bucket:
  atr_bucket_step: 4
"""
        path = _write_yaml(tmp_path, yaml_content)
        with caplog.at_level(logging.WARNING, logger="wf_grid.config.loader"):
            load_grid_config(path)
        atr_step_warnings = [
            r.message for r in caplog.records
            if r.levelno == logging.WARNING and "atr_period_step" in r.message
        ]
        assert not any("is not an integer multiple of" in m for m in atr_step_warnings), (
            f"Unexpected Warning A at step=1: {atr_step_warnings}"
        )

    def test_warns_period_step_greater_than_bucket_step(self, tmp_path, caplog):
        """step=5, bucket=2 → Warning B: at most one ATR grid point per bucket."""
        yaml_content = MINIMAL_VALID_YAML + """\
optimization:
  atr_period_step: 5
bucket:
  atr_bucket_step: 2
"""
        path = _write_yaml(tmp_path, yaml_content)
        with caplog.at_level(logging.WARNING, logger="wf_grid.config.loader"):
            load_grid_config(path)
        atr_step_warnings = [
            r.message for r in caplog.records
            if r.levelno == logging.WARNING and "atr_period_step" in r.message
        ]
        assert any("at most one" in m for m in atr_step_warnings), (
            f"Expected Warning B, got: {atr_step_warnings}"
        )
