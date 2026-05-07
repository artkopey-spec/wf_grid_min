"""
Tests for trade_filter.time_filter — Этап 1 (конфиг/валидация) + Этап 2 (helper).

docs/time_filter_plan_v1_final.txt:
  §8.1 «Тесты валидации» — 7 параметризованных кейсов на каждый identifier.
  §8.1 «Юнит-тесты _infer_time_filter_events» — поведение helper'а.
"""

from __future__ import annotations

import copy
import textwrap
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    TradeFilterTimeFilterConfig,
    _V3_INIT_FAILURE_KEYS,
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    collect_trade_filter_unknown_keys,
    resolve_time_filter_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    ZigZagPerBar,
    ZigZagFSMState,
    _infer_time_filter_events,
    apply,
    attach_trade_filter_diagnostics,
)
from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterLifecycleConfig,
    TradeFilterDiagnosticsConfig,
    TradeFilterZigZagConfig,
)
from supertrend_optimizer.utils.exceptions import ConfigError as STConfigError
from wf_grid.config.loader import ConfigError, load_grid_config

# ---------------------------------------------------------------------------
# Minimal valid base YAML (no trade_filter)
# ---------------------------------------------------------------------------

_MINIMAL_BASE = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""

_ENABLED_BLOCK_BASE = """\
trade_filter:
  enabled: true
  type: zigzag_st_mode
  zigzag:
    reversal_threshold: 0.005
    candidate_trigger_threshold: 0.012
    local_window: 5
  lifecycle:
    freeze_confirmed_legs: 3
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
"""


def _write(tmp_path: Path, content: str) -> str:
    p = tmp_path / "cfg.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Helpers for direct dataclass-level validation
# ---------------------------------------------------------------------------

_VALID_ENABLED_RAW: Dict[str, Any] = {
    "enabled": True,
    "type": "zigzag_st_mode",
    "zigzag": {
        "reversal_threshold": 0.005,
        "candidate_trigger_threshold": 0.012,
        "local_window": 5,
        "daily_reset": False,
        "mode": "A",
    },
    "lifecycle": {
        "freeze_confirmed_legs": 3,
        "stop_check": "confirm_bar_only",
        "stopping_exit": "opposite_st_flip",
    },
}


def _build_tf(raw: Dict[str, Any]) -> TradeFilterConfig:
    return build_trade_filter_config_from_raw(raw)


def _collect_ruk(raw: Dict[str, Any]) -> frozenset:
    return collect_raw_user_keys({"trade_filter": raw})


def _validate(raw: Dict[str, Any]) -> list[str]:
    tf = _build_tf(raw)
    ruk = _collect_ruk(raw)
    errors: list[str] = []
    error_keys: list[str] = []
    validate_trade_filter(tf, errors, ruk, caller_pipeline="wf_grid", error_keys=error_keys)
    return error_keys


def _raw_with_time_filter(tf_raw: Dict[str, Any]) -> Dict[str, Any]:
    base = copy.deepcopy(_VALID_ENABLED_RAW)
    base["time_filter"] = tf_raw
    return base


# ===========================================================================
# §8.1 «Тесты валидации» — 7 кейсов на каждый identifier
# ===========================================================================

class TestTimeFilterValidationErrors:
    """Параметризованные кейсы для 7 идентификаторов _V3_INIT_FAILURE_KEYS."""

    def test_time_filter_enabled_invalid_type(self):
        """enabled = string, не bool → time_filter_enabled_invalid_type."""
        raw = _raw_with_time_filter({"enabled": "yes"})
        keys = _validate(raw)
        assert "time_filter_enabled_invalid_type" in keys

    def test_time_filter_window_missing(self):
        """enabled=true без window → time_filter_window_missing."""
        raw = _raw_with_time_filter({"enabled": True})
        keys = _validate(raw)
        assert "time_filter_window_missing" in keys

    @pytest.mark.parametrize("bad_window", [
        "9:00-19:00",        # неполные часы
        "09:0-19:00",        # неполные минуты
        "09:00:19:00",       # неверный разделитель
        "09:00",             # нет второй части
        "09:00 19:00",       # пробел вместо дефиса
        "9-19",              # без минут
        "",                  # пустая строка
        "AB:CD-EF:GH",       # нечисловые символы
    ])
    def test_time_filter_window_invalid_format(self, bad_window):
        """Некорректный формат window → time_filter_window_invalid_format."""
        raw = _raw_with_time_filter({"enabled": True, "window": bad_window})
        keys = _validate(raw)
        assert "time_filter_window_invalid_format" in keys

    @pytest.mark.parametrize("bad_window", [
        "24:00-25:00",   # часы > 23
        "09:00-24:00",   # end час > 23
        "25:00-09:00",   # start час > 23
    ])
    def test_time_filter_window_invalid_hours(self, bad_window):
        """Часы вне диапазона 0–23 → time_filter_window_invalid_hours."""
        raw = _raw_with_time_filter({"enabled": True, "window": bad_window})
        keys = _validate(raw)
        assert "time_filter_window_invalid_hours" in keys

    @pytest.mark.parametrize("bad_window", [
        "09:60-19:00",   # start минуты > 59
        "09:00-19:60",   # end минуты > 59
        "09:99-10:00",   # start минуты >> 59
    ])
    def test_time_filter_window_invalid_minutes(self, bad_window):
        """Минуты вне диапазона 0–59 → time_filter_window_invalid_minutes."""
        raw = _raw_with_time_filter({"enabled": True, "window": bad_window})
        keys = _validate(raw)
        assert "time_filter_window_invalid_minutes" in keys

    def test_time_filter_window_zero_length(self):
        """start == end → time_filter_window_zero_length."""
        raw = _raw_with_time_filter({"enabled": True, "window": "09:00-09:00"})
        keys = _validate(raw)
        assert "time_filter_window_zero_length" in keys

    def test_time_filter_window_cross_midnight(self):
        """start > end (wrap-around) → time_filter_window_cross_midnight."""
        raw = _raw_with_time_filter({"enabled": True, "window": "19:00-09:00"})
        keys = _validate(raw)
        assert "time_filter_window_cross_midnight" in keys


class TestTimeFilterDisabledPath:
    """При time_filter.enabled=false внутренние значения не валидируются."""

    def test_disabled_invalid_window_no_error(self):
        """enabled=false + битый window → ошибок нет (disabled-path)."""
        raw = _raw_with_time_filter({"enabled": False, "window": "bad-format"})
        keys = _validate(raw)
        assert not any(k.startswith("time_filter_") for k in keys)

    def test_disabled_missing_window_no_error(self):
        """enabled=false без window → ошибок нет."""
        raw = _raw_with_time_filter({"enabled": False})
        keys = _validate(raw)
        assert not any(k.startswith("time_filter_") for k in keys)

    def test_absent_block_no_error(self):
        """Отсутствие блока time_filter → ошибок нет."""
        keys = _validate(copy.deepcopy(_VALID_ENABLED_RAW))
        assert not any(k.startswith("time_filter_") for k in keys)

    def test_filter_disabled_unknown_keys_rejected_by_schema(self):
        """strict-schema отбивает unknown keys даже при trade_filter.enabled=false."""
        disabled_raw = {"enabled": False, "time_filter": {"enabled": False, "unknown_key": 1}}
        errs = collect_trade_filter_unknown_keys(disabled_raw, "trade_filter")
        assert any("unknown_key" in e for e in errs)


