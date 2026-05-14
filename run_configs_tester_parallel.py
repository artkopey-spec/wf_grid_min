"""
Parallel runner for the Tester CLI over a directory of tester YAML configs.

Each worker process loads the OHLC CSV once in its initializer, then processes
multiple configs in-process through ``supertrend_optimizer.cli.tester``.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import sys
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent
DONOR_ROOT = PROJECT_ROOT / "donor"

if str(DONOR_ROOT) not in sys.path:
    sys.path.insert(0, str(DONOR_ROOT))

from supertrend_optimizer.data.loader import load_ohlc_csv
from supertrend_optimizer.data.validator import validate_ohlc_data


DEFAULT_CONFIGS_DIR = PROJECT_ROOT / "config tester"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results tester"
DEFAULT_CSV = PROJECT_ROOT / "data.csv"
SUMMARY_FIELDS = [
    "config_index",
    "config_path",
    "status",
    "exit_code",
    "started_at",
    "finished_at",
    "duration_sec",
    "output_path",
    "log_path",
    "error_message",
]

_WORKER_DF: pd.DataFrame | None = None
_WORKER_CSV: str | None = None


@dataclass(frozen=True)
class ConfigTask:
    index: int
    total: int
    config_path: str
    output_xlsx: str
    output_log: str
    csv_path: str


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--jobs must be a positive integer, got: {value!r}"
        )
    if n < 1:
        raise argparse.ArgumentTypeError("--jobs must be >= 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Tester CLI in parallel for multiple YAML configs."
    )
    parser.add_argument(
        "--jobs",
        type=_positive_int,
        default=min(8, os.cpu_count() or 1),
    )
    parser.add_argument("--configs-dir", type=Path, default=DEFAULT_CONFIGS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--glob", default="*.y*ml")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--summary-format", choices=("csv", "json"), default="csv")
    return parser


def _resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def _resolve_existing_file(path: Path, label: str) -> Path:
    resolved = _resolve_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def _resolve_existing_dir(path: Path, label: str) -> Path:
    resolved = _resolve_path(path)
    if not resolved.is_dir():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def discover_configs(configs_dir: Path, pattern: str) -> list[Path]:
    configs = sorted(configs_dir.glob(pattern), key=lambda p: p.name)
    if not configs:
        raise ValueError(
            f"No configs found in {configs_dir} for pattern {pattern!r}"
        )
    return configs


def _make_batch_id() -> str:
    now = datetime.now()
    millis = now.microsecond // 1000
    return f"{now:%Y%m%d_%H%M%S}_{millis:03d}_{os.getpid()}"


def _safe_stem(path: Path) -> str:
    return path.stem.replace("/", "_").replace("\\", "_")


def build_tasks(
    configs: list[Path],
    output_dir: Path,
    csv_path: Path,
    batch_id: str,
) -> list[ConfigTask]:
    total = len(configs)
    tasks: list[ConfigTask] = []
    for index, config_path in enumerate(configs, start=1):
        prefix = f"{index:04d}_{_safe_stem(config_path)}_{batch_id}"
        tasks.append(
            ConfigTask(
                index=index,
                total=total,
                config_path=str(config_path.resolve()),
                output_xlsx=str((output_dir / f"{prefix}.xlsx").resolve()),
                output_log=str((output_dir / f"{prefix}.log").resolve()),
                csv_path=str(csv_path.resolve()),
            )
        )
    return tasks


def preflight(args: argparse.Namespace) -> tuple[Path, Path, Path, list[Path]]:
    csv_path = _resolve_existing_file(args.csv, "CSV")
    configs_dir = _resolve_existing_dir(args.configs_dir, "Configs directory")
    output_dir = _resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    configs = discover_configs(configs_dir, args.glob)

    df = load_ohlc_csv(str(csv_path))
    df = validate_ohlc_data(df)
    del df

    return csv_path, configs_dir, output_dir, configs


def init_worker(csv_path: str) -> None:
    global _WORKER_DF, _WORKER_CSV

    df = load_ohlc_csv(csv_path)
    df = validate_ohlc_data(df)

    _WORKER_DF = df
    _WORKER_CSV = csv_path


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_config_task(task: ConfigTask) -> dict[str, object]:
    if _WORKER_DF is None or _WORKER_CSV is None:
        raise RuntimeError("Worker CSV was not initialized")

    from supertrend_optimizer.cli.tester import parse_args, run_backtest_with_df

    started_at = _iso_now()
    t0 = perf_counter()
    log_path = Path(task.output_log)
    actual_output = task.output_xlsx
    status = "ok"
    exit_code = 0
    error_summary = ""

    with log_path.open("w", encoding="utf-8") as log_file:
        with contextlib.redirect_stdout(log_file), contextlib.redirect_stderr(log_file):
            try:
                print(f"[{task.index}/{task.total}] {Path(task.config_path).name}")
                df = _WORKER_DF.copy(deep=True)
                tester_args = parse_args(
                    [
                        "--csv",
                        task.csv_path,
                        "--config",
                        task.config_path,
                        "--out",
                        task.output_xlsx,
                        "--exact-output-path",
                    ]
                )
                actual_output = run_backtest_with_df(
                    tester_args,
                    df,
                    csv_path_for_metadata=task.csv_path,
                )
            except Exception as exc:
                traceback.print_exc()
                status = "failed"
                exit_code = 1
                error_summary = f"{type(exc).__name__}: {exc}"

    finished_at = _iso_now()
    error_message = ""
    if status == "failed":
        error_message = _read_log_prefix(task.output_log) or error_summary

    return {
        "config_index": task.index,
        "config_path": task.config_path,
        "status": status,
        "exit_code": exit_code,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": round(perf_counter() - t0, 3),
        "output_path": str(Path(actual_output).resolve()),
        "log_path": task.output_log,
        "error_message": error_message,
    }


def _read_log_prefix(log_path: str, limit: int = 500) -> str:
    try:
        return Path(log_path).read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def _first_non_empty_log_line(log_path: str) -> str:
    try:
        with Path(log_path).open("r", encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if text:
                    return text
    except OSError:
        return "(log unavailable)"
    return "(empty log)"


def _failed_row_from_exception(task: ConfigTask, exc: BaseException) -> dict[str, object]:
    error_message = _read_log_prefix(task.output_log) or f"{type(exc).__name__}: {exc}"
    return {
        "config_index": task.index,
        "config_path": task.config_path,
        "status": "failed",
        "exit_code": 1,
        "started_at": "",
        "finished_at": _iso_now(),
        "duration_sec": 0.0,
        "output_path": task.output_xlsx,
        "log_path": task.output_log,
        "error_message": error_message,
    }


def write_summary(
    output_dir: Path,
    rows: list[dict[str, object]],
    summary_format: str,
    batch_id: str,
) -> Path:
    rows = sorted(rows, key=lambda row: int(row["config_index"]))
    path = output_dir / f"tester_parallel_summary_{batch_id}.{summary_format}"
    if summary_format == "csv":
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    else:
        with path.open("w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    return path


def _submit_until_full(
    executor: ProcessPoolExecutor,
    task_iter: Any,
    pending: dict[Any, ConfigTask],
    jobs: int,
) -> None:
    while len(pending) < jobs:
        try:
            task = next(task_iter)
        except StopIteration:
            return
        future = executor.submit(run_config_task, task)
        pending[future] = task


def run_all(args: argparse.Namespace) -> tuple[list[dict[str, object]], Path, bool]:
    csv_path, _configs_dir, output_dir, configs = preflight(args)
    batch_id = _make_batch_id()
    tasks = build_tasks(configs, output_dir, csv_path, batch_id)
    task_iter = iter(tasks)
    pending: dict[Any, ConfigTask] = {}
    results: list[dict[str, object]] = []
    pool_error = False
    stop_scheduling = False

    print(f"Found {len(tasks)} tester config(s).")
    print(f"Jobs: {args.jobs}")
    print(f"Batch id: {batch_id}")

    try:
        with ProcessPoolExecutor(
            max_workers=args.jobs,
            initializer=init_worker,
            initargs=(str(csv_path),),
        ) as executor:
            _submit_until_full(executor, task_iter, pending, args.jobs)

            while pending:
                done, _not_done = wait(
                    pending,
                    return_when=FIRST_COMPLETED,
                )

                for future in done:
                    task = pending.pop(future)
                    try:
                        row = future.result()
                    except BrokenProcessPool as exc:
                        pool_error = True
                        stop_scheduling = True
                        row = _failed_row_from_exception(task, exc)
                        print(
                            "Pool failed while processing "
                            f"{Path(task.config_path).name}: {exc}"
                        )
                    except Exception as exc:
                        pool_error = True
                        stop_scheduling = True
                        row = _failed_row_from_exception(task, exc)
                        print(
                            f"Pool-level failure [{task.index}/{task.total}] "
                            f"{Path(task.config_path).name}: "
                            f"{type(exc).__name__}: {exc}"
                        )
                    else:
                        if row["status"] == "failed":
                            if args.stop_on_error:
                                stop_scheduling = True
                            print(
                                f"FAILED [{task.index}/{task.total}] "
                                f"{Path(task.config_path).name}: "
                                f"{_first_non_empty_log_line(task.output_log)}"
                            )
                        else:
                            print(
                                f"OK [{task.index}/{task.total}] "
                                f"{Path(task.config_path).name}"
                            )
                    results.append(row)

                if not stop_scheduling:
                    _submit_until_full(executor, task_iter, pending, args.jobs)

                if pool_error:
                    for future in pending:
                        future.cancel()
                    break

    except BrokenProcessPool as exc:
        pool_error = True
        stop_scheduling = True
        print(f"Process pool failed: {exc}")
        for future, task in list(pending.items()):
            if future.done():
                try:
                    results.append(future.result())
                except Exception as task_exc:
                    results.append(_failed_row_from_exception(task, task_exc))
            else:
                future.cancel()
    except KeyboardInterrupt:
        pool_error = True
        stop_scheduling = True
        print("Interrupted; stopping scheduling and cancelling pending work.")
        for future in pending:
            future.cancel()
    finally:
        try:
            summary_path = write_summary(
                output_dir,
                results,
                args.summary_format,
                batch_id,
            )
        except OSError as exc:
            print(f"Warning: could not write summary: {exc}")
            summary_path = (
                output_dir
                / f"tester_parallel_summary_{batch_id}.{args.summary_format}"
            )

    return results, summary_path, pool_error or stop_scheduling


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        results, summary_path, had_pool_or_stop = run_all(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    failed = sum(1 for row in results if row["status"] == "failed")
    ok = len(results) - failed
    print(f"\nDone: {ok} OK, {failed} failed")
    print(f"Summary: {summary_path}")
    return 1 if failed or had_pool_or_stop else 0


if __name__ == "__main__":
    raise SystemExit(main())
