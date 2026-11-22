# Продолжение работы над WF Grid

Состояние проекта описано на 22.11.2026. Последний коммит с изменениями кода на момент переноса — `e918bb5` в ветке `main`. WF Grid подбирает параметры SuperTrend по скользящим обучающим и проверочным окнам. TESTER запускает бэктест для заданного профиля и выгружает диагностику в Excel.

## Устройство проекта

| Компонент | Где смотреть |
|---|---|
| Walk-forward подбор по сетке | [run.py](run.py) → [wf_grid/pipeline/orchestrator.py](wf_grid/pipeline/orchestrator.py) → [wf_grid/wf/step_executor.py](wf_grid/wf/step_executor.py) |
| Независимый запуск тестера | [run_batch_tester.py](run_batch_tester.py) → [donor TESTER/run_batch_tester.py](donor%20TESTER/run_batch_tester.py), профиль [config_tester.yaml](config_tester.yaml) |
| Движок SuperTrend, позиции, PnL и исполнение | [donor/supertrend_optimizer](donor/supertrend_optimizer), в частности [engine/run.py](donor/supertrend_optimizer/engine/run.py) и [core/backtest.py](donor/supertrend_optimizer/core/backtest.py) |
| Регрессии | [pyproject.toml](pyproject.toml), [wf_grid/tests](wf_grid/tests) и [donor TESTER/tests](donor%20TESTER/tests) |
| ТЗ, приёмка и история изменений | [docs](docs) и [CHANGELOG.md](CHANGELOG.md). В `docs/` есть несколько редакций одних и тех же ТЗ |

## Запуск в Windows

Команды выполняются из корня проекта в PowerShell. При переносе проверялся Python 3.14.6. Корневой `requirements.txt` перечисляет только XlsxWriter, поэтому в инструкции также указаны зависимости кода и тестов. При клонировании через Git восстановите в корне `data.csv` и `data 1.csv` из полного архива рабочей папки.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt numpy pandas pyyaml openpyxl pytest
.\.venv\Scripts\python.exe run.py --config config.yaml
```

Для последовательной диагностики добавьте `--sequential`. Перед полным прогоном проверьте настройки сетки и входного CSV в [config.yaml](config.yaml).

В `config_tester.yaml` после числа 18 в поле `supertrend.atr_period` стоит обратный апостроф. YAML читает поле как строку, и тестер завершается с `TypeError` при расчёте прогрева. Создайте локальную копию профиля с целым числом `18`, если такой копии ещё нет. С этим исправлением тестер сформировал два XLSX-отчёта для `data.csv`.

```powershell
if (-not (Test-Path -LiteralPath 'config_tester.portable.yaml')) {
    $testerProfile = Get-Content -LiteralPath 'config_tester.yaml' -Raw
    $testerProfile.Replace('atr_period: 18' + [char]96, 'atr_period: 18') |
        Set-Content -LiteralPath 'config_tester.portable.yaml' -Encoding utf8 -NoNewline
}
.\.venv\Scripts\python.exe run_batch_tester.py --csv data.csv --config config_tester.portable.yaml
```

В корневых профилях встречаются комментарии про старый снимок Si. По аудиту от 21.11.2026 текущие корневые файлы относятся к SBER. `data.csv` содержит полную историю (190 620 баров, 1 112 дат), `data 1.csv` задаёт разрешённые даты (820 уникальных дат). Используйте фактический заголовок, тикер и диапазон данных перед сравнением прогонов.

## Последние решения и проверка

- **Торговые даты в WF Grid.** Основа — [ТЗ v2](docs/trade_date_filter_spec_v2.md) и [приёмка](docs/trade_date_filter_acceptance.md). [Исходный аудит](docs/audit_trade_dates_20261121.md) объясняет, почему отсев строк CSV меняет расчёт и почему важны close, комиссии и границы окон. В корневом профиле фильтр дат выключен. Для включения нужны оба флага `trade_filter.enabled` и `trade_filter.date_filter.enabled`. Активный фильтр сохраняет шкалу исходной истории, проверяет дату исполнения и закрывает остаток позиции по close пограничного бара с причиной `date_filter_close`. Для самостоятельного TESTER эти требования не входили в эту поставку.
- **Отбор и проверка параметров.** Штатный итоговый рейтинг фильтра дат ретроспективный и использует агрегированные OOS-результаты. Запрос от 21–22 ноября о выборе одной пары по всем train-окнам обсуждался отдельным экспериментом в чате «Проверь настройки прогона данных». Если обучающие интервалы перекрывают ранние WF-окна, проверка там не является независимым подтверждением общей пары. Для будущей независимой оценки требуется заранее зафиксированный протокол выбора и отдельный контрольный участок. Для причинного сценария также требуется установить, когда список дат и календарь реально доступны.
- **Подтверждение разворота и фильтры времени.** Для тестера см. [подтверждение разворота](docs/st_reversal_confirmation_usage.md). Для нескольких окон времени см. [спецификацию time filter](docs/time_filter_spec.md) и [план обновления v3](docs/time_filter_multi_window_time_only_upgrade_plan_v3.md). Поведение разных режимов сверяйте по фактическому конфигу и тестам.
- **Проверка при переносе.** Оба CLI отвечают на `--help`. Целевой набор календарных тестов прошёл: 78 тестов. Полную матрицу не запускали.

```powershell
.\.venv\Scripts\python.exe -m pytest wf_grid/tests/test_trade_calendar.py wf_grid/tests/test_date_execution.py wf_grid/tests/test_date_filter_pipeline.py -q
```

## Исследования и незакрытые вопросы

- В локальном `outputs/` сохранены отдельные эксперименты по SBER, MIX, Si, ZigZag, возврату цены к закрытию и послеторговым движениям. В архивах переноса начните с `outputs/zz_three_hypotheses_20261021/deliverables/` и `outputs/zz_predictive_day_reset_20261021/deliverables/`.
- В сравнении ZZ сильнейший зафиксированный кандидат SBER имел PF 4,56 при 27 сделках на всей истории. Поздний вход в 18:50 дал слишком мало сделок для вывода. Эффект RVOL в исследовании следующего открытия неоднозначен, особенно на SBER и MIX. Эти результаты годятся для выбора кандидатов на дальнейшую проверку.
- Подбор SBER на полной истории (`outputs/sber_full_20261021/deliverables/`) использует отдельный минутный источник и не содержит независимой проверки. Старые исследования ссылаются в том числе на данные вне рабочей папки. Для повторения сверяйте источник и SHA-256 в соответствующем отчёте.
- Подтверждённая свежая проблема воспроизведения — тип `atr_period` в корневом профиле тестера. Следующую продуктовую задачу пользователь пока не выбрал.

## История и перенос

- Локальные архивы лежат в `_transfer_20261122/`. Для истории откройте `_codex_history/INDEX.md` из `wf_grid-context.zip`. Указатель ведёт к 69 чатам проекта. В этом же ZIP есть текстовые ТЗ и отчёты исследований.
- `wf_grid-project.zip` содержит рабочую папку на дату снимка, включая данные и результаты вне Git. Если у вас уже есть клон, восстановите в нём нужные локальные данные и результаты из ZIP.
- `wf_grid-git.bundle` содержит историю `main` до коммита `e918bb5`. Его можно клонировать локально командой `git clone <путь-к-файлу-bundle> wf_grid`.

## Первое сообщение новой модели

> Прочитай HANDOFF.md и сверь его с текущим кодом. Покажи, как запустить WF Grid и TESTER и какие решения уже приняты по фильтру дат и проверке параметров. Если код, документация и архив чатов расходятся, укажи где. Пока ничего не меняй.