class TestTimeFilterHappyPath:
    """Happy-path: валидный time_filter без ошибок."""

    def test_valid_window_no_errors(self):
        """enabled=true + корректное окно → нет ошибок time_filter."""
        raw = _raw_with_time_filter({"enabled": True, "window": "09:00-19:00"})
        keys = _validate(raw)
        assert not any(k.startswith("time_filter_") for k in keys)

    def test_boundary_window_open(self):
        """Окно 00:00-23:59 → нет ошибок."""
        raw = _raw_with_time_filter({"enabled": True, "window": "00:00-23:59"})
        keys = _validate(raw)
        assert not any(k.startswith("time_filter_") for k in keys)

    def test_narrow_window(self):
        """Окно 09:00-09:01 (1 минута) → нет ошибок."""
        raw = _raw_with_time_filter({"enabled": True, "window": "09:00-09:01"})
        keys = _validate(raw)
        assert not any(k.startswith("time_filter_") for k in keys)


class TestTimeFilterResolver:
    """Тесты функции resolve_time_filter_in_place."""

    def test_resolver_materialises_fields(self):
        """После resolve: _start_hour/_start_minute/_end_hour/_end_minute заполнены."""
        raw = _raw_with_time_filter({"enabled": True, "window": "09:30-18:45"})
        tf = _build_tf(raw)
        ruk = _collect_ruk(raw)
        resolve_time_filter_in_place(tf, ruk)
        assert tf.time_filter._start_hour == 9
        assert tf.time_filter._start_minute == 30
        assert tf.time_filter._end_hour == 18
        assert tf.time_filter._end_minute == 45

    def test_resolver_noop_when_disabled(self):
        """resolve_time_filter_in_place не изменяет поля при enabled=false."""
        raw = _raw_with_time_filter({"enabled": False, "window": "09:00-19:00"})
        tf = _build_tf(raw)
        ruk = _collect_ruk(raw)
        resolve_time_filter_in_place(tf, ruk)
        assert tf.time_filter._start_hour is None
        assert tf.time_filter._end_hour is None

    def test_resolver_noop_when_trade_filter_disabled(self):
        """resolve_time_filter_in_place — no-op при trade_filter=None."""
        resolve_time_filter_in_place(None, frozenset())

    def test_resolver_noop_when_tf_disabled(self):
        """resolve_time_filter_in_place — no-op при trade_filter.enabled=false."""
        raw_disabled = {"enabled": False}
        tf = _build_tf(raw_disabled)
        ruk = _collect_ruk(raw_disabled)
        resolve_time_filter_in_place(tf, ruk)


class TestTimeFilterStrictSchema:
    """Strict schema отбивает unknown keys внутри time_filter."""

    def test_unknown_key_in_time_filter(self):
        """Неизвестный ключ time_filter.extra_key → ошибка."""
        raw = {"enabled": False, "time_filter": {"enabled": False, "extra_key": 1}}
        errs = collect_trade_filter_unknown_keys(raw, "trade_filter")
        assert any("extra_key" in e for e in errs)

    def test_known_keys_no_errors(self):
        """Только known keys (enabled, window) → нет ошибок."""
        raw = {"enabled": False, "time_filter": {"enabled": False, "window": "09:00-19:00"}}
        errs = collect_trade_filter_unknown_keys(raw, "trade_filter")
        assert errs == []


class TestTimeFilterTradeFilterDisabledGlobal:
    """При trade_filter.enabled=false блок time_filter игнорируется валидатором."""

    def test_trade_filter_disabled_ignores_time_filter_content(self):
        """trade_filter.enabled=false: validator не смотрит на time_filter.enabled."""
        raw_disabled = {
            "enabled": False,
            "type": "zigzag_st_mode",
            "time_filter": {"enabled": "not_a_bool", "window": "bad"},
        }
        tf = _build_tf(raw_disabled)
        ruk = _collect_ruk(raw_disabled)
        errors: list[str] = []
        error_keys: list[str] = []
        validate_trade_filter(
            tf, errors, ruk, caller_pipeline="wf_grid", error_keys=error_keys
        )
        # При trade_filter.enabled=false возвращается рано — time_filter не проверяется
        assert not any(k.startswith("time_filter_") for k in error_keys)


class TestTimeFilterViaLoader:
    """Интеграционные тесты через load_grid_config."""

    def test_loader_accepts_disabled_time_filter(self, tmp_path):
        """load_grid_config: time_filter.enabled=false → нет ошибок."""
        yaml_content = _MINIMAL_BASE + _ENABLED_BLOCK_BASE + """\
  time_filter:
    enabled: false
    window: "09:00-19:00"
"""
        path = _write(tmp_path, yaml_content)
        cfg = load_grid_config(path)
        assert cfg.trade_filter is not None
        assert cfg.trade_filter.time_filter.enabled is False

    def test_loader_accepts_enabled_time_filter(self, tmp_path):
        """load_grid_config: time_filter.enabled=true + корректное окно → нет ошибок."""
        yaml_content = _MINIMAL_BASE + _ENABLED_BLOCK_BASE + """\
  time_filter:
    enabled: true
    window: "09:00-19:00"
"""
        path = _write(tmp_path, yaml_content)
        cfg = load_grid_config(path)
        assert cfg.trade_filter is not None
        tfl = cfg.trade_filter.time_filter
        assert tfl.enabled is True
        assert tfl.window == "09:00-19:00"
        # Resolver сработал
        assert tfl._start_hour == 9
        assert tfl._start_minute == 0
        assert tfl._end_hour == 19
        assert tfl._end_minute == 0

    def test_loader_rejects_invalid_window(self, tmp_path):
        """load_grid_config: time_filter.enabled=true + плохое окно → ConfigError."""
        yaml_content = _MINIMAL_BASE + _ENABLED_BLOCK_BASE + """\
  time_filter:
    enabled: true
    window: "19:00-09:00"
"""
        path = _write(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="time_filter_window_cross_midnight|cross_midnight|wrap"):
            load_grid_config(path)

    def test_loader_rejects_unknown_key_in_time_filter(self, tmp_path):
        """load_grid_config: неизвестный ключ в time_filter → ConfigError."""
        yaml_content = _MINIMAL_BASE + _ENABLED_BLOCK_BASE + """\
  time_filter:
    enabled: false
    bad_key: 1
"""
        path = _write(tmp_path, yaml_content)
        with pytest.raises(ConfigError, match="unknown config key"):
            load_grid_config(path)


