# Time filter multi-window migration

## Изменение

Production schedule переведён с legacy `window` на canonical `windows` и
Time-only mode:

```yaml
trade_filter:
  enabled: true
  zigzag:
    enabled: false
  volume:
    enabled: false
  time_filter:
    enabled: true
    windows:
      - "09:00-12:00"
      - "16:00-18:00"
  wakeup_regime:
    enabled: false
```

Active Time использует calendar-cycle reset. Смена даты или окна закрывает
предыдущий цикл даже без наблюдаемого outside-бара между ними.

## Ожидаемая дельта

- Time-off результаты не меняются.
- Dense same-date single-window сохраняет прежнюю position parity.
- Date/window gaps могут изменить positions, returns, trades и PnL.
- Gap PnL сохраняется до следующего доступного open-to-open исполнения.
- Last-bar reset не считается исполненным выходом.

## Перед запуском

1. Удалить active `type: zigzag_st_mode`.
2. Установить `zigzag.enabled: false`.
3. Установить `volume.enabled: false`.
4. Установить `wakeup_regime.enabled: false`.
5. Проверить, что nested wakeup Volume template не активен.
6. Проверить, что входные данные имеют монотонный `DatetimeIndex`.
7. Volume column для Time-only не требуется.

## Проверка

1. Загрузить `config.yaml` через WF loader.
2. Загрузить `config_tester.yaml` через Tester loader.
3. Выполнить direct Time-only smoke.
4. Выполнить короткий sequential и parallel WF smoke.
5. Выполнить canonical Tester legacy smoke.
6. Убедиться, что canonical batch показывает documented equal-blocks skip.
7. Проверить Time counters, exit reasons и Tester_Config snapshots.

## Rollback

Rollback выполняется конфигурацией:

```yaml
trade_filter:
  enabled: false
```

Это отключает все trade-filter capabilities и возвращает Time-off runtime.
Для сравнения старой transition semantics используется только offline
`build_legacy_time_filter_runtime`; production runtime на неё не переключается.
