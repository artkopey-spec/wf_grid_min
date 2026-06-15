# ТЗ: OHLC-based ATR Expansion для `wakeup_regime` Mode D

## 1. Цель

Компонент `trade_filter.wakeup_regime.entry.atr_expansion` должен считать расширение волатильности по настоящему True Range на базе OHLC, а не по close-to-close surrogate.

Правильная формула:

```python
tr = calculate_true_range(high, low, close)
short_atr = _atr_rma_or_nan(tr, short_window)
long_atr = _atr_rma_or_nan(tr, long_window)
ratio = short_atr / long_atr
```

Вход разрешается, если:

```python
ratio[t] >= min_ratio
```

Старое поведение `calculate_true_range(close, close, close)` должно быть удалено. Тихого fallback на close-only расчёт быть не должно.

## 2. Архитектурный контракт

Новый контракт:

```text
ZigZag pivot/height calculation remains close-only.
Mode D wakeup ATR expansion uses OHLC runtime data.
```

Это означает:

- ZigZag-кандидаты, высоты, локальные медианы и pivot/leg logic продолжают вычисляться только из `close`.
- `high` и `low` добавляются в runtime только для компонентов, которым они реально нужны.
- `wakeup_regime.entry.atr_expansion` обязан использовать `high`, `low`, `close`.
- Наличие `high`/`low` в `zigzag_st_filter.apply()` не означает, что ZigZag стал OHLC-based.

## 3. Требуемое поведение

### 3.1. ATR ratio

Если активен Mode D и включён:

```yaml
trade_filter:
  wakeup_regime:
    entry:
      atr_expansion:
        enabled: true
```

то `wakeup_entry_atr_ratio` должен считаться только так:

```python
tr = calculate_true_range(high, low, close)
short_atr = _atr_rma_or_nan(tr, short_window)
long_atr = _atr_rma_or_nan(tr, long_window)
```

Деление:

```python
ratio = short_atr / long_atr
```

выполняется только там, где:

```python
np.isfinite(short_atr)
np.isfinite(long_atr)
long_atr > 0
```

В остальных местах `ratio` остаётся `NaN`.

### 3.2. Warmup

Первые `long_window - 1` значений `ratio` должны быть `NaN`.

На warmup-барах вход через `atr_expansion` невозможен, потому что проверка входа должна оставаться такой:

```python
math.isfinite(ratio[t]) and ratio[t] >= min_ratio
```

### 3.3. Execution shift

Сдвиг исполнения не менять:

- расчёт на баре `t` использует данные до `t` включительно;
- если вход разрешён на баре `t`, позиция открывается на `t + 1`.

### 3.4. Ошибки

Если `atr_expansion.enabled=true`, но в `apply()` нет валидных `high`, `low` или `close`, нужно выбрасывать `ConfigError`.

Сообщение должно быть понятным, например:

```text
apply() Mode D wakeup atr_expansion requires high, low, and close OHLC arrays
```

Fallback на `close` запрещён.

## 4. API и runtime-данные

### 4.1. `zigzag_st_filter.apply()`

В сигнатуру `apply()` добавить trailing optional параметры:

```python
high: Optional[np.ndarray] = None
low: Optional[np.ndarray] = None
```

`close` уже существует и остаётся как есть.

Контракт:

- `high`/`low` не обязательны для обычного ZigZag/FSM path;
- `high`/`low` обязательны только при `wakeup_regime.entry.atr_expansion.enabled=true`;
- `per_bar` отключает только построение ZigZag из `close`;
- `per_bar` не отключает runtime-компоненты wakeup;
- поэтому `per_bar + atr_expansion.enabled=true` всё равно требует `high`, `low`, `close`.

### 4.2. Валидация OHLC в `apply()`

При `atr_expansion.enabled=true` выполнить runtime-валидацию:

- `high is not None`;
- `low is not None`;
- `close is not None`;
- каждый массив является 1-D;
- длина каждого массива равна `n`;
- все значения finite;
- `high >= low` для всех баров.

При нарушении — `ConfigError`.

Эта проверка является защитным runtime-дублем общей data-level валидации OHLC. Правила должны быть согласованы с общим validator’ом данных, чтобы не было разных представлений о валидном OHLC.

Если `atr_expansion.enabled=false`, отсутствие `high`/`low` не является ошибкой.

Если `high`/`low` переданы при выключенном `atr_expansion`, они не должны влиять на ZigZag/FSM результат.

## 5. Изменения в коде

### 5.1. `_compute_wakeup_atr_ratio`

Изменить сигнатуру:

```python
def _compute_wakeup_atr_ratio(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    short_window: int,
    long_window: int,
) -> np.ndarray:
```

Внутри:

```python
high_f = np.asarray(high, dtype=np.float64)
low_f = np.asarray(low, dtype=np.float64)
close_f = np.asarray(close, dtype=np.float64)

tr = calculate_true_range(high_f, low_f, close_f)
short_atr = _atr_rma_or_nan(tr, short_window)
long_atr = _atr_rma_or_nan(tr, long_window)
```

Сохранить текущую защитную семантику:

- если `short_window < 1` или `long_window < 1` — `ConfigError`;
- если `n < long_window` — вернуть массив `NaN` длины `n`;
- делить только при finite `short_atr`, finite `long_atr`, `long_atr > 0`;
- после расчёта принудительно выставить:

```python
ratio[:long_window - 1] = np.nan
```

Важно: внутри `_compute_wakeup_atr_ratio` использовать `_atr_rma_or_nan`, а не прямой `calculate_atr_rma`, чтобы короткие массивы возвращали `NaN`, а не падали с `ValueError`.

### 5.2. `apply()`

В Mode D initialization заменить close-only расчёт на OHLC-расчёт.

Логика должна быть эквивалентна:

```python
if _is_component_enabled(wakeup_atr_cfg):
    high_arr = _require_wakeup_ohlc_array("high", high, n)
    low_arr = _require_wakeup_ohlc_array("low", low, n)
    close_arr = _require_wakeup_ohlc_array("close", close, n)

    _validate_wakeup_ohlc(high_arr, low_arr, close_arr)

    wakeup_atr_ratio = _compute_wakeup_atr_ratio(
        high_arr,
        low_arr,
        close_arr,
        int(getattr(wakeup_atr_cfg, "short_window")),
        int(getattr(wakeup_atr_cfg, "long_window")),
    )
```

Имена helper’ов можно выбрать по стилю проекта. Поведение обязательно.

### 5.3. `run_backtest_fast`

При вызове `zigzag_st_filter.apply(...)` передавать исходные массивы:

```python
high=high,
low=low,
```

Они должны доходить до `_compute_wakeup_atr_ratio` без изменения длины, порядка и выравнивания относительно `close`.

### 5.4. `run_single_backtest`

Сигнатуру менять не нужно: `run_single_backtest` уже принимает `high` и `low`.

Нужно только убедиться, что существующий путь:

```text
run_single_backtest
→ run_backtest_fast
→ zigzag_st_filter.apply
→ _compute_wakeup_atr_ratio
```

передаёт один и тот же OHLC-набор без рассинхронизации.

## 6. Конфиг

Схему конфига не менять.

Параметры остаются прежними:

```yaml
trade_filter:
  wakeup_regime:
    entry:
      atr_expansion:
        enabled: true
        short_window: 5
        long_window: 30
        min_ratio: 1.5
```

Не добавлять:

- `tr_source`;
- `smoothing`;
- `normalize_by_price`;
- compatibility flag для старого close-only поведения.

Старое close-only поведение намеренно ломается.

## 7. Диагностика

Существующее per-bar поле оставить:

```text
wakeup_entry_atr_ratio
```

Новых diagnostic fields не требуется.

После изменения `wakeup_entry_atr_ratio` должен содержать OHLC-based ATR ratio.

## 8. Тесты

### 8.1. Unit: `_compute_wakeup_atr_ratio` использует OHLC TR

Построить `high`, `low`, `close` с очевидным expected TR.

Проверить, что результат `_compute_wakeup_atr_ratio(...)` совпадает с ручным expected, построенным через:

```python
tr = calculate_true_range(high, low, close)
short_atr = _atr_rma_or_nan(tr, short_window)
long_atr = _atr_rma_or_nan(tr, long_window)
```

### 8.2. Wide candles

Сценарий:

- `close` плоский или почти плоский;
- `high - low` расширяется на последних барах.

Проверить:

- OHLC-ratio становится finite после warmup, если `long_atr > 0`;
- OHLC-ratio реагирует на расширение внутрибара;
- close-only surrogate не используется как expected result.

Не требовать общего правила “OHLC ratio всегда строго больше close-only ratio”: это математически неверно для части рядов.

### 8.3. Gaps

Сценарий с гэпом между `prev_close` и текущими `high`/`low`.

Проверить, что TR учитывает:

```python
abs(high[t] - close[t - 1])
abs(low[t] - close[t - 1])
```

### 8.4. Warmup

Проверить:

```python
np.isnan(ratio[:long_window - 1]).all()
```

И на уровне `apply()` проверить, что warmup-бары не открывают вход через `atr_expansion`.

### 8.5. Error contract

Прямой вызов `apply()` должен выбрасывать `ConfigError`, если:

- `atr_expansion.enabled=true`;
- отсутствует `high`;
- отсутствует `low`;
- отсутствует `close`;
- длина `high`, `low` или `close` не равна `n`;
- массив не 1-D;
- есть `NaN` или `Inf`;
- есть бары с `high < low`.

### 8.6. Plumbing

Через monkeypatch/spy проверить:

```text
run_backtest_fast → zigzag_st_filter.apply
```

Ожидание:

- `high` передан в `apply()` как тот же массив;
- `low` передан в `apply()` как тот же массив;
- `close` остаётся тем же массивом.

### 8.7. Сквозной backtest

Сквозной тест:

- `run_backtest_fast`;
- Mode D активен;
- `atr_expansion.enabled=true`;
- передан реальный OHLC.

Ожидание:

- backtest завершается без ошибки;
- `filter_diagnostics["wakeup_entry_atr_ratio"]` существует;
- длина diagnostics совпадает с длиной `positions`;
- после warmup есть finite ratio-значения, если данные содержат ненулевой TR.

### 8.8. Close-only ZigZag invariant

Старые anti-drift тесты переписать под новый контракт.

Удалить требования вида:

```python
assert "high" not in inspect.signature(apply).parameters
assert "low" not in inspect.signature(apply).parameters
```

Новый invariant:

```text
ZigZag pivot/height calculation remains close-only.
Mode D wakeup ATR expansion uses OHLC runtime data.
```

#### A. Backtest-level invariant

Через `run_backtest_fast` / `run_single_backtest` нельзя требовать неизменности FSM-результата при изменении `high/low`, потому что `high/low` влияют на SuperTrend, а SuperTrend формирует `trend`.

При искажении `high/low` через backtest-level path могут измениться:

- `positions`;
- `trade_filter_state`;
- `confirmed_legs_since_start`;
- `st_flip_dir`;
- любые FSM-поля, зависящие от `trend`.

Через backtest-level тесты можно требовать неизменности только close-derived ZigZag-полей, например:

- `candidate_height_pct`;
- `local_median_N`;
- другие diagnostics, которые вычисляются только из `close` / `per_bar`, а не из SuperTrend.

#### B. Direct `apply()` invariant

Если тест вызывает `zigzag_st_filter.apply()` напрямую с:

- фиксированным `trend`;
- одинаковым `close` или одинаковым `per_bar`;
- `atr_expansion.enabled=false`;

тогда изменение `high/low` не должно менять:

- `positions`;
- `trade_filter_state`;
- `confirmed_legs_since_start`;
- `st_flip_dir`;
- ZigZag-derived diagnostics.

Это корректный unit-level invariant, потому что `trend` уже зафиксирован, а OHLC-dependent ATR gate выключен.

#### C. При включённом `atr_expansion`

Если `atr_expansion.enabled=true`, изменение `high/low` имеет право менять:

- `wakeup_entry_atr_ratio`;
- `wakeup_entry_atr_ok`;
- итоговый вход;
- `positions`;
- FSM-state после первой точки различия.

Это ожидаемое поведение.

## 9. Что обновить в существующих тестах и документации

Обновить все прямые вызовы `apply()` с `atr_expansion.enabled=true`:

- либо передать `high`, `low`, `close`;
- либо выключить `atr_expansion`;
- либо явно ожидать `ConfigError`.

Обновить tests/fixtures, где Mode D ATR включён через shared config helper.

Обновить plumbing-тесты, которые сейчас ожидают, что `run_backtest_fast` не передаёт `high/low` в `apply()`.

Обновить оба anti-drift grep-gate теста, запрещающие `high/low` в сигнатуре `apply()`:

```text
wf_grid/tests/test_wp10_finalization.py
wf_grid/tests/test_wp11_rollback_hardening.py
```

Их нельзя просто удалить без замены. Нужно переписать их под новый контракт:

```text
apply() may accept high/low.
ZigZag pivot/height remains close-only.
wakeup ATR expansion is OHLC-based.
```

Обновить комментарии, docstrings и спецификацию, где старый контракт сформулирован как:

```text
apply() is close-only
```

на:

```text
ZigZag pivot/height calculation is close-only.
Mode D wakeup ATR expansion uses OHLC runtime data.
```

## 10. Критерии приёмки

1. При `atr_expansion.enabled=true` ratio считается через `calculate_true_range(high, low, close)`.
2. Close-only fallback отсутствует.
3. `_compute_wakeup_atr_ratio` использует `_atr_rma_or_nan`, а не прямой небезопасный `calculate_atr_rma`.
4. `run_backtest_fast` передаёт `high` и `low` в `zigzag_st_filter.apply()`.
5. Прямой `apply()` с включённым `atr_expansion` без `high`/`low` падает с `ConfigError`.
6. Невалидные OHLC-массивы для `atr_expansion` дают `ConfigError`.
7. Warmup сохраняется: первые `long_window - 1` значений ratio равны `NaN`.
8. Execution shift не меняется: сигнал на `t`, исполнение на `t + 1`.
9. ZigZag pivot/height logic остаётся close-only.
10. Anti-drift тесты переписаны под новый контракт, а не удалены без замены.
11. Сквозной backtest с OHLC и включённым `atr_expansion` проходит.
12. Все релевантные тесты зелёные.

## 11. Не входит в задачу

Не реализовывать:

- `tr_source`;
- выбор smoothing method;
- `ATR / price`;
- compatibility mode для старого close-only поведения;
- новую схему конфига;
- новые diagnostic fields;
- автоматический подбор новых `min_ratio`;
- логирование доли `high == low`.

## 12. Замечание по параметрам

После перехода на OHLC-based ATR старые значения `min_ratio`, подобранные на close-only surrogate, нельзя считать переносимыми.

Для новых прогонов нужна отдельная сетка параметров под OHLC-based ATR. Начальный исследовательский диапазон:

```text
min_ratio: 1.3 .. 2.0
```

Точный диапазон должен подтверждаться отдельным grid-sweep.