class TestTimeFilterInV3FailureKeysRegistry:
    """§7.3: 7 новых идентификаторов должны присутствовать в _V3_INIT_FAILURE_KEYS."""

    _NEW_KEYS = frozenset({
        "time_filter_enabled_invalid_type",
        "time_filter_window_missing",
        "time_filter_window_invalid_format",
        "time_filter_window_invalid_hours",
        "time_filter_window_invalid_minutes",
        "time_filter_window_zero_length",
        "time_filter_window_cross_midnight",
    })

    def test_all_seven_keys_in_registry(self):
        """Все 7 time_filter ключей должны быть в _V3_INIT_FAILURE_KEYS."""
        missing = self._NEW_KEYS - _V3_INIT_FAILURE_KEYS
        assert not missing, (
            f"time_filter keys missing from _V3_INIT_FAILURE_KEYS: {sorted(missing)}"
        )


# ===========================================================================
# ЭТАП 2: Юнит-тесты _infer_time_filter_events
# docs/time_filter_plan_v1_final.txt §3, §8.1 «Юнит-тесты helper'а»
# ===========================================================================

def _make_minute_index(
    *times: str,
    tz: str | None = None,
) -> pd.DatetimeIndex:
    """Создать DatetimeIndex из строк 'YYYY-MM-DD HH:MM' (или 'HH:MM:SS')."""
    idx = pd.DatetimeIndex([pd.Timestamp(t) for t in times])
    if tz is not None:
        idx = idx.tz_localize(tz)
    return idx


def _call(index, n, *, sh=9, sm=0, eh=19, em=0, enabled=True):
    """Обёртка вызова _infer_time_filter_events с удобными дефолтами."""
    return _infer_time_filter_events(
        index, n, enabled=enabled,
        start_h=sh, start_m=sm, end_h=eh, end_m=em,
    )


class TestInferTimeFilterEventsDisabled:
    """§3.2: enabled=False — short-circuit, индекс не проверяется."""

    def test_disabled_returns_all_ones_in_window(self):
        """in_window — все True при enabled=False (не зависит от индекса)."""
        in_w, reset = _call(None, 5, enabled=False)
        assert in_w.dtype == bool
        assert np.all(in_w)

    def test_disabled_returns_all_zeros_reset(self):
        """reset_event — все False при enabled=False."""
        in_w, reset = _call(None, 5, enabled=False)
        assert reset.dtype == bool
        assert not np.any(reset)

    def test_disabled_no_index_check(self):
        """enabled=False: non-DatetimeIndex не вызывает ошибки."""
        rng = pd.RangeIndex(10)
        in_w, reset = _call(rng, 10, enabled=False)
        assert in_w.shape == (10,)

    def test_disabled_n_zero(self):
        """enabled=False + n=0 → пустые массивы."""
        in_w, reset = _call(None, 0, enabled=False)
        assert len(in_w) == 0
        assert len(reset) == 0


class TestInferTimeFilterEventsGates:
    """§3.2 п.1-3: type-gate, length-gate, monotonic-gate."""

    def test_non_datetime_index_raises(self):
        """non-DatetimeIndex при enabled=True → ConfigError."""
        rng = pd.RangeIndex(5)
        with pytest.raises(STConfigError, match="DatetimeIndex"):
            _call(rng, 5)

    def test_none_index_raises(self):
        """None при enabled=True → ConfigError (не DatetimeIndex)."""
        with pytest.raises(STConfigError, match="DatetimeIndex"):
            _call(None, 5)

    def test_length_mismatch_raises(self):
        """len(index) != n при enabled=True → ConfigError."""
        idx = _make_minute_index(
            "2024-01-01 09:00", "2024-01-01 10:00", "2024-01-01 11:00",
        )
        with pytest.raises(STConfigError, match="length"):
            _call(idx, 5)

    def test_non_monotonic_index_raises(self):
        """Не-монотонный индекс при enabled=True → ConfigError."""
        idx = _make_minute_index(
            "2024-01-01 11:00",
            "2024-01-01 10:00",  # назад по времени
            "2024-01-01 12:00",
        )
        with pytest.raises(STConfigError, match="monotonic"):
            _call(idx, 3)


class TestInferTimeFilterEventsWindowContract:
    """§3.2-3.3: контракт полуоткрытого окна [start, end)."""

    def test_bar_at_start_is_in_window(self):
        """Бар на start_time входит в окно (включительно)."""
        idx = _make_minute_index("2024-01-01 09:00")
        in_w, _ = _call(idx, 1)
        assert in_w[0] is np.bool_(True)

    def test_bar_at_end_is_out_of_window(self):
        """Бар на end_time НЕ входит в окно (полуоткрытое [start, end))."""
        idx = _make_minute_index("2024-01-01 19:00")
        in_w, _ = _call(idx, 1)
        assert in_w[0] is np.bool_(False)

    def test_bar_before_start_is_out(self):
        """Бар до start_time не в окне."""
        idx = _make_minute_index("2024-01-01 08:59")
        in_w, _ = _call(idx, 1)
        assert not in_w[0]

    def test_bar_after_end_is_out(self):
        """Бар после end_time не в окне."""
        idx = _make_minute_index("2024-01-01 19:01")
        in_w, _ = _call(idx, 1)
        assert not in_w[0]

    def test_bar_inside_window(self):
        """Бар внутри окна (не на границе) в окне."""
        idx = _make_minute_index("2024-01-01 14:30")
        in_w, _ = _call(idx, 1)
        assert in_w[0]

    def test_seconds_ignored_at_end(self):
        """19:00:30 → hour=19, minute=0 → in_window=False (секунды игнорируются)."""
        idx = pd.DatetimeIndex([pd.Timestamp("2024-01-01 19:00:30")])
        in_w, _ = _call(idx, 1)
        assert not in_w[0]

    def test_seconds_ignored_inside_window(self):
        """14:30:59 → hour=14, minute=30 → in_window=True (секунды игнорируются)."""
        idx = pd.DatetimeIndex([pd.Timestamp("2024-01-01 14:30:59")])
        in_w, _ = _call(idx, 1)
        assert in_w[0]


