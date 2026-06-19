Проверка кода подтвердила все ключевые замечания ревью, и часть из них опровергает мой же v2. Фиксирую факты, затем пересобираю ТЗ.

## Что подтвердилось в коде (и где v2 ошибался)

- **Strict keyset = 46 ключей, не-D, и в нём нет ни одного `wakeup_*`.** `EXPECTED_KEYSET` (`test_phase2_tester_diagnostics_dtype_contract.py:33–81`) реально содержит 46 ключей; docstring «41 keys» — устаревший. Тест гоняет legacy-конфиг (`candidate_threshold`/`confirmed_median`), а не Mode D.
- **Wakeup-диагностика гейтится `zigzag_mode == "D"`** (`zigzag_st_filter.py:2277–2282`): весь блок `wakeup_*` (включая `wakeup_exit_ttl_triggered`) добавляется в `filter_diagnostics` только в Mode D. → Новая Mode-D-only колонка **не трогает** не-D strict-контракт. **Мой v2 §11/§18 («41→42») — неверен, убираю.**
- **wf_grid отвергает весь `wakeup_regime` на уровне shared-валидатора** (`trade_filter_config.py:2220–2244`, ошибки `mode_d_unsupported_pipeline`/`exit_c_unsupported_pipeline`/`wakeup_regime_unsupported_pipeline`). Изоляция сильнее, чем «не добавлять ключ в whitelist» — она wholesale. → Мой v2 §2 про whitelist был неточен.
- **`local_median_stop`-only ≠ полностью пустой wakeup:** `_make_enabled_cfg`/`build_zigzag_global_stats` показывают, что `candidate_trigger_threshold` и обязательные Mode-D-поля никуда не деваются.

Все 12 пунктов ревью принимаю. Ниже — финальная сборка.

---

# ТЗ v3: 4-й Exit C-триггер `local_median_stop` (tester-only, Mode D)

## 0. Режим задачи

Задача — **production-scope, один режим**. Двухэтапность из v2 убрана из acceptance. «Proof of value» допустим как личный pre-step разработчика, но не является частью контракта и не влияет на критерии готовности (устраняет конфликт ревью §12).

## 1. Цель

Добавить четвёртое Exit C-условие: остановка wakeup-цикла, когда `local_median_N[t] < global_median` на confirmed leg bar. Триггер живёт в Mode D / Exit C, уважает общий `wakeup_regime.exit.action.mode`, независим от `no_fresh_candidate`.

## 2. Слои изменений (точная модель «tester-only»)

Ревью §2 принято — разделяю три слоя:

1. **Config admission.** Dataclass и shared-валидатор — общие. Для wf_grid фича недостижима в принципе: shared-валидатор reject-ит весь `wakeup_regime`/`exit C`/`mode D` для `caller_pipeline="wf_grid"`. Поэтому добавление `local_median_stop` для wf_grid ничего не меняет: независимо от фактического порядка loader/build/validate контракт — `caller_pipeline="wf_grid"` обязан reject-ить весь `wakeup_regime`/Mode D/Exit C.
2. **Common runtime support.** Сам триггер, reason/action-строки и per-bar diagnostic-ключ физически живут в общем `zigzag_st_filter.py`, но diagnostic-ключ эмитится только при `zigzag_mode == "D"` (как все прочие `wakeup_*`). В не-D и в wf_grid-прогонах ключа нет.
3. **Tester export/reporting.** Excel header/summary и счётчик в `runner.py` — только tester.

**Не трогаем:** `wf_grid/config/loader.py` whitelist, `wf_grid/export/*`, wf_grid-тесты. **Whitelist wf_grid намеренно не расширяем** — изоляция обеспечена caller_pipeline gate.

## 3. Отличие от `no_fresh_candidate`

Не freshness-timeout. Не использует `wakeup_bars_since_fresh`, `max_age_bars`, `timeout_bars`, `quantile`. Условия независимы; при совпадении — общий priority (§8).

## 4. Конфиг

