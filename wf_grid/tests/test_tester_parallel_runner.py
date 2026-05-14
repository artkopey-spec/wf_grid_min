import argparse
import csv
import json
from pathlib import Path

import pytest

import run_configs_tester_parallel as runner


def test_parser_defaults_and_jobs_validation():
    args = runner.build_parser().parse_args([])

    assert args.jobs == min(8, runner.os.cpu_count() or 1)
    assert args.configs_dir == runner.DEFAULT_CONFIGS_DIR
    assert args.output_dir == runner.DEFAULT_OUTPUT_DIR
    assert args.csv == runner.DEFAULT_CSV
    assert args.glob == "*.y*ml"
    assert args.summary_format == "csv"
    assert args.stop_on_error is False

    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(["--jobs", "0"])

    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(["--jobs", "abc"])


def test_preflight_resolves_paths_loads_csv_once_and_sorts_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("date,open,high,low,close,volume\n", encoding="utf-8")
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "b.yaml").write_text("b: 1\n", encoding="utf-8")
    (configs_dir / "a.yml").write_text("a: 1\n", encoding="utf-8")
    output_dir = tmp_path / "out"
    calls: list[tuple[str, object]] = []

    def fake_load(path: str) -> object:
        calls.append(("load", Path(path)))
        return object()

    def fake_validate(df: object) -> object:
        calls.append(("validate", df))
        return df

    monkeypatch.setattr(runner, "load_ohlc_csv", fake_load)
    monkeypatch.setattr(runner, "validate_ohlc_data", fake_validate)

    args = argparse.Namespace(
        csv=csv_path,
        configs_dir=configs_dir,
        output_dir=output_dir,
        glob="*.y*ml",
    )

    resolved_csv, resolved_configs_dir, resolved_output_dir, configs = runner.preflight(
        args
    )

    assert resolved_csv == csv_path.resolve()
    assert resolved_configs_dir == configs_dir.resolve()
    assert resolved_output_dir == output_dir.resolve()
    assert output_dir.is_dir()
    assert [p.name for p in configs] == ["a.yml", "b.yaml"]
    assert calls[0] == ("load", csv_path.resolve())
    assert calls[1][0] == "validate"
    assert len(calls) == 2


def test_build_tasks_uses_index_stem_batch_id_and_absolute_paths(tmp_path: Path):
    configs = [tmp_path / "one.yaml", tmp_path / "two.yml"]
    for config in configs:
        config.write_text("x: 1\n", encoding="utf-8")
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("", encoding="utf-8")

    tasks = runner.build_tasks(
        configs,
        output_dir,
        csv_path,
        "20260514_120000_123_999",
    )

    assert [task.index for task in tasks] == [1, 2]
    assert [task.total for task in tasks] == [2, 2]
    assert Path(tasks[0].output_xlsx).name == (
        "0001_one_20260514_120000_123_999.xlsx"
    )
    assert Path(tasks[0].output_log).name == (
        "0001_one_20260514_120000_123_999.log"
    )
    assert Path(tasks[1].output_xlsx).name == (
        "0002_two_20260514_120000_123_999.xlsx"
    )
    assert Path(tasks[0].config_path).is_absolute()
    assert Path(tasks[0].csv_path).is_absolute()


def test_write_summary_sorts_rows_by_config_index(tmp_path: Path):
    rows = [
        {
            "config_index": 2,
            "config_path": "b.yaml",
            "status": "ok",
            "exit_code": 0,
            "started_at": "s2",
            "finished_at": "f2",
            "duration_sec": 0.2,
            "output_path": "b.xlsx",
            "log_path": "b.log",
            "error_message": "",
        },
        {
            "config_index": 1,
            "config_path": "a.yaml",
            "status": "failed",
            "exit_code": 1,
            "started_at": "s1",
            "finished_at": "f1",
            "duration_sec": 0.1,
            "output_path": "a.xlsx",
            "log_path": "a.log",
            "error_message": "boom",
        },
    ]

    json_path = runner.write_summary(tmp_path, rows, "json", "batch")
    assert [row["config_index"] for row in json.loads(json_path.read_text())] == [
        1,
        2,
    ]

    csv_path = runner.write_summary(tmp_path, rows, "csv", "batch_csv")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        assert [row["config_index"] for row in csv.DictReader(f)] == ["1", "2"]