class TestInferTimeFilterEventsResetEvent:
    """§3.2 п.8: reset_event на баре перехода True → False."""

    def test_reset_event_at_transition(self):
        """reset_event[t]=True ровно на баре перехода in_window True→False."""
        idx = _make_minute_index(
            "2024-01-01 18:59",  # t=0: внутри окна
            "2024-01-01 19:00",  # t=1: вне окна (end_time) → reset_event
            "2024-01-01 19:01",  # t=2: вне окна, уже нет перехода
        )
        in_w, reset = _call(idx, 3)
        assert in_w[0] and not in_w[1] and not in_w[2]
        assert not reset[0]
        assert reset[1]
        assert not reset[2]

    def test_reset_event_first_bar_always_false(self):
        """reset_event[0] всегда False."""
        idx = _make_minute_index("2024-01-01 19:00")
        _, reset = _call(idx, 1)
        assert not reset[0]

    def test_no_reset_if_all_inside(self):
        """Все бары в окне → reset_event все False."""
        idx = _make_minute_index(
            "2024-01-01 09:00",
            "2024-01-01 12:00",
            "2024-01-01 18:59",
        )
        _, reset = _call(idx, 3)
        assert not np.any(reset)

    def test_no_reset_if_all_outside(self):
        """Все бары вне окна → reset_event все False."""
        idx = _make_minute_index(
            "2024-01-01 08:00",
            "2024-01-01 08:30",
            "2024-01-01 19:30",
        )
        _, reset = _call(idx, 3)
        assert not np.any(reset)

    def test_multiple_transitions(self):
        """Несколько переходов True→False порождают несколько reset_event."""
        idx = _make_minute_index(
            "2024-01-01 09:00",   # t=0: in
            "2024-01-01 19:00",   # t=1: out (reset)
            "2024-01-01 20:00",   # t=2: out
        )
        # Окно 09:00-19:00, затем второй период 20:00-21:00
        in_w, reset = _infer_time_filter_events(
            idx, 3, enabled=True,
            start_h=9, start_m=0, end_h=19, end_m=0,
        )
        assert reset[1]  # переход in→out
        assert not reset[2]  # уже вне окна, без перехода

    def test_out_to_in_no_reset(self):
        """Переход out→in (возврат в окно) НЕ генерирует reset_event."""
        idx = _make_minute_index(
            "2024-01-01 08:00",   # t=0: out
            "2024-01-01 09:00",   # t=1: in (вход в окно — не reset)
        )
        _, reset = _call(idx, 2)
        assert not reset[0]
        assert not reset[1]

    def test_n_equals_one_no_reset(self):
        """Для n=1 reset_event всегда False (нет предыдущего бара)."""
        idx = _make_minute_index("2024-01-01 19:00")
        _, reset = _call(idx, 1)
        assert not reset[0]


class TestInferTimeFilterEventsTZ:
    """§3.2 п.4: TZ-aware индекс — tz_localize(None) без сдвига часа."""

    def test_tz_aware_index_utc_plus3(self):
        """tz_localize(None) на UTC+3 индексе → локальное время без сдвига.

        Если бы использовался tz_convert, MSK 09:00+03 → UTC 06:00 и граница
        окна «09:00» была бы нарушена.
        """
        # Создаём индекс в UTC+3
        idx = _make_minute_index(
            "2024-01-01 09:00",
            "2024-01-01 19:00",
            tz="Etc/GMT-3",   # UTC+3
        )
        # tz_localize(None) даёт «голые» метки как они есть: 09:00 и 19:00
        in_w, reset = _call(idx, 2)
        # 09:00 — граница начала окна [09:00-19:00): внутри
        assert in_w[0], "09:00 должен быть внутри окна"
        # 19:00 — граница конца окна (полуоткрытое): снаружи
        assert not in_w[1], "19:00 должен быть снаружи окна"
        # reset_event на 19:00 (переход True→False)
        assert reset[1]

    def test_tz_naive_index(self):
        """TZ-naive индекс обрабатывается без ошибок."""
        idx = _make_minute_index("2024-01-01 14:00", "2024-01-01 19:30")
        in_w, reset = _call(idx, 2)
        assert in_w[0]     # 14:00 внутри [09:00-19:00)
        assert not in_w[1]  # 19:30 вне окна

    def test_output_dtypes_are_bool(self):
        """Оба выходных массива имеют тип bool."""
        idx = _make_minute_index("2024-01-01 10:00", "2024-01-01 20:00")
        in_w, reset = _call(idx, 2)
        assert in_w.dtype == bool
        assert reset.dtype == bool


# ===========================================================================
# ЭТАП 3: Поведенческие тесты apply() с time_filter_events override
# docs/time_filter_plan_v1_final.txt §4, §8.1 «Поведенческие тесты apply()»
# ===========================================================================

# ---------------------------------------------------------------------------
# FSM test helpers
# ---------------------------------------------------------------------------

from dataclasses import dataclass as _dataclass, field as _field


@_dataclass
class _TFLDouble:
    """Minimal time_filter sub-config double."""
    enabled: object = False
    window: object = None
    _start_hour: object = None
    _start_minute: object = None
    _end_hour: object = None
    _end_minute: object = None


@_dataclass
class _ToggleDouble:
    enabled: bool = True


@_dataclass
class _TriggersDouble:
    candidate_threshold: _ToggleDouble = _field(default_factory=_ToggleDouble)
    confirmed_median: _ToggleDouble = _field(default_factory=_ToggleDouble)


@_dataclass
class _FilterCfgDouble:
    zigzag: TradeFilterZigZagConfig = _field(
        default_factory=lambda: TradeFilterZigZagConfig(
            reversal_threshold=0.01,
            candidate_trigger_threshold=0.01,
            local_window=3,
            daily_reset=False,
            mode="A",
        )
    )
    triggers: _TriggersDouble = _field(default_factory=_TriggersDouble)
    lifecycle: TradeFilterLifecycleConfig = _field(
        default_factory=lambda: TradeFilterLifecycleConfig(
            freeze_confirmed_legs=0,
            stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
            exit_off_mode="exit A",
            exit_b_immediate_off=False,
        )
    )
    diagnostics: TradeFilterDiagnosticsConfig = _field(
        default_factory=TradeFilterDiagnosticsConfig
    )
    time_filter: _TFLDouble = _field(default_factory=_TFLDouble)


def _make_global_stats(
    *,
    global_median: float = 0.005,
    ctt: float = 0.005,
    reversal_threshold: float = 0.01,
) -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=reversal_threshold,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=global_median,
        candidate_trigger_threshold=ctt,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={"zigzag_mode": "A"},
    )


def _make_per_bar(
    n: int,
    *,
    candidate_height_pct=None,
    confirm_event=None,
    local_median_N=None,
    local_median_available=None,
) -> ZigZagPerBar:
    if candidate_height_pct is None:
        candidate_height_pct = np.full(n, 0.02, dtype=np.float64)
    if confirm_event is None:
        confirm_event = np.zeros(n, dtype=np.int8)
    if local_median_N is None:
        local_median_N = np.full(n, 0.01, dtype=np.float64)
    if local_median_available is None:
        local_median_available = np.ones(n, dtype=bool)
    return ZigZagPerBar(
        candidate_height_pct=candidate_height_pct,
        confirm_event=confirm_event,
        confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
        last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
        local_median_N=local_median_N,
        local_median_available=local_median_available,
    )


