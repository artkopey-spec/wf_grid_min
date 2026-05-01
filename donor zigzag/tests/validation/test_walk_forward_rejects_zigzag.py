"""
Тест-защита: walk-forward/optimizer отказывается от zigzag и zigzag_and_volume
(план v2.0.1 §3.9, §6.4, FIX 9).

Zigzag — tester-only режим. validate_config должен бросать ConfigError
с совпадением "tester-only" для mode=zigzag и mode=zigzag_and_volume.
Дополнительно проверяется, что _TESTER_ONLY_MODES содержит оба режима
и что guard не задет более мягкими режимами.
"""
from __future__ import annotations

import pytest

from supertrend_optimizer.cli.validators import (
    _TESTER_ONLY_MODES,
    validate_config,
)
from supertrend_optimizer.utils.exceptions import ConfigError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_wf_cfg(filter_mode: str) -> dict:
    """Минимальный конфиг с walk_forward.enabled=True для тестирования через WF entry."""
    return {
        "optimization": {
            "atr_period_range": [10, 20],
            "multiplier_range": [1.0, 5.0],
            "multiplier_step": 0.5,
            "n_trials": 100,
            "n_jobs": 1,
        },
        "validation": {
            "warmup_period": 0,
            "walk_forward": {
                "enabled": True,
                "train_size": "6M",
                "test_size": "2M",
            },
        },
        "backtest": {
            "commission": 0.001,
            "early_exit_enabled": False,
            "annualization_factor": 252,
            "min_trades_required": 3,
        },
        "filters": {"mode": filter_mode},
    }


# ---------------------------------------------------------------------------
# 1. Базовые проверки — validate_config отвергает zigzag
# ---------------------------------------------------------------------------


class TestWalkForwardRejectsZigZag:

    def test_walk_forward_rejects_zigzag_mode(self):
        """mode=zigzag в WF-конфиге → ConfigError с 'tester-only'."""
        cfg = _minimal_wf_cfg("zigzag")
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(cfg)

    def test_walk_forward_rejects_zigzag_and_volume_mode(self):
        """mode=zigzag_and_volume в WF-конфиге → ConfigError с 'tester-only'."""
        cfg = _minimal_wf_cfg("zigzag_and_volume")
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(cfg)

    def test_error_message_mentions_zigzag_mode(self):
        """Сообщение об ошибке должно содержать имя режима."""
        cfg = _minimal_wf_cfg("zigzag")
        with pytest.raises(ConfigError, match="zigzag"):
            validate_config(cfg)

    def test_error_message_mentions_zigzag_and_volume_mode(self):
        cfg = _minimal_wf_cfg("zigzag_and_volume")
        with pytest.raises(ConfigError, match="zigzag_and_volume"):
            validate_config(cfg)


# ---------------------------------------------------------------------------
# 2. _TESTER_ONLY_MODES содержит оба zigzag-режима
# ---------------------------------------------------------------------------


class TestTesterOnlyModesConstant:

    def test_tester_only_modes_includes_zigzag(self):
        assert "zigzag" in _TESTER_ONLY_MODES

    def test_tester_only_modes_includes_zigzag_and_volume(self):
        assert "zigzag_and_volume" in _TESTER_ONLY_MODES

    def test_tester_only_modes_includes_amplitude(self):
        assert "amplitude" in _TESTER_ONLY_MODES

    def test_tester_only_modes_includes_amplitude_and_volume(self):
        assert "amplitude_and_volume" in _TESTER_ONLY_MODES


# ---------------------------------------------------------------------------
# 3. Разрешённые режимы НЕ блокируются zigzag-guard
# ---------------------------------------------------------------------------


class TestAllowedModesNotBlocked:

    def _try_validate(self, mode: str) -> str | None:
        """
        Пробует validate_config с указанным режимом.
        Возвращает None если guard не сработал, или строку ошибки если
        ConfigError с 'tester-only' (guard сработал неправомерно).
        Другие ConfigError (отсутствие секций) игнорируются — нас интересует
        только guard zigzag/amplitude.
        """
        cfg = _minimal_wf_cfg(mode)
        try:
            validate_config(cfg)
        except ConfigError as e:
            if "tester-only" in str(e):
                return str(e)
        return None

    def test_none_mode_not_blocked_by_zigzag_guard(self):
        err = self._try_validate("none")
        assert err is None, f"mode='none' неправомерно заблокирован guard: {err}"

    def test_volatility_mode_not_blocked_by_zigzag_guard(self):
        err = self._try_validate("volatility")
        assert err is None, f"mode='volatility' неправомерно заблокирован guard: {err}"

    def test_volume_mode_not_blocked_by_zigzag_guard(self):
        err = self._try_validate("volume")
        assert err is None, f"mode='volume' неправомерно заблокирован guard: {err}"

    def test_volatility_and_volume_mode_not_blocked_by_zigzag_guard(self):
        err = self._try_validate("volatility_and_volume")
        assert err is None, (
            f"mode='volatility_and_volume' неправомерно заблокирован guard: {err}"
        )


# ---------------------------------------------------------------------------
# 4. run_walk_forward вызывается через validate_config (WF entry point)
# ---------------------------------------------------------------------------


class TestWalkForwardEntryPointGuard:
    """
    Проверяем, что guard zigzag срабатывает на уровне validate_config,
    который используется перед run_walk_forward в CLI-pipeline.

    Прямой вызов run_walk_forward не включает validate_config — это
    ответственность CLI. Тест проверяет именно CLI-уровень через validate_config,
    фиксируя инвариант, что WF-конфиг с zigzag должен быть отклонён.
    """

    def test_validate_config_is_wf_entrypoint_guard(self):
        """
        Фиксация инварианта: validate_config отклоняет zigzag ДО любой
        WF-обработки (guard в строке ~49 validators.py).
        """
        from supertrend_optimizer.cli.validators import validate_config as vc
        import inspect
        source = inspect.getsource(vc)
        # Guard должен быть в начале функции (до _validate_optimization_section)
        guard_pos = source.find("tester-only")
        opt_pos = source.find("_validate_optimization_section")
        assert guard_pos < opt_pos, (
            "Guard 'tester-only' должен быть до _validate_optimization_section — "
            "порядок изменился, нужно проверить validators.py"
        )

    def test_zigzag_rejected_before_optimization_section_validates(self):
        """
        С неполным конфигом (нет optimization) + mode=zigzag →
        должен бросить ConfigError с 'tester-only', а не KeyError.
        """
        cfg = {"filters": {"mode": "zigzag"}}
        with pytest.raises(ConfigError, match="tester-only"):
            validate_config(cfg)