def test_run_all_stop_on_error_stops_submitting_new_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("", encoding="utf-8")
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    configs = []
    for name in ["a.yaml", "b.yaml", "c.yaml"]:
        path = configs_dir / name
        path.write_text("x: 1\n", encoding="utf-8")
        configs.append(path)

    submitted: list[int] = []

    class FakeFuture:
        def __init__(self, task: runner.ConfigTask):
            self.task = task

        def result(self) -> dict[str, object]:
            status = "failed" if self.task.index == 1 else "ok"
            return {
                "config_index": self.task.index,
                "config_path": self.task.config_path,
                "status": status,
                "exit_code": 1 if status == "failed" else 0,
                "started_at": "start",
                "finished_at": "finish",
                "duration_sec": 0.0,
                "output_path": self.task.output_xlsx,
                "log_path": self.task.output_log,
                "error_message": "boom" if status == "failed" else "",
            }

        def cancel(self) -> bool:
            return True

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, task: runner.ConfigTask) -> FakeFuture:
            submitted.append(task.index)
            return FakeFuture(task)

    def fake_wait(pending, return_when):
        assert return_when == runner.FIRST_COMPLETED
        first = next(iter(pending))
        return {first}, set(pending) - {first}

    monkeypatch.setattr(
        runner,
        "preflight",
        lambda args: (csv_path, configs_dir, output_dir, configs),
    )
    monkeypatch.setattr(runner, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(runner, "wait", fake_wait)
    monkeypatch.setattr(runner, "_make_batch_id", lambda: "batch")
    monkeypatch.setattr(runner, "_first_non_empty_log_line", lambda path: "boom")

    args = argparse.Namespace(jobs=2, stop_on_error=True, summary_format="json")

    rows, summary_path, had_pool_or_stop = runner.run_all(args)

    assert submitted == [1, 2]
    assert [row["config_index"] for row in rows] == [1, 2]
    assert rows[0]["status"] == "failed"
    assert had_pool_or_stop is True
    assert summary_path.name == "tester_parallel_summary_batch.json"


def test_init_worker_sets_globals(monkeypatch: pytest.MonkeyPatch):
    import pandas as pd

    fake_df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    monkeypatch.setattr(runner, "load_ohlc_csv", lambda path: fake_df)
    monkeypatch.setattr(runner, "validate_ohlc_data", lambda df: df)
    monkeypatch.setattr(runner, "_WORKER_DF", None)
    monkeypatch.setattr(runner, "_WORKER_CSV", None)

    runner.init_worker("/some/data.csv")

    assert runner._WORKER_DF is fake_df
    assert runner._WORKER_CSV == "/some/data.csv"


def test_run_config_task_success_copies_df_and_writes_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import argparse as _argparse

    import pandas as pd
    import supertrend_optimizer.cli.tester as tester_mod

    original_df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    monkeypatch.setattr(runner, "_WORKER_DF", original_df)
    monkeypatch.setattr(runner, "_WORKER_CSV", "data.csv")

    output_xlsx = str(tmp_path / "0001_cfg_batch.xlsx")
    output_log = str(tmp_path / "0001_cfg_batch.log")

    received: list[pd.DataFrame] = []
    parse_args_calls: list[list[str]] = []

    def fake_parse_args(args_list):
        parse_args_calls.append(list(args_list))
        return _argparse.Namespace(
            csv="data.csv",
            config="cfg.yaml",
            out=output_xlsx,
            exact_output_path=True,
            atr=None,
            mult=None,
            mode=None,
            periods_per_year=None,
            annualization_basis=None,
            market=None,
            execution_model=None,
        )

    def fake_run_backtest_with_df(args, df, *, csv_path_for_metadata):
        received.append(df)
        return output_xlsx

    monkeypatch.setattr(tester_mod, "parse_args", fake_parse_args)
    monkeypatch.setattr(tester_mod, "run_backtest_with_df", fake_run_backtest_with_df)

    task = runner.ConfigTask(
        index=1,
        total=3,
        config_path="cfg.yaml",
        output_xlsx=output_xlsx,
        output_log=output_log,
        csv_path="data.csv",
    )

    row = runner.run_config_task(task)

    assert row["status"] == "ok"
    assert row["exit_code"] == 0
    assert row["error_message"] == ""
    assert row["config_index"] == 1
    assert parse_args_calls == [
        [
            "--csv",
            "data.csv",
            "--config",
            "cfg.yaml",
            "--out",
            output_xlsx,
            "--exact-output-path",
        ]
    ]
    assert len(received) == 1
    assert received[0] is not original_df
    pd.testing.assert_frame_equal(received[0], original_df)
    assert Path(output_log).exists()
    assert "[1/3]" in Path(output_log).read_text(encoding="utf-8")


def test_run_config_task_failure_writes_traceback_to_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import argparse as _argparse

    import pandas as pd
    import supertrend_optimizer.cli.tester as tester_mod

    monkeypatch.setattr(runner, "_WORKER_DF", pd.DataFrame({"close": [1.0]}))
    monkeypatch.setattr(runner, "_WORKER_CSV", "data.csv")

    output_xlsx = str(tmp_path / "0001_cfg_batch.xlsx")
    output_log = str(tmp_path / "0001_cfg_batch.log")

    monkeypatch.setattr(
        tester_mod,
        "parse_args",
        lambda _: _argparse.Namespace(
            csv="data.csv",
            config="cfg.yaml",
            out=output_xlsx,
            atr=None,
            mult=None,
            mode=None,
            periods_per_year=None,
            annualization_basis=None,
            market=None,
            execution_model=None,
        ),
    )

    def exploding_backtest(*args, **kwargs):
        raise ValueError("bad config value")

    monkeypatch.setattr(tester_mod, "run_backtest_with_df", exploding_backtest)

    task = runner.ConfigTask(
        index=2,
        total=5,
        config_path="cfg.yaml",
        output_xlsx=output_xlsx,
        output_log=output_log,
        csv_path="data.csv",
    )

    row = runner.run_config_task(task)

    assert row["status"] == "failed"
    assert row["exit_code"] == 1
    log_text = Path(output_log).read_text(encoding="utf-8")
    assert "ValueError" in log_text
    assert "bad config value" in log_text


def test_run_all_broken_process_pool_sets_pool_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from concurrent.futures.process import BrokenProcessPool

    csv_path = tmp_path / "data.csv"
    csv_path.write_text("", encoding="utf-8")
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    configs = []
    for name in ["a.yaml", "b.yaml"]:
        p = configs_dir / name
        p.write_text("x: 1\n", encoding="utf-8")
        configs.append(p)

    class BrokenFuture:
        def __init__(self, task: runner.ConfigTask):
            self.task = task

        def result(self) -> dict:
            raise BrokenProcessPool("worker crash")

        def cancel(self) -> bool:
            return True

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, fn, task: runner.ConfigTask) -> BrokenFuture:
            return BrokenFuture(task)

    def fake_wait(pending, return_when):
        first = next(iter(pending))
        return {first}, set(pending) - {first}

    monkeypatch.setattr(
        runner,
        "preflight",
        lambda args: (csv_path, configs_dir, output_dir, configs),
    )
    monkeypatch.setattr(runner, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(runner, "wait", fake_wait)
    monkeypatch.setattr(runner, "_make_batch_id", lambda: "batch")

    args = argparse.Namespace(jobs=2, stop_on_error=False, summary_format="json")

    rows, summary_path, had_pool_or_stop = runner.run_all(args)

    assert had_pool_or_stop is True
    assert len(rows) >= 1
    assert all(row["status"] == "failed" for row in rows)
    assert all(row["exit_code"] == 1 for row in rows)