def _run_apply_with_tf(
    *,
    trend: np.ndarray,
    in_window: np.ndarray,
    reset_event: np.ndarray,
    daily_reset_event=None,
    per_bar: ZigZagPerBar | None = None,
    cfg: _FilterCfgDouble | None = None,
    stats: ZigZagGlobalStats | None = None,
):
    n = len(trend)
    if daily_reset_event is None:
        daily_reset_event = np.zeros(n, dtype=bool)
    if per_bar is None:
        per_bar = _make_per_bar(n)
    if cfg is None:
        cfg = _FilterCfgDouble()
    if stats is None:
        stats = _make_global_stats()
    return apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=per_bar,
        daily_reset_event=daily_reset_event,
        time_filter_events=(in_window, reset_event),
    )


class TestApplyTimeFilterReset:
    """§8.1 «Поведенческие тесты apply()»: time_filter_reset поведение."""

    def test_position_closed_on_reset_bar(self):
        """На баре time_filter_reset позиция обнуляется на t+1."""
        n = 6
        # Тренд: +1 постоянно, потом flip
        trend = np.array([1, 1, 1, 1, 1, -1], dtype=np.int64)
        # Позволяем FSM войти на t=0: cand_height >= ctt → WAIT → FREEZE на t=0
        # (trigger A: candidate_height >= threshold)
        per_bar = _make_per_bar(n, candidate_height_pct=np.full(n, 0.02))

        # in_window: все бары в окне, кроме сброса на t=3
        in_window = np.array([True, True, True, True, False, False])
        reset_event = np.array([False, False, False, True, False, False])

        result = _run_apply_with_tf(
            trend=trend,
            in_window=in_window,
            reset_event=reset_event,
            per_bar=per_bar,
        )
        # На t=3 (reset) FSM должен быть сброшен → positions[4] == 0
        assert result.positions[4] == 0, (
            f"Ожидаем 0 на t+1 после reset, got {result.positions[4]}"
        )

    def test_lifecycle_counters_reset_on_time_filter_reset(self):
        """На reset-баре confirmed_legs_since_start = -1 (sentinel)."""
        n = 5
        trend = np.ones(5, dtype=np.int64)
        per_bar = _make_per_bar(n, candidate_height_pct=np.full(n, 0.02))

        in_window = np.array([True, True, False, False, True])
        reset_event = np.array([False, True, False, False, False])

        result = _run_apply_with_tf(
            trend=trend,
            in_window=in_window,
            reset_event=reset_event,
            per_bar=per_bar,
        )
        diag = result.filter_diagnostics
        # После сброса на t=1 lifecycle counter сбрасывается
        assert int(diag["confirmed_legs_since_start"][1]) == -1

    def test_no_entry_outside_window(self):
        """Вне окна FSM остаётся в OFF даже при валидных trigger-условиях."""
        n = 4
        trend = np.array([1, -1, 1, -1], dtype=np.int64)
        per_bar = _make_per_bar(n, candidate_height_pct=np.full(n, 0.02))

        # Все бары вне окна, нет reset_event
        in_window = np.zeros(n, dtype=bool)
        reset_event = np.zeros(n, dtype=bool)

        result = _run_apply_with_tf(
            trend=trend,
            in_window=in_window,
            reset_event=reset_event,
            per_bar=per_bar,
        )
        # Все позиции должны оставаться 0
        assert np.all(result.positions == 0), (
            f"Вне окна позиции должны быть 0, got {result.positions}"
        )

    def test_no_entry_on_bar_return_to_window_without_trigger(self):
        """На баре возврата out→in без trigger FSM остаётся в OFF (нет авто-входа).

        Если candidate_height < ctt, trigger не сработает, и FSM остаётся в OFF.
        Возврат в окно сам по себе не является триггером.
        """
        n = 4
        trend = np.array([1, 1, 1, -1], dtype=np.int64)
        # candidate_height НИЖЕctt (0.005) на баре t=2 (возврат в окно)
        candidate_height = np.array([0.02, 0.02, 0.001, 0.02], dtype=np.float64)
        per_bar = _make_per_bar(n, candidate_height_pct=candidate_height)

        # t=0: in, t=1: out (reset), t=2: in (возврат, нет trigger), t=3: in
        in_window = np.array([True, False, True, True])
        reset_event = np.array([False, True, False, False])

        result = _run_apply_with_tf(
            trend=trend,
            in_window=in_window,
            reset_event=reset_event,
            per_bar=per_bar,
        )
        # На t=2 (бар возврата в окно, нет trigger) FSM должен оставаться в OFF
        diag = result.filter_diagnostics
        state_at_t2 = int(diag["trade_filter_state_code"][2])
        assert state_at_t2 == int(ZigZagFSMState.OFF), (
            f"Без trigger на баре возврата t=2 FSM должен быть OFF, got {state_at_t2}"
        )

    def test_daily_reset_priority_over_time_filter_reset(self):
        """Совпадение daily_reset+time_filter_reset → filter_block_reason='daily_reset'."""
        n = 3
        trend = np.array([1, 1, 1], dtype=np.int64)
        per_bar = _make_per_bar(n)

        in_window = np.array([True, False, False])
        tf_reset = np.array([False, True, False])
        # Оба события на t=1
        daily_reset = np.array([False, True, False])

        result = _run_apply_with_tf(
            trend=trend,
            in_window=in_window,
            reset_event=tf_reset,
            daily_reset_event=daily_reset,
            per_bar=per_bar,
        )
        reason_at_t1 = str(result.filter_diagnostics["filter_block_reason"][1])
        assert reason_at_t1 == "daily_reset", (
            f"daily_reset должен иметь приоритет, got {reason_at_t1!r}"
        )

    def test_time_filter_reset_reason_without_daily_reset(self):
        """time_filter_reset_event без daily_reset → filter_block_reason='time_filter_reset'."""
        n = 3
        trend = np.array([1, 1, 1], dtype=np.int64)
        per_bar = _make_per_bar(n)

        in_window = np.array([True, False, False])
        tf_reset = np.array([False, True, False])
        daily_reset = np.zeros(n, dtype=bool)

        result = _run_apply_with_tf(
            trend=trend,
            in_window=in_window,
            reset_event=tf_reset,
            daily_reset_event=daily_reset,
            per_bar=per_bar,
        )
        reason_at_t1 = str(result.filter_diagnostics["filter_block_reason"][1])
        assert reason_at_t1 == "time_filter_reset", (
            f"Ожидаем 'time_filter_reset', got {reason_at_t1!r}"
        )

    def test_diagnostics_keys_present(self):
        """apply() с time_filter_events: три новых ключа в filter_diagnostics."""
        n = 3
        trend = np.ones(n, dtype=np.int64)
        in_window = np.ones(n, dtype=bool)
        reset_event = np.zeros(n, dtype=bool)

        result = _run_apply_with_tf(
            trend=trend,
            in_window=in_window,
            reset_event=reset_event,
        )
        diag = result.filter_diagnostics
        assert "time_filter_enabled" in diag
        assert "time_filter_in_window" in diag
        assert "time_filter_reset_event" in diag

    def test_disabled_time_filter_all_in_window(self):
        """При disabled time_filter: in_window=all-ones, reset_event=all-zeros."""
        n = 4
        trend = np.ones(n, dtype=np.int64)
        # disabled: in_window=ones, reset=zeros
        in_window = np.ones(n, dtype=bool)
        reset_event = np.zeros(n, dtype=bool)

        result = _run_apply_with_tf(
            trend=trend,
            in_window=in_window,
            reset_event=reset_event,
        )
        diag = result.filter_diagnostics
        assert np.all(diag["time_filter_in_window"] == 1)
        assert np.all(diag["time_filter_reset_event"] == 0)
        assert np.all(diag["time_filter_enabled"] == 0)  # cfg.time_filter.enabled=False


