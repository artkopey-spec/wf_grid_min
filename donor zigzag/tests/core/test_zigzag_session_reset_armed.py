"""
Тест-защита §1.5/§1.7: session_reset при armed ноге → fired=FIRED_SESSION_RESET
(план v2.0.1 FIX 7).

При переходе в новую сессию (session_ids[t] != session_ids[t-1]):
  - armed нога получает fired=FIRED_SESSION_RESET, shot_bar=-1
  - armed сбрасывается на первом баре новой сессии
  - one_shot очищается

Тест использует синтетические данные через _run_armament_state_machine
(юнит) и через compute_zigzag_filter (интеграция).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    ARMED_SIDE_LONG,
    FIRED_NONE,
    FIRED_SESSION_RESET,
    FIRED_YES_SHOT,
    LEG_DIR_DOWN,
    LEG_DIR_UP,
    REGIME_OPEN_ACTIVE,
    REGIME_OPEN_GRACE,
    _LegRegimeInfo,
    _LegStatsSnapshot,
    _PartialLeg,
    _run_armament_state_machine,
    compute_zigzag_filter,
)
from supertrend_optimizer.utils.constants import (
    FILTER_REASON_ZZ_NOT_ARMED,
    FILTER_REASON_ZZ_WARMUP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_leg(leg_id: int, direction: int, confirm_bar: int,
            height_pct: float = 0.05) -> _PartialLeg:
    start_bar = max(0, confirm_bar - 5)
    end_bar = confirm_bar - 1
    start_price = 100.0
    end_price = (start_price * (1.0 + height_pct) if direction == LEG_DIR_UP
                 else start_price * (1.0 - height_pct))
    return _PartialLeg(
        leg_id=leg_id,
        start_bar=start_bar,
        end_bar=end_bar,
        confirm_bar=confirm_bar,
        start_price=start_price,
        end_price=end_price,
        direction=direction,
        height_pct=height_pct,
        length_bars=end_bar - start_bar,
        confirm_lag_bars=confirm_bar - end_bar,
    )


def _mk_snap(n_before: int, g_med: float = 0.05, g_p80: float = 0.08,
             l_med: float = float("nan")) -> _LegStatsSnapshot:
    return _LegStatsSnapshot(
        n_legs_before=n_before, global_median=g_med,
        global_p80=g_p80, local_median=l_med,
    )


def _mk_reg(state: int = REGIME_OPEN_ACTIVE, is_strong: bool = True,
            opened: bool = False, n_since: int = 5) -> _LegRegimeInfo:
    return _LegRegimeInfo(
        state_at_confirm=state,
        opened_regime=opened,
        closed_regime=False,
        n_legs_since_regime_open=n_since,
        is_strong=is_strong,
    )


# ---------------------------------------------------------------------------
# 1. Юнит-тест: _run_armament_state_machine с session_reset_event
# ---------------------------------------------------------------------------


class TestSessionResetArmedUnit:
    """§1.7: armed нога должна получить fired=FIRED_SESSION_RESET при сбросе сессии."""

    def test_armed_leg_fired_session_reset(self):
        """
        Сценарий:
          - leg 0 (DOWN) → должна вооружиться на bar 5 (armed LONG)
          - bar 15: session_reset_event=True, ST-flip НЕ произошёл
          - Ожидаем: leg_outs[0].fired == FIRED_SESSION_RESET
          - Ожидаем: armed[15] == False (на баре сброса уже сбрасывается)
        """
        N = 30
        legs = [_mk_leg(0, LEG_DIR_DOWN, confirm_bar=5)]
        snaps = [_mk_snap(n_before=20)]
        regs = [_mk_reg(state=REGIME_OPEN_ACTIVE, is_strong=True, n_since=5)]

        trend = np.full(N, -1, dtype=np.int8)  # нет flip → armed ждёт

        session_reset_event = np.zeros(N, dtype=bool)
        session_reset_event[15] = True  # сброс сессии на баре 15

        leg_outs, arr = _run_armament_state_machine(
            legs=legs,
            regime_infos=regs,
            snapshots=snaps,
            st_trend=trend,
            high=np.zeros(N),
            low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=session_reset_event,
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )

        # Нога должна была вооружиться на confirm_bar=5
        assert arr.armed[5], "expected arm on confirm_bar=5"

        # На баре сброса — fired=SESSION_RESET
        assert leg_outs[0].fired == FIRED_SESSION_RESET, (
            f"expected FIRED_SESSION_RESET, got fired={leg_outs[0].fired}"
        )
        assert leg_outs[0].shot_bar == -1, (
            f"shot_bar должен быть -1 при SESSION_RESET, got {leg_outs[0].shot_bar}"
        )

        # После сброса — armed=False
        assert not arr.armed[15], (
            "armed должен быть False на баре session_reset"
        )
        assert not arr.armed[16], (
            "armed должен быть False после session_reset"
        )

    def test_no_session_reset_without_armed_leg(self):
        """Если нога не вооружена — session_reset не создаёт FIRED_SESSION_RESET."""
        N = 20
        # Нога не вооружается (not_strong + grace)
        legs = [_mk_leg(0, LEG_DIR_DOWN, confirm_bar=5)]
        snaps = [_mk_snap(n_before=5)]  # n_before < min_legs_global → не вооружится
        regs = [_mk_reg(state=REGIME_OPEN_GRACE, is_strong=False, n_since=1)]

        trend = np.full(N, -1, dtype=np.int8)
        session_reset_event = np.zeros(N, dtype=bool)
        session_reset_event[10] = True

        leg_outs, arr = _run_armament_state_machine(
            legs=legs, regime_infos=regs, snapshots=snaps,
            st_trend=trend, high=np.zeros(N), low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=session_reset_event,
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        # Нога не вооружалась — fired должен остаться FIRED_NONE или NO_*
        assert leg_outs[0].fired != FIRED_SESSION_RESET, (
            "не вооружённая нога не должна получать FIRED_SESSION_RESET"
        )

    def test_session_reset_clears_one_shot(self):
        """§1.7: one_shot сбрасывается при session_reset."""
        N = 30
        # Нога 0 (DOWN) → вооружается, потом fires YES_SHOT на bar 8 (flip +1)
        # Нога 1 (UP) подтверждается на bar 8 → one_shot=True блокирует вход до новой ноги
        legs = [
            _mk_leg(0, LEG_DIR_DOWN, confirm_bar=5),
            _mk_leg(1, LEG_DIR_UP,   confirm_bar=8, height_pct=0.05),
        ]
        snaps = [_mk_snap(n_before=20), _mk_snap(n_before=21)]
        regs = [
            _mk_reg(state=REGIME_OPEN_ACTIVE, is_strong=True, n_since=5),
            _mk_reg(state=REGIME_OPEN_ACTIVE, is_strong=True, n_since=6),
        ]
        trend = np.full(N, -1, dtype=np.int8)
        trend[8:] = +1  # flip на баре 8 → YES_SHOT leg 0

        session_reset_event = np.zeros(N, dtype=bool)
        session_reset_event[15] = True  # сброс сессии после одного выстрела

        leg_outs, arr = _run_armament_state_machine(
            legs=legs, regime_infos=regs, snapshots=snaps,
            st_trend=trend, high=np.zeros(N), low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=session_reset_event,
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        # После session_reset one_shot должен быть сброшен
        assert not arr.one_shot_fired_current_leg[15], (
            "one_shot должен быть False на баре session_reset (§1.7)"
        )
        assert not arr.one_shot_fired_current_leg[16], (
            "one_shot должен оставаться False после session_reset"
        )


# ---------------------------------------------------------------------------
# 2. Интеграционный тест: compute_zigzag_filter с DatetimeIndex (реальный reset)
# ---------------------------------------------------------------------------


class TestSessionResetArmedIntegration:
    """Интеграционная проверка через compute_zigzag_filter с multi-session данными."""

    def _make_two_session_data(self, n_day1: int = 60, n_day2: int = 40):
        """
        Синтетические данные с осцилляциями — чтобы появились ноги на обоих днях.
        День 1: с достаточным количеством баров для армирования.
        """
        rng = np.random.default_rng(777)
        N = n_day1 + n_day2

        # Осцилляции для генерации ног
        t = np.arange(N)
        base = 100.0 + 5.0 * np.sin(t * 0.4) + rng.normal(0, 0.3, N)
        high = base + 0.5
        low = base - 0.5
        close = base
        open_p = base

        # session_ids меняются на n_day1
        session_ids = np.zeros(N, dtype=np.int64)
        session_ids[n_day1:] = 1

        # trend с несколькими флипами
        trend = np.ones(N, dtype=np.int8)
        trend[20:30] = -1
        trend[40:45] = -1

        return high, low, close, open_p, session_ids, trend, N

    def test_session_reset_produces_fired_session_reset_legs(self):
        """
        Если нога была вооружена на последнем баре сессии 1 — в следующей сессии
        она должна стать FIRED_SESSION_RESET.
        """
        high, low, close, open_p, session_ids, trend, N = self._make_two_session_data(
            n_day1=60, n_day2=40
        )

        cfg = dict(
            reversal_threshold=0.003,
            min_legs_global=0,  # без warmup-порога
            q_strong=0.80,
            k_local=5,
            entry_side="counter_trend",
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        res = compute_zigzag_filter(high, low, close, open_p, session_ids, trend, cfg)

        # Проверяем, что есть хотя бы одна нога с FIRED_SESSION_RESET
        # (только если были вооружённые ноги на границе сессии)
        session_reset_legs = [lg for lg in res.legs if lg.fired == FIRED_SESSION_RESET]
        armed_legs_total = [lg for lg in res.legs if lg.arm_bar >= 0]

        if armed_legs_total:
            # Проверяем инвариант: вооружённые ноги, которые были активны
            # на баре session_reset, должны быть разряжены как SESSION_RESET
            boundary = int(np.where(np.diff(session_ids.astype(int)) != 0)[0][0]) + 1

            # На баре boundary armed должен быть False
            assert not res.armed[boundary], (
                f"armed должен быть False на баре boundary={boundary} после session_reset"
            )

            # Если были ноги, вооружённые до boundary и не разряженные до него,
            # они должны получить FIRED_SESSION_RESET
            for lg in armed_legs_total:
                if lg.arm_bar < boundary and lg.fired == FIRED_NONE:
                    pytest.fail(
                        f"Нога {lg.leg_id} (arm_bar={lg.arm_bar}) активна через "
                        f"session boundary={boundary} без FIRED_SESSION_RESET"
                    )
