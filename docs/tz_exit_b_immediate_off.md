# ТЗ: `exit_b_immediate_off` — немедленное завершение lifecycle по порогу exit B

## 1. Цель

Добавить опциональный режим для `trade_filter.lifecycle.exit_off_mode: "exit B"`:

- при достижении `exit_off_zz_leg_count` не переходить в `ST_STOPPING`,
- завершать lifecycle как `OFF` в баре решения,
- закрывать позицию по стандартной модели `open_to_open`:
  решение на close бара `t`, исполнение на open бара `t+1`.

Режим по умолчанию не меняется: при `exit_b_immediate_off: false` остаётся текущее поведение через `ST_STOPPING`.

## 2. Область изменений

- `donor/supertrend_optimizer/core/trade_filter_config.py`
  - расширение dataclass lifecycle,
  - whitelist ключей,
  - валидация нового ключа.
- `donor/supertrend_optimizer/core/zigzag_st_filter.py`
  - ветвление в `apply()` в path `exit B threshold`,
  - новые диагностические массивы.
- `donor/supertrend_optimizer/io/excel_tester.py`
  - отображение нового флага/режима и новой причины выхода в summary/diagnostics.
- Тесты:
  - schema/validation,
  - runtime FSM/positions/trades,
  - excel contract (минимально необходимое).

## 3. Конфигурация

### 3.1 Новый ключ

- Путь: `trade_filter.lifecycle.exit_b_immediate_off`
- Тип: `bool`
- Дефолт: `false` (только когда применим, см. ниже)
- Применимость: только при `exit_off_mode == "exit B"`.

### 3.2 Валидация (без альтернатив)

1. Если `exit_off_mode == "exit B"`:
   - ключ `exit_b_immediate_off` опционален,
   - при отсутствии трактуется как `false`.
2. Если `exit_off_mode != "exit B"` (включая `"exit A"` и неявный default `"exit A"`):
   - ключ `exit_b_immediate_off` запрещён (fail-fast).
3. Все текущие правила для `exit_off_mode` и `exit_off_zz_leg_count` сохраняются без изменений.

## 4. Поведение FSM

### 4.1 Точка срабатывания (без изменений)

Условие порога прежнее:

- `is_exit_b == true`
- `state == ST_COUNTING_ZZ_LEGS`
- `zz_legs_since_lifecycle_start >= exit_off_zz_target`

### 4.2 Ветвление по новому флагу

#### Ветка A: `exit_b_immediate_off == false` (legacy)

- Полностью текущее поведение:
  - переход в `ST_STOPPING`,
  - `zz_leg_stop_triggered = 1`,
  - закрытие только по текущим правилам `ST_STOPPING`.

#### Ветка B: `exit_b_immediate_off == true`

На баре порога `t` выполнить в этом порядке:

1. Зафиксировать факт порога в существующем `zz_leg_stop_triggered[t] = 1`.
2. Зафиксировать путь обработки `immediate_off` в новом диагностическом флаге (см. §5).
3. Установить FSM в `OFF` на этом же баре.
4. Сбросить lifecycle-счётчики и runtime-состояние к OFF-инвариантам:
   - `zz_legs_since_lifecycle_start = -1`
   - `confirmed_legs_since_start = -1`
   - `held_pos = 0`
5. В записи `filtered_positions[t+1]` использовать существующий механизм `open_to_open`:
   - позиция становится `0` на open(`t+1`),
   - отдельный механизм исполнения не вводится.

### 4.3 Важная семантика state vs position

Для `immediate_off=true` допустимо и требуется:

- `trade_filter_state[t] == "OFF"` (решение уже принято на close `t`),
- но фактическая позиция в `positions` обнуляется на следующем баре (`t+1`).

Это не look-ahead, а стандартный контракт `open_to_open`.

### 4.4 Ограничения и инварианты

- Не менять `exit A` path.
- Не менять не-`exit B` сценарии.
- Не ломать `daily_reset` приоритеты и reset/wipe инварианты.
- Не допускать закрытие раньше open(`t+1`).
- При `immediate_off=true` порог exit B не должен приводить к `ST_STOPPING`.

## 5. Диагностики (обязательные изменения)

Чтобы не ломать обратную совместимость:

1. Сохранить `zz_leg_stop_triggered` как индикатор факта достижения порога.
2. Добавить новый пер-бар флаг (0/1):
   - `exit_b_immediate_off_triggered`
   - =1 только на баре порога, если применён immediate-off path.
3. Добавить новый пер-бар config echo флаг (broadcast):
   - `exit_b_immediate_off_config` (0/1).

Примечание: старые потребители `zz_leg_stop_triggered` продолжают работать без изменений.

## 6. Trade-level exit reason (обязательное уточнение)

Текущий `exit_reason` не должен оставаться двусмысленным для immediate-off сценария.

Требование:

- при закрытии, вызванном `exit_b_immediate_off`, писать отдельную причину, например:
  - `filter_exit_b_immediate_off`.

Приоритет причины должен быть ниже `filter_daily_reset` и выше generic `st_flip`.
Существующий `filter_stopping_opposite_flip` сохраняется только для legacy-path через `ST_STOPPING`.

## 7. Excel / отчётность

Минимально:

- В params/snapshot добавить:
  - `Exit-B Immediate OFF` (`true`/`false`).
- В diagnostics/export добавить новые поля из §5.
- Если отображается причина выхода, добавить значение `filter_exit_b_immediate_off`.

Ограничение:

- Не менять смысл и расчёт существующей колонки `Ног ZigZag в цикле`.
- Разрешены только добавления новых полей/значений без ломки старых.

## 8. Тест-план (минимум)

1. **Регрессия legacy**
   - `exit_b_immediate_off=false` даёт идентичное поведение текущему baseline.
2. **Runtime immediate-off**
   - на баре порога `trade_filter_state[t] == OFF`,
   - `zz_leg_stop_triggered[t] == 1`,
   - `exit_b_immediate_off_triggered[t] == 1`,
   - `positions[t+1] == 0`,
   - нет промежуточного `ST_STOPPING` из этого события.
3. **Daily reset соседство**
   - без фантомных инкрементов счётчиков,
   - без двойного закрытия.
4. **Config validation**
   - `exit A + exit_b_immediate_off` -> ошибка,
   - `exit B` без `exit_off_zz_leg_count` -> как раньше ошибка,
   - `exit B` без `exit_b_immediate_off` -> валидно, default false.
5. **Mode C / C+B**
   - immediate-off не ломает OFF->COUNTING сценарий,
   - не создаёт двойного close.
6. **Trade exit reason**
   - immediate-off закрытие маркируется `filter_exit_b_immediate_off`,
   - legacy stopping закрытие остаётся `filter_stopping_opposite_flip`.

## 9. Критерии приёмки

- Дефолтные конфиги (без нового ключа) проходят регрессию без изменений результатов.
- При `exit_b_immediate_off=true` закрытие происходит раньше (без фазы `ST_STOPPING` для threshold-события exit B).
- Контракт диагностики и `exit_reason` однозначный и документирован.