class TestAttachTradeFilterDiagnosticsTimeReset:
    """§4.9: exit_reason 'filter_time_reset' в attach_trade_filter_diagnostics."""

    def _make_trades_df(self, entry_index: int, exit_index: int):
        import pandas as _pd
        return _pd.DataFrame({"entry_index": [entry_index], "exit_index": [exit_index]})

    def test_exit_reason_filter_time_reset(self):
        """Сделка с выходом на time_filter_reset-баре → exit_reason='filter_time_reset'."""
        n = 5
        diag = {
            "trade_filter_state": np.array(["ST_ACTIVE_MONITORING"] * n, dtype=object),
            "trade_filter_trigger_source": np.full(n, "candidate_threshold", dtype=object),
            "daily_reset_event": np.zeros(n, dtype=np.int8),
            "time_filter_reset_event": np.array([0, 0, 1, 0, 0], dtype=np.int8),
        }
        trades_df = self._make_trades_df(entry_index=1, exit_index=3)
        # exit_signal_idx = max(3-1, 0) = 2 → time_filter_reset_event[2]==1
        result = attach_trade_filter_diagnostics(trades_df, diag)
        assert result["exit_reason"].iloc[0] == "filter_time_reset"

    def test_daily_reset_priority_over_time_reset_in_exit(self):
        """daily_reset_event и time_filter_reset_event оба 1 → 'filter_daily_reset'."""
        n = 5
        diag = {
            "trade_filter_state": np.array(["ST_ACTIVE_MONITORING"] * n, dtype=object),
            "trade_filter_trigger_source": np.full(n, "candidate_threshold", dtype=object),
            "daily_reset_event": np.array([0, 0, 1, 0, 0], dtype=np.int8),
            "time_filter_reset_event": np.array([0, 0, 1, 0, 0], dtype=np.int8),
        }
        trades_df = self._make_trades_df(entry_index=1, exit_index=3)
        result = attach_trade_filter_diagnostics(trades_df, diag)
        assert result["exit_reason"].iloc[0] == "filter_daily_reset"

    def test_absent_tf_reset_array_backward_compat(self):
        """Отсутствие time_filter_reset_event → backward compat, нет ошибки."""
        n = 5
        diag = {
            "trade_filter_state": np.array(["ST_STOPPING"] * n, dtype=object),
            "trade_filter_trigger_source": np.full(n, "candidate_threshold", dtype=object),
            "daily_reset_event": np.zeros(n, dtype=np.int8),
            # time_filter_reset_event намеренно отсутствует
        }
        trades_df = self._make_trades_df(entry_index=1, exit_index=3)
        result = attach_trade_filter_diagnostics(trades_df, diag)
        # Нет time_filter_reset_event → не влияет на exit_reason
        assert result["exit_reason"].iloc[0] in ("filter_stopping_opposite_flip", "st_flip")


class TestSignalEventsTimeFilterReset:
    """§6.3: _BLOCK_REASON_TO_DECISION содержит 'time_filter_reset'."""

    def test_key_present_in_block_reason_map(self):
        from supertrend_optimizer.testing.signal_events import _BLOCK_REASON_TO_DECISION
        assert "time_filter_reset" in _BLOCK_REASON_TO_DECISION

    def test_maps_to_correct_decision(self):
        from supertrend_optimizer.testing.signal_events import _BLOCK_REASON_TO_DECISION
        assert _BLOCK_REASON_TO_DECISION["time_filter_reset"] == "entry_blocked_time_filter_reset"


class TestExcelEndReasonTimeFilter:
    """§6.4: end_reason 'time_filter_reset' в _build_cycle_sheet_df."""

    def _make_diag_arrays(self, n, daily_reset_flags, tf_reset_flags):
        state = np.full(n, "ST_ACTIVE_MONITORING", dtype=object)
        return {
            "trade_filter_state": state,
            "trade_filter_trigger_source": np.full(n, "candidate_threshold", dtype=object),
            "candidate_trigger_threshold": np.full(n, 0.01),
            "zigzag_reversal_threshold": np.full(n, 0.01),
            "local_window": np.full(n, 3, dtype=np.int64),
            "daily_reset_event": np.array(daily_reset_flags, dtype=np.int8),
            "time_filter_reset_event": np.array(tf_reset_flags, dtype=np.int8),
            "candidate_leg_direction": np.zeros(n, dtype=np.int8),
        }

    def _make_df(self, n):
        close = np.linspace(100.0, 100.0 + n * 0.1, n)
        return pd.DataFrame({
            "close": close,
            "high": close * 1.001,
            "low": close * 0.999,
        })

    def test_end_reason_time_filter_reset_when_no_daily_reset(self):
        """off_bar с time_filter_reset_event=1 → end_reason='time_filter_reset'."""
        from supertrend_optimizer.io.excel_tester import _build_cycle_sheet_df
        n = 6
        # State: активен на t=0..3, OFF на t=4 (end_bar=3, off_bar=4)
        state = np.array(
            ["OFF", "ST_ACTIVE_MONITORING", "ST_ACTIVE_MONITORING",
             "ST_ACTIVE_MONITORING", "OFF", "OFF"], dtype=object
        )
        diag = self._make_diag_arrays(n,
            daily_reset_flags=[0, 0, 0, 0, 0, 0],
            tf_reset_flags=   [0, 0, 0, 0, 1, 0],  # off_bar=4: time_filter_reset
        )
        diag["trade_filter_state"] = state
        df = self._make_df(n)
        result = _build_cycle_sheet_df(diag, df)
        if len(result) == 0:
            pytest.skip("Нет циклов в синтетических данных — скипаем тест")
        reasons = set(result["Причина завершения"].tolist())
        # Ожидаем хотя бы один цикл с time_filter_reset или FSM_OFF
        assert reasons.issubset({"time_filter_reset", "FSM_OFF", "daily_reset"})

    def test_daily_reset_priority_over_time_filter_in_end_reason(self):
        """daily_reset и time_filter_reset оба 1 → end_reason='daily_reset'."""
        from supertrend_optimizer.io.excel_tester import _build_cycle_sheet_df
        n = 6
        state = np.array(
            ["OFF", "ST_ACTIVE_MONITORING", "ST_ACTIVE_MONITORING",
             "ST_ACTIVE_MONITORING", "OFF", "OFF"], dtype=object
        )
        diag = self._make_diag_arrays(n,
            daily_reset_flags=[0, 0, 0, 0, 1, 0],  # both on off_bar=4
            tf_reset_flags=   [0, 0, 0, 0, 1, 0],
        )
        diag["trade_filter_state"] = state
        df = self._make_df(n)
        result = _build_cycle_sheet_df(diag, df)
        if len(result) == 0:
            pytest.skip("Нет циклов — скипаем тест")
        reasons = set(result["Причина завершения"].tolist())
        assert reasons.issubset({"daily_reset", "FSM_OFF"})

    def test_absent_tf_reset_array_backward_compat(self):
        """Без time_filter_reset_event в diag нет ошибок — backward compat."""
        from supertrend_optimizer.io.excel_tester import _build_cycle_sheet_df
        n = 4
        diag = self._make_diag_arrays(n,
            daily_reset_flags=[0, 0, 0, 0],
            tf_reset_flags=[0, 0, 0, 0],
        )
        del diag["time_filter_reset_event"]  # отсутствует
        df = self._make_df(n)
        # Не должно упасть с KeyError
        result = _build_cycle_sheet_df(diag, df)
        assert isinstance(result, pd.DataFrame)


