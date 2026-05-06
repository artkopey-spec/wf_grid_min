# Changelog

## [Unreleased]

### Added

- **`exit_b_immediate_off`** (`trade_filter.lifecycle`): опциональный флаг для
  `exit_off_mode: "exit B"`. При `true` lifecycle завершается немедленно как
  `OFF` на баре порога `exit_off_zz_leg_count`, минуя фазу `ST_STOPPING`.
  Добавлены per-bar diagnostics `exit_b_immediate_off_triggered` (int8) и
  `exit_b_immediate_off_config` (int8 broadcast), trade-level `exit_reason`
  `"filter_exit_b_immediate_off"`, отображение в Excel (`filters_summary.params`,
  `FilterDiagnostics_100`) и summary-ключи в step_collector/runner.
  Реализовано в коммитах 1–5 (Plan v3 `exit_b_immediate_off`).