```yaml
trade_filter:
  lifecycle:
    exit_off_mode: "exit C"
  wakeup_regime:
    enabled: true
    entry:
      # минимум один enabled entry-компонент остаётся ОБЯЗАТЕЛЬНЫМ (Mode D)
      candidate_height:
        enabled: true
        # ...
    exit:
      ttl: { enabled: false }
      no_fresh_candidate: { enabled: false }
      max_trades_per_cycle: { enabled: false }
      local_median_stop: { enabled: true }
      action:
        mode: block_new_entries   # или close_position
```

## 5. Источник медиан

`local_median_N[t]` (окно `N = trade_filter.zigzag.local_window`, из `compute_zigzag_per_bar`) и `global_median` (`ZigZagGlobalStats.global_median`). Новых параметров окна не вводится. Оба поля уже материализованы в рантайме.

## 6. Условие срабатывания

Все одновременно:
- `wakeup_regime.enabled == true`;
- контекст Mode D / Exit C (обеспечивается `mode_d_enabled`; отдельная проверка `exit_off_mode` не требуется);
- `local_median_stop.enabled == true`;
- `state_at_bar_start == ST_ACTIVE_FREEZE`;
- не reset-бар;
- `wakeup_exit_c_fired == false`;
- `confirmed[t] == true`;
- `local_median_available[t]` и `isfinite(local_median_N[t])`;
- `local_median_N[t] < global_median`.

### 6.1. NaN — fail-open (явно ≠ exit A)

NaN/невалидная медиана → **не срабатывает**. Это противоположно legacy median-stop (exit A — fail-closed). Код exit A **не переиспользуется**; в Mode-D-ветке отдельная проверка с fail-open. Допустим общий predicate сравнения с параметром NaN-политики, но для `local_median_stop` строго fail-open.

## 7. Action mode

`block_new_entries`: `state = ST_STOPPING`; входы блокируются; позиция не закрывается сразу; выход на `opposite_st_flip`.
`close_position`: `state = OFF`; `held_pos = 0`; закрытие через OPEN_TO_OPEN; runtime цикла сбрасывается.

## 8. Приоритет Exit C и диагностика проигравших (ревью §6)

Порядок: `ttl > no_fresh_candidate > local_median_stop > cycle_trade_limit` — реализуется `elif` между `no_fresh_candidate` и `cycle_trade_limit`.

**Инвариант флагов:** `*_triggered`-флаг (и reason, и position_action) ставится **только победившему** Exit C-условию. На баре, где `ttl` победил `local_median_stop`, `wakeup_exit_local_median_stop_triggered` остаётся `0`. Никаких флагов «всем satisfied predicates».

## 9. Config — точки интеграции (только shared, аддитивно)

`donor/supertrend_optimizer/core/trade_filter_config.py`:
- dataclass `TradeFilterWakeupLocalMedianStopExitConfig(enabled: object = False)`;
- поле `local_median_stop` в `TradeFilterWakeupExitConfig` (default_factory);
- парсинг `exit_raw.get("local_median_stop")`;
- allowed-keys: `"local_median_stop"` в `"...exit"` + `"...exit.local_median_stop": frozenset({"enabled"})`;
- валидация: `enabled` — bool; unknown sibling → reject; добавить `local_median_enabled` в правило «at least one enabled exit».

### 9.1. Уточнение валидности (ревью §4)

Формулировка: **«exit-блок валиден, если единственное enabled exit condition — `local_median_stop`; все прочие обязательные Mode-D-поля (минимум один enabled entry-компонент, `action.mode`, `candidate_trigger_threshold` и т.д.) остаются обязательными»**. Тест №4 пишется именно так — НЕ «во всём wakeup только local_median_stop».

## 10. Runtime — точки интеграции

`zigzag_st_filter.py`:
- `_WakeupExitConfigParts` + `_resolve_mode_d_wakeup_exit` — новое поле `local_median_stop`;
- распаковка cfg рядом с `wakeup_ttl_cfg`/`wakeup_no_fresh_cfg`;
- `elif`-проверка (§6/§6.1) с raw reason `local_median_stop`;
- `_wakeup_exit_action_for_reason`: ветка `local_median_stop -> exit_local_median_stop` (иначе AssertionError);
- на победившем баре: `wakeup_exit_reason_this_bar="local_median_stop"`, `wakeup_position_action_this_bar="exit_local_median_stop"`, `wakeup_exit_c_fired=true`, `wakeup_exit_c_triggered_this_bar=true`; далее общий `action.mode`.