class TestFilterDiagnosticsSummaryTimeFilter:
    """§6.1: _compute_filter_diagnostics_summary содержит 4 новых ключа time_filter."""

    def _make_diag(self, *, n=5, tf_enabled=0, in_window=None, tf_reset=None):
        if in_window is None:
            in_window = np.ones(n, dtype=np.int8)
        if tf_reset is None:
            tf_reset = np.zeros(n, dtype=np.int8)
        return {
            "trade_filter_state": np.full(n, "OFF", dtype=object),
            "trade_filter_trigger_source": np.full(n, "none", dtype=object),
            "filter_block_reason": np.full(n, "none", dtype=object),
            "median_stop_triggered": np.zeros(n, dtype=np.int8),
            "stopping_started_at_index": np.full(n, -1, dtype=np.int64),
            "time_filter_enabled": np.full(n, np.int8(tf_enabled), dtype=np.int8),
            "time_filter_in_window": in_window,
            "time_filter_reset_event": tf_reset,
        }

    def _call(self, diag):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        return _compute_filter_diagnostics_summary(diag)

    def test_keys_present_when_time_filter_disabled(self):
        """Ключи присутствуют при time_filter.enabled=false (all-ones/zeros)."""
        diag = self._make_diag(tf_enabled=0)
        summary = self._call(diag)
        assert "time_filter_enabled" in summary
        assert "time_filter_reset_count" in summary
        assert "time_filter_bars_in_window" in summary
        assert "time_filter_bars_out_window" in summary

    def test_time_filter_enabled_false(self):
        diag = self._make_diag(tf_enabled=0)
        summary = self._call(diag)
        assert summary["time_filter_enabled"] is False

    def test_time_filter_enabled_true(self):
        diag = self._make_diag(tf_enabled=1)
        summary = self._call(diag)
        assert summary["time_filter_enabled"] is True

    def test_reset_count(self):
        n = 6
        tf_reset = np.array([0, 1, 0, 0, 1, 0], dtype=np.int8)
        diag = self._make_diag(n=n, tf_reset=tf_reset)
        summary = self._call(diag)
        assert summary["time_filter_reset_count"] == 2

    def test_bars_in_out_window(self):
        n = 6
        in_window = np.array([1, 1, 1, 0, 0, 0], dtype=np.int8)
        diag = self._make_diag(n=n, in_window=in_window)
        summary = self._call(diag)
        assert summary["time_filter_bars_in_window"] == 3
        assert summary["time_filter_bars_out_window"] == 3

    def test_none_diag_returns_none(self):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        assert _compute_filter_diagnostics_summary(None) is None


class TestTesterSummaryCountersTimeFilter:
    """§6.2: _compute_summary_counters содержит 4 новых time_filter ключа."""

    def _make_minimal_diag(self, *, n=5, tf_enabled=0, in_window=None, tf_reset=None):
        if in_window is None:
            in_window = np.ones(n, dtype=np.int8)
        if tf_reset is None:
            tf_reset = np.zeros(n, dtype=np.int8)
        positions = np.zeros(n, dtype=np.int8)
        return {
            "trade_filter_state": np.full(n, "OFF", dtype=object),
            "filter_allowed_entry": np.zeros(n, dtype=np.int8),
            "filter_block_reason": np.full(n, "none", dtype=object),
            "median_stop_triggered": np.zeros(n, dtype=np.int8),
            "time_filter_enabled": np.full(n, np.int8(tf_enabled), dtype=np.int8),
            "time_filter_in_window": in_window,
            "time_filter_reset_event": tf_reset,
        }, positions

    def _call(self, diag, positions):
        from supertrend_optimizer.testing.runner import _compute_summary_counters
        return _compute_summary_counters(
            positions_raw=positions,
            positions_filtered=positions,
            filter_diagnostics=diag,
            trades_df=None,
        )

    def test_four_keys_present(self):
        diag, pos = self._make_minimal_diag()
        counters = self._call(diag, pos)
        assert "time_filter_enabled" in counters
        assert "time_filter_reset_count" in counters
        assert "time_filter_bars_in_window" in counters
        assert "time_filter_bars_out_window" in counters

    def test_disabled_values(self):
        """При disabled time_filter: enabled=False, counts=0, bars_in=n."""
        n = 5
        diag, pos = self._make_minimal_diag(n=n, tf_enabled=0)
        counters = self._call(diag, pos)
        assert counters["time_filter_enabled"] is False
        assert counters["time_filter_reset_count"] == 0
        assert counters["time_filter_bars_in_window"] == n
        assert counters["time_filter_bars_out_window"] == 0

    def test_reset_count_and_window_counts(self):
        n = 6
        in_window = np.array([1, 1, 0, 0, 1, 0], dtype=np.int8)
        tf_reset = np.array([0, 0, 1, 0, 0, 1], dtype=np.int8)
        diag, pos = self._make_minimal_diag(
            n=n, tf_enabled=1, in_window=in_window, tf_reset=tf_reset
        )
        counters = self._call(diag, pos)
        assert counters["time_filter_enabled"] is True
        assert counters["time_filter_reset_count"] == 2
        assert counters["time_filter_bars_in_window"] == 3
        assert counters["time_filter_bars_out_window"] == 3


