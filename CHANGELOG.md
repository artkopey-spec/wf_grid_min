# Changelog

## [Unreleased]

### Added

- **`time_filter`** (`trade_filter.time_filter`): опциональный фильтр торгового
  времени. При `enabled: true` ограничивает входы в позицию заданным окном
  `HH:MM-HH:MM` (контракт `[start, end)`), секунды индекса игнорируются,
  timezone берётся из индекса as-is без конвертации. При выходе из окна
  поведение эквивалентно `daily_reset`: FSM wipe, сброс lifecycle-счётчиков,
  закрытие позиции на `open(t+1)`, сброс ZigZag candidate-state. Приоритет
  reset-событий: `daily_reset` > `time_filter_reset`. Добавлены per-bar
  diagnostics (`time_filter_enabled`, `time_filter_in_window`,
  `time_filter_reset_event`), trade-level `exit_reason` `"filter_time_reset"`,
  summary-ключи (`time_filter_reset_count`, `time_filter_bars_in_window`,
  `time_filter_bars_out_window`, `time_filter_enabled`), отображение в Excel
  (`FilterDiagnostics_100`, `filters_summary`). При `enabled: false` — полный
  no-op, baseline bit-identical. Кросс-платформенная реализация в
  `donor/supertrend_optimizer/` (единый код для WF Grid и Tester).
  Спецификация: `docs/time_filter_spec.md`.

- **`exit_b_immediate_off`** (`trade_filter.lifecycle`): опциональный флаг для
  `exit_off_mode: "exit B"`. При `true` lifecycle завершается немедленно как
  `OFF` на баре порога `exit_off_zz_leg_count`, минуя фазу `ST_STOPPING`.
  Добавлены per-bar diagnostics `exit_b_immediate_off_triggered` (int8) и
  `exit_b_immediate_off_config` (int8 broadcast), trade-level `exit_reason`
  `"filter_exit_b_immediate_off"`, отображение в Excel (`filters_summary.params`,
  `FilterDiagnostics_100`) и summary-ключи в step_collector/runner.
  Реализовано в коммитах 1–5 (Plan v3 `exit_b_immediate_off`).
