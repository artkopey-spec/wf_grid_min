# time_filter — Specification v1

Документ описывает контракт фильтра торгового времени (`trade_filter.time_filter`).

---

## Конфигурация

```yaml
trade_filter:
  enabled: true
  ...
  time_filter:
    enabled: true          # false = полный no-op
    window: "09:00-19:00"  # HH:MM-HH:MM, обязателен при enabled=true
```

Поле `window` **обязательно** при `enabled: true`. При `enabled: false` поле
`window` принимается (но не валидируется на range/format) для удобства хранения
шаблона.

### Ограничения формата `window`

| Правило | Код ошибки |
|---|---|
| Отсутствует `window` при `enabled=true` | `time_filter_window_missing` |
| Не соответствует `HH:MM-HH:MM` | `time_filter_window_invalid_format` |
| `HH` вне `[00, 23]` | `time_filter_window_invalid_hours` |
| `MM` вне `[00, 59]` | `time_filter_window_invalid_minutes` |
| `start == end` (нулевая длина) | `time_filter_window_zero_length` |
| `start > end` (cross-midnight) | `time_filter_window_cross_midnight` |
| `enabled` не `bool` | `time_filter_enabled_invalid_type` |

Cross-midnight окна (`"22:00-06:00"`) **запрещены в v1**. При необходимости
нужно разделить на два сегмента на стороне data-loader'а.

---

## Контракт окна `[start, end)`

Бар включается в окно, если:

```
start_minutes <= minute_of_day(bar) < end_minutes
```

где `minute_of_day = hour * 60 + minute`. Секунды и наносекунды индекса
**игнорируются** (`tz_localize(None)` + целочисленное деление).

Пример для окна `"09:00-19:00"`:

| Время бара | `in_window` |
|---|---|
| `09:00` | `True` |
| `18:59` | `True` |
| `19:00` | `False` |
| `08:59` | `False` |

---

## Timezone

Timezone берётся из индекса **as-is** — без тихой конвертации. Окно задаётся
в той же временной зоне, что и данные CSV.

- **МосБиржа (MSK, UTC+3, нет DST)**: tz-naive индекс в локальном времени —
  окно `"09:00-19:00"` задаётся в MSK.
- **tz-aware с DST**: при переводе часов возможны дубликаты/пропуски timestamp'ов.
  Это ответственность data-loader'а — `time_filter` не выполняет DST-коррекцию.
- **UTC-индекс**: окно задаётся в UTC. Для МосБиржи в UTC это `"06:00-16:00"`.

---

## Эквивалентность с `daily_reset`

`time_filter_reset` повторяет поведение `daily_reset` по всем аспектам:

| Аспект | Поведение |
|---|---|
| FSM wipe | `state → OFF`, counters → sentinel `-1` |
| Lifecycle reset | `confirmed_legs_since_start`, `zz_legs_since_lifecycle_start` → `-1` |
| Закрытие позиции | `positions[t+1] = 0` (open-to-open) |
| ZigZag candidate-state | `compute_zigzag_per_bar` получает `combined_reset_event = daily_reset | time_filter_reset` |
| `exit_reason` | `"filter_time_reset"` (см. приоритет ниже) |

Reset-бар определяется как **первый бар вне окна** после бара внутри окна
(или первый бар всего ряда, если он уже вне окна). Пока FSM находится вне
окна, дополнительные reset-события не генерируются.

---

## Приоритет reset-событий

При одновременном срабатывании `daily_reset` и `time_filter_reset` приоритет
принадлежит `daily_reset`:

| `daily_reset_event[t]` | `time_filter_reset_event[t]` | `filter_block_reason[t]` |
|---|---|---|
| 1 | 0 | `"daily_reset"` |
| 1 | 1 | `"daily_reset"` |
| 0 | 1 | `"time_filter_reset"` |

Trade-level `exit_reason` (приоритет сверху вниз):

1. `filter_daily_reset`
2. `filter_time_reset`
3. `pending_open_trade_at_end`
4. `filter_exit_b_immediate_off`
5. `filter_stopping_opposite_flip`
6. `st_flip`

---

## Per-bar diagnostics

Три ключа присутствуют в `filter_diagnostics` **всегда** при
`trade_filter.enabled=true`:

| Ключ | dtype | Значения |
|---|---|---|
| `time_filter_enabled` | `int8` | `1` broadcast (или `0` если `time_filter.enabled=false`) |
| `time_filter_in_window` | `int8` | `1` — бар внутри окна, `0` — снаружи |
| `time_filter_reset_event` | `int8` | `1` — бар является reset-событием, иначе `0` |

При `time_filter.enabled=false`: `time_filter_enabled` — все `0` (broadcast),
`time_filter_in_window` — все `1` (как при отключённом подблоке: временное
окно не режет вход; поведение эквивалентно полному «внутри окна»), `time_filter_reset_event`
— все `0`.

---

## Summary-счётчики

Добавляются в `filter_diagnostics_summary["counters"]` (WF Grid и Tester):

| Ключ | Формула |
|---|---|
| `time_filter_enabled` | `bool(int(time_filter_enabled[0]))` |
| `time_filter_reset_count` | `sum(time_filter_reset_event == 1)` |
| `time_filter_bars_in_window` | `sum(time_filter_in_window == 1)` |
| `time_filter_bars_out_window` | `sum(time_filter_in_window == 0)` |

**Важно**: `time_filter_reset_count` **не входит** в `blocked_entry_signals`.
Счётчик `blocked_entry_signals` включает только шесть `blocked_*` компонентов
(аналогично `daily_reset_count`). Это соответствует §0.3 #10 плана.

---

## Ограничения v1

- Cross-midnight окна запрещены.
- Несколько непересекающихся окон в одном дне не поддерживаются.
- Режим `segmentation.mode: equal_blocks` несовместим с
  `trade_filter.enabled: true` (существующее ограничение).
- DST-коррекция не выполняется — ответственность на data-loader'е.