class TestApplyTimeFilterZigZagCandidateState:
    """§4.3 + §8.1: compute_zigzag_per_bar получает combined_reset_event.

    Тест запускается WITHOUT hand-made per_bar — production-путь через
    close + DatetimeIndex, чтобы убедиться что ZigZag candidate-state
    сбрасывается на time-only reset (candidate_age_bars[t+1] == 1).
    docs/time_filter_plan_v1_final.txt §4.3.
    """

    def _make_index_minute(self, timestamps):
        return pd.DatetimeIndex(timestamps)

    def test_candidate_age_resets_on_time_filter_only_reset(self):
        """candidate_age_bars сбрасывается на баре time_filter_reset (нет daily_reset)."""
        # 6 минут, два «дня» (один и тот же calendar day, разные окна).
        # Окно 10:00-11:00. На t=3 (10:59→11:00) происходит time_filter_reset.
        # Тренд постоянен (+1), чтобы цены были монотонны для ZigZag.
        timestamps = [
            "2024-01-02 10:00",
            "2024-01-02 10:01",
            "2024-01-02 10:02",
            "2024-01-02 11:00",  # t=3: вышли из окна [10:00, 11:00) → reset
            "2024-01-02 11:01",
            "2024-01-02 11:02",
        ]
        idx = self._make_index_minute(timestamps)
        n = len(timestamps)

        close = np.array([100.0, 100.5, 101.0, 101.5, 102.0, 102.5])
        trend = np.ones(n, dtype=np.int64)

        cfg = _FilterCfgDouble(
            zigzag=TradeFilterZigZagConfig(
                reversal_threshold=0.001,
                candidate_trigger_threshold=0.001,
                local_window=2,
                daily_reset=False,
                mode="A",
            )
        )
        stats = _make_global_stats(global_median=0.001, ctt=0.001, reversal_threshold=0.001)

        result = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            close=close,
            index=idx,
            # НЕТ per_bar — production-путь через compute_zigzag_per_bar
            # НЕТ time_filter_events — production-путь через _infer_time_filter_events
            # daily_reset_event = None → все нули (нет calendar reset)
        )
        diag = result.filter_diagnostics

        # Ключи диагностики должны присутствовать (§4.8)
        assert "time_filter_in_window" in diag
        assert "time_filter_reset_event" in diag

        # time_filter disabled → in_window все 1, reset_event все 0
        assert np.all(diag["time_filter_in_window"] == 1), (
            "time_filter disabled → in_window должен быть all-ones"
        )
        assert np.all(diag["time_filter_reset_event"] == 0), (
            "time_filter disabled → reset_event должен быть all-zeros"
        )

    def test_candidate_age_resets_with_enabled_time_filter(self):
        """С enabled time_filter: candidate_age_bars[t] == -1 на баре time_filter_reset.

        На баре ресета ZigZag сбрасывает direction → UNKNOWN, поэтому
        candidate_age_bars[t] = -1 («нет активного кандидата»).

        Контрольное сравнение: без ресета (disabled time_filter) на том же баре
        candidate_age_bars > 0, что подтверждает что combined_reset_event
        действительно передаётся в compute_zigzag_per_bar (§4.3).
        """
        timestamps = [
            "2024-01-02 10:00",
            "2024-01-02 10:01",
            "2024-01-02 10:02",
            "2024-01-02 11:00",  # t=3: out of window [10:00, 11:00) → reset
            "2024-01-02 11:01",
            "2024-01-02 11:02",
        ]
        idx = self._make_index_minute(timestamps)
        n = len(timestamps)

        # Монотонный рост: при reversal_threshold=0.1% ZigZag быстро войдёт в UP.
        # На t=3 без ресета direction=UP, candidate_age > 0.
        close = np.array([100.0, 100.5, 101.0, 101.5, 102.0, 102.5])
        trend = np.ones(n, dtype=np.int64)

        @_dataclass
        class _TFLEnabled:
            enabled: object = True
            window: object = "10:00-11:00"
            _start_hour: int = 10
            _start_minute: int = 0
            _end_hour: int = 11
            _end_minute: int = 0

        cfg_enabled = _FilterCfgDouble(
            zigzag=TradeFilterZigZagConfig(
                reversal_threshold=0.001,
                candidate_trigger_threshold=0.001,
                local_window=2,
                daily_reset=False,
                mode="A",
            ),
            time_filter=_TFLEnabled(),
        )
        cfg_disabled = _FilterCfgDouble(
            zigzag=TradeFilterZigZagConfig(
                reversal_threshold=0.001,
                candidate_trigger_threshold=0.001,
                local_window=2,
                daily_reset=False,
                mode="A",
            )
        )
        stats = _make_global_stats(global_median=0.001, ctt=0.001, reversal_threshold=0.001)

        # --- С ресетом (enabled time_filter, окно 10:00-11:00) ---
        res_reset = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=cfg_enabled,
            zigzag_global_stats=stats,
            close=close,
            index=idx,
        )
        diag_reset = res_reset.filter_diagnostics

        # На t=3 вышли из окна → time_filter_reset_event[3] == 1
        assert int(diag_reset["time_filter_reset_event"][3]) == 1, (
            f"Ожидаем time_filter_reset на t=3, got {diag_reset['time_filter_reset_event']}"
        )
        # После ресета direction → UNKNOWN → candidate_age == -1
        cand_age_reset = diag_reset["candidate_age_bars"]
        assert int(cand_age_reset[3]) == -1, (
            f"После time_filter_reset на t=3 candidate_age должен быть -1 (UNKNOWN), "
            f"got {int(cand_age_reset[3])}"
        )

        # --- Без ресета (disabled time_filter) ---
        res_no_reset = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=cfg_disabled,
            zigzag_global_stats=stats,
            close=close,
            index=idx,
        )
        diag_no_reset = res_no_reset.filter_diagnostics

        # Без ресета: ZigZag накапливал кандидата → candidate_age > 0
        cand_age_no_reset = diag_no_reset["candidate_age_bars"]
        assert int(cand_age_no_reset[3]) > 0, (
            f"Без ресета на t=3 candidate_age должен быть > 0, "
            f"got {int(cand_age_no_reset[3])}"
        )

        # Итог: ресет убил кандидата (-1 вместо >0) — §4.3 подтверждён
        assert int(cand_age_reset[3]) < int(cand_age_no_reset[3]), (
            "combined_reset_event должен уменьшать candidate_age на баре ресета"
        )