### 10.1. Централизация строк (ревью §8, обязательный мини-рефакторинг)

Завести единый источник истины для тройки `raw_reason -> position_action -> trade_exit_reason` (module-level constants или один mapping), используемый рантаймом, trade-level диагностикой и export. Не размазывать литералы `"local_median_stop"`/`"exit_local_median_stop"` по 4+ местам.

## 11. Diagnostics (Mode-D-only) — точная стратегия (ревью §1, §5)

- per-bar `int8`-ключ `wakeup_exit_local_median_stop_triggered` добавляется в `filter_diagnostics` **внутри Mode-D-гейта** (`zigzag_st_filter.py:2277+`), рядом с прочими `wakeup_exit_*_triggered`. В не-D и wf_grid-прогонах ключа нет;
- значения: `1` только на баре-победителе (§8), иначе `0`;
- на баре срабатывания: `wakeup_exit_reason="local_median_stop"`, `wakeup_exit_action_mode="block_new_entries"|"close_position"`, `wakeup_position_action="exit_local_median_stop"`.

**Контракт keyset (решение):**
- не-D `EXPECTED_KEYSET` (46) **не меняем** — новая колонка туда не попадает;
- **ввести отдельный Mode-D diagnostics keyset-контракт** (новый тест), фиксирующий полный набор `wakeup_*` ключей, включая `wakeup_exit_local_median_stop_triggered`, с dtype `int8`. Это устраняет «один глобальный EXPECTED_KEYSET» и закрепляет Mode-D-расширение (ревью «контрактный рефакторинг»). Если отдельный Mode-D strict-контракт уже существует — расширить его; сейчас его нет.
Форма контракта: `BASE_EXPECTED_KEYSET + MODE_D_WAKEUP_EXPECTED_KEYS`, чтобы не дублировать руками 46 базовых ключей и не получить новый хрупкий монолит.

## 12. Trade-level reason

`filter_trade_diagnostics.py`:
- `_wakeup_exit_reason_at`: `local_median_stop -> wakeup_exit_local_median_stop` (достаточно для `exit_reason`);
- `_wakeup_position_action_at`: `exit_local_median_stop -> wakeup_exit_local_median_stop` — опционально (siblings `exit_ttl`/`exit_no_fresh` там не маппятся; добавлять только для консистентности отчёта).

Ожидание в сделке: `exit_reason="wakeup_exit_local_median_stop"`, `wakeup_cycle_exit_reason="local_median_stop"`, `wakeup_position_action="exit_local_median_stop"`.

## 13. Export (tester-only) + counter-инварианты (ревью §7, §11)

`io/excel_tester.py`:
- header-map: `"wakeup_exit_local_median_stop_triggered" -> "Wakeup Exit Local Median Stop Triggered"`;
- summary-строка `"Wakeup Exit Local Median Stop"` рядом с `Wakeup Exit TTL`/`No Fresh Candidate`/`Close` (строки `Cycle Trade Limit` в summary нет — не ориентироваться на неё).

`testing/runner.py`:
- счётчик `wakeup_exit_local_median_stop_count = _sum_int8("wakeup_exit_local_median_stop_triggered")`.

**Counter-инвариант (ревью §7):** счётчики Exit C **не взаимоисключающие**. `wakeup_exit_close_count` считает action-mode `close`, а `wakeup_exit_local_median_stop_count` — raw reason. При `close_position` на одном баре инкрементятся оба. Тесты не должны считать суммы Exit C mutually-exclusive.

**Контрактная миграция (ревью §11):** в plan включить grep-driven список затрагиваемых контрактов — header-map, summary-row, `test_pr6_excel_contract`, `test_phase2_wp_t7_excel_export`, snapshot workbook/header expectations — и обновить их явно.

## 14. Test harness (ревью §9, §10)

Обязательная фикстура: **direct-call `apply(..., per_bar=ZigZagPerBar(...))`**, где явно задаются `confirm_event`, `local_median_N`/`local_median_available`, `trend`/ST-flips, `candidate_*`. Runtime Exit C / priority / action кейсы строятся на ней, а не на случайной ZigZag-динамике из синтетического OHLC. Direct-call runtime tests должны строить валидный Mode D / Exit C config даже если runtime полагается на validator-инварианты; иначе тест может случайно проверять поведение на невозможной production-конфигурации. Для §19 — минимальный synthetic dataset с numeric `candidate_trigger_threshold`, чтобы тест проверял именно отсутствие зависимости от no_fresh quantile, а не падал на общих требованиях global stats.

