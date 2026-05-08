"""
Batch launcher for run.py over multiple YAML configs.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIGS_DIR = Path(r"C:\3.1_wf_grid\config")
DEFAULT_OUTPUT_DIR = Path(r"C:\3.1_wf_grid\results\batch")
TS_FMT = "%Y%m%d_%H%M%S"


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--max-workers должен быть целым числом >= 1, получено: {value!r}"
        ) from exc
    if n < 1:
        raise argparse.ArgumentTypeError(
            f"--max-workers должен быть >= 1, получено: {n}"
        )
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Последовательный запуск run.py для нескольких конфигов."
    )
    parser.add_argument(
        "--configs-dir",
        type=Path,
        default=DEFAULT_CONFIGS_DIR,
        help=f"Каталог с конфигами (по умолчанию: {DEFAULT_CONFIGS_DIR}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Каталог для XLSX/логов/сводки (по умолчанию: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--glob",
        default="*.y*ml",
        help="Шаблон поиска конфигов (по умолчанию: *.y*ml).",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Остановить батч после первой ошибки.",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Прокинуть --sequential в каждый запуск run.py.",
    )
    parser.add_argument(
        "--max-workers",
        type=_positive_int,
        default=None,
        dest="max_workers",
        help="Прокинуть --max-workers N в каждый запуск run.py (N >= 1).",
    )
    parser.add_argument(
        "--python-exe",
        default="python",
        help="Интерпретатор Python для запуска run.py (по умолчанию: python).",
    )
    parser.add_argument(
        "--summary-format",
        choices=("csv", "json"),
        default="csv",
        help="Формат итоговой сводки: csv или json (по умолчанию: csv).",
    )
    return parser


def discover_configs(configs_dir: Path, pattern: str) -> list[Path]:
    configs = sorted(configs_dir.glob(pattern), key=lambda p: p.name)
    if not configs:
        print(
            f"[ERROR] В каталоге '{configs_dir}' не найдено конфигов по шаблону '{pattern}'."
        )
        raise SystemExit(2)

    print("Найденные конфиги:")
    for cfg in configs:
        print(f" - {cfg}")
    print()
    return configs


def build_command(
    python_exe: str,
    config_path: Path,
    output_path: Path,
    sequential: bool,
    max_workers: int | None,
) -> list[str]:
    cmd = [
        python_exe,
        "run.py",
        "--config",
        str(config_path.resolve()),
        "--output",
        str(output_path.resolve()),
    ]
    if sequential:
        cmd.append("--sequential")
    if max_workers is not None:
        cmd.extend(["--max-workers", str(max_workers)])
    return cmd


def _build_run_paths(output_dir: Path, config_path: Path, timestamp: str) -> tuple[Path, Path]:
    base_name = f"{config_path.stem}_{timestamp}"
    output_xlsx = output_dir / f"{base_name}.xlsx"
    output_log = output_dir / f"{base_name}.log"
    return output_xlsx, output_log


def _first_non_empty_log_line(log_path: Path) -> str:
    try:
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if text:
                    return text
    except OSError:
        return "(лог недоступен)"
    return "(нет текста в логе)"


def _read_log_prefix(log_path: Path, limit: int = 500) -> str:
    try:
        with log_path.open("r", encoding="utf-8") as f:
            return f.read(limit)
    except OSError:
        return ""


def write_summary(
    output_dir: Path, results: list[dict[str, object]], summary_format: str
) -> Path:
    ts = datetime.now().strftime(TS_FMT)
    summary_path = output_dir / f"batch_summary_{ts}.{summary_format}"
    fields = [
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

    if summary_format == "csv":
        with summary_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(results)
    else:
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    return summary_path


def run_all(args: argparse.Namespace) -> tuple[list[dict[str, object]], Path]:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    configs = discover_configs(args.configs_dir, args.glob)

    results: list[dict[str, object]] = []
    total = len(configs)

    for idx, config_path in enumerate(configs, start=1):
        print(f"[{idx}/{total}] {config_path.name} ...")
        run_ts = datetime.now().strftime(TS_FMT)
        output_xlsx, output_log = _build_run_paths(output_dir, config_path, run_ts)

        cmd = build_command(
            python_exe=args.python_exe,
            config_path=config_path,
            output_path=output_xlsx,
            sequential=args.sequential,
            max_workers=args.max_workers,
        )

        started_at = datetime.now()
        t0 = time.perf_counter()
        with output_log.open("w", encoding="utf-8") as log_file:
            proc = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=False,
            )
        finished_at = datetime.now()
        duration_sec = round(time.perf_counter() - t0, 3)

        status = "ok" if proc.returncode == 0 else "failed"
        error_message = "" if status == "ok" else _read_log_prefix(output_log, 500)

        result = {
            "config_path": str(config_path.resolve()),
            "status": status,
            "exit_code": proc.returncode,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "duration_sec": duration_sec,
            "output_path": str(output_xlsx.resolve()),
            "log_path": str(output_log.resolve()),
            "error_message": error_message,
        }
        results.append(result)

        if status == "ok":
            print(f"  OK -> {output_xlsx}")
        else:
            first_line = _first_non_empty_log_line(output_log)
            print(f"  FAILED (exit_code={proc.returncode}) -> {first_line}")
            if args.stop_on_error:
                print("  Остановка по флагу --stop-on-error.")
                break

    summary_path = write_summary(output_dir, results, args.summary_format)
    return results, summary_path


def print_totals(results: list[dict[str, object]], summary_path: Path) -> None:
    total = len(results)
    failed = sum(1 for r in results if r["status"] == "failed")
    ok = total - failed
    print("\n" + "=" * 30)
    print(f"ИТОГО: {total} прогонов | OK: {ok} | FAILED: {failed}")
    print(f"Сводка: {summary_path}")
    print("=" * 30)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.max_workers is not None and args.max_workers < 1:
        print("[ERROR] --max-workers должен быть >= 1")
        raise SystemExit(2)

    results, summary_path = run_all(args)
    print_totals(results, summary_path)

    if any(r["status"] == "failed" for r in results):
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