## 15. Тесты

Config: 1) load `enabled:true`; 2) `enabled:"yes"` → fail; 3) unknown key внутри `local_median_stop` → fail; 4) валиден, если `local_median_stop` — единственное enabled exit, остальные обязательные Mode-D-поля заданы (§9.1).

Runtime (на фикстуре §14): 5) `block_new_entries` → `ST_STOPPING`, позиция не закрывается сразу; 6) `close_position` → `OFF`, закрытие; 7) NaN → не срабатывает; 8) только на confirmed bar; 9) независимость от `wakeup_bars_since_fresh`; 10) независимость от `quantile/max_age_bars/timeout_bars`; 11) `ttl`+LMS → `ttl` (и `wakeup_exit_local_median_stop_triggered==0`, §8); 12) `no_fresh`+LMS → `no_fresh`; 13) LMS+`cycle_trade_limit` → `local_median_stop`.

Diagnostics/export: 14) `wakeup_exit_local_median_stop_triggered==1` на победном баре; 15) `wakeup_exit_reason=="local_median_stop"`; 16) trade `exit_reason=="wakeup_exit_local_median_stop"`; 17) XLSX-колонка + summary заполняются.

Контракт/регрессия: 18) **новый Mode-D keyset-контракт** содержит новый ключ и зелёный; не-D `EXPECTED_KEYSET` (46) без изменений и зелёный; 19) global stats инициализируются для `local_median_stop`-only конфига на минимальном synthetic dataset (§14); 20) counter-инвариант close+LMS (§13) не считается ошибкой; 21) **wf_grid regression**: YAML с `trade_filter.wakeup_regime.exit.local_median_stop` падает с `wakeup_regime_unsupported_pipeline` (caller_pipeline gate), старые wf_grid-конфиги проходят без изменений; 22) старые tester-конфиги без `local_median_stop` дают прежнее поведение.

## 16. Рефакторинг

- **Обязательно:** централизованный mapping строк reason/action/trade_exit_reason (§10.1).
- **Тестовый:** Mode-D Exit C direct-call фикстура (§14).
- **Контрактный:** отдельный Mode-D diagnostics keyset вместо расширения глобального (§11).

## 17. Что может сломаться

`_WakeupExitConfigParts` unpacking; `_wakeup_exit_action_for_reason` (AssertionError); Excel header/summary contracts (`test_pr6_excel_contract`, `wp_t7_excel_export`); tester summary counters; trade-level `exit_reason`; wf_grid rejection regression (должна остаться `wakeup_regime_unsupported_pipeline`). **Не должны сломаться:** не-D `EXPECTED_KEYSET` (колонка Mode-D-only), exit A (legacy median-stop gated `not mode_d_enabled`), wf_grid (весь wakeup_regime уже reject-ится).

## 18. Acceptance Criteria

- работает только при `local_median_stop.enabled:true` в Mode D / Exit C;
- использует существующие `local_median_N`/`global_median`, без новых параметров окна;
- NaN → fail-open (явно ≠ exit A);
- не смешивается с `no_fresh_candidate`;
- уважает `action.mode`;
- priority `ttl > no_fresh_candidate > local_median_stop > cycle_trade_limit`; флаг — только победителю;
- diagnostic-ключ — Mode-D-only; не-D 46-keyset не изменён; добавлен Mode-D keyset-контракт;
- counter-инварианты close/LMS не взаимоисключающие;
- tester export (колонка + summary) заполняется;
- wf_grid отвергает фичу через caller_pipeline gate; старые wf_grid/tester конфиги — без регресса;
- exit A / TTL / no_fresh / cycle limit тесты зелёные.

---

Открытые архитектурные вопросы из ревью я закрыл решениями: (1) diagnostic-ключ — Mode-D-only; (2) не-D keyset не трогаем, вводим отдельный Mode-D контракт; (3) scope — production, один режим. Если согласны с этими тремя решениями, ТЗ v3 готово под implementation plan.
