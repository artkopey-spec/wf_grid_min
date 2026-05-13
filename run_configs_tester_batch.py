"""
Sequential runner for the Tester CLI over a directory of tester YAML configs.

Each config is executed with the main production entrypoint:

    python -m supertrend_optimizer.cli.tester --csv data.csv --config <cfg> --out <xlsx>

The script writes one XLSX and one log per config, plus a CSV/JSON summary.
It is intentionally separate from ``donor TESTER/run_batch_tester.py``: this
runner is for multi-config operations, while the donor TESTER launcher preserves
the historical equal_blocks + legacy two-run workflow for one config.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIGS_DIR = PROJECT_ROOT / "config tester"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results tester"
DEFAULT_CSV = PROJECT_ROOT / "data.csv"
TS_FMT = "%Y%m%d_%H%M%S"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Tester CLI sequentially for multiple YAML configs."
    )
    parser.add_argument("--configs-dir", type=Path, default=DEFAULT_CONFIGS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--glob", default="*.y*ml")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--summary-format", choices=("csv", "json"), default="csv")
    return parser


def discover_configs(configs_dir: Path, pattern: str) -> list[Path]:
    configs = sorted(configs_dir.glob(pattern), key=lambda p: p.name)
    if not configs:
        raise SystemExit(f"No configs found in {configs_dir} for pattern {pattern!r}")
    return configs


def _resolve_existing_file(path: Path, label: str) -> Path:
    resolved = path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    if not resolved.is_file():
        raise SystemExit(f"{label} not found: {resolved}")
    return resolved


def _resolve_existing_dir(path: Path, label: str) -> Path:
    resolved = path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    if not resolved.is_dir():
        raise SystemExit(f"{label} not found: {resolved}")
    return resolved


def build_command(
    python_exe: str,
    csv_path: Path,
    config_path: Path,
    output_path: Path,
) -> list[str]:
    return [
        python_exe,
        "-m",
        "supertrend_optimizer.cli.tester",
        "--csv",
        str(csv_path.resolve()),
        "--config",
        str(config_path.resolve()),
        "--out",
        str(output_path.resolve()),
    ]


def _env_with_donor() -> dict[str, str]:
    env = os.environ.copy()
    donor = str((PROJECT_ROOT / "donor").resolve())
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = donor if not existing else donor + os.pathsep + existing
    return env


def _read_log_prefix(log_path: Path, limit: int = 500) -> str:
    try:
        return log_path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def _first_non_empty_log_line(log_path: Path) -> str:
    try:
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if text:
                    return text
    except OSError:
        return "(log unavailable)"
    return "(empty log)"


def write_summary(
    output_dir: Path, rows: list[dict[str, object]], summary_format: str
) -> Path:
    ts = datetime.now().strftime(TS_FMT)
    path = output_dir / f"tester_batch_summary_{ts}.{summary_format}"
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
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    else:
        with path.open("w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    return path


def run_all(args: argparse.Namespace) -> tuple[list[dict[str, object]], Path]:
    csv_path = _resolve_existing_file(args.csv, "CSV")
    configs_dir = _resolve_existing_dir(args.configs_dir, "Configs directory")
    output_dir = args.output_dir if args.output_dir.is_absolute() else (
        PROJECT_ROOT / args.output_dir
    ).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    configs = discover_configs(configs_dir, args.glob)
    env = _env_with_donor()
    results: list[dict[str, object]] = []
    total = len(configs)

    print(f"Found {total} tester config(s).")
    for idx, config_path in enumerate(configs, start=1):
        run_ts = datetime.now().strftime(TS_FMT)
        stem = f"{config_path.stem}_{run_ts}"
        output_xlsx = output_dir / f"{stem}.xlsx"
        output_log = output_dir / f"{stem}.log"
        cmd = build_command(args.python_exe, csv_path, config_path, output_xlsx)

        print(f"[{idx}/{total}] {config_path.name} ...")
        started_at = datetime.now()
        t0 = time.perf_counter()
        with output_log.open("w", encoding="utf-8") as log_file:
            proc = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                check=False,
            )
        finished_at = datetime.now()
        duration_sec = round(time.perf_counter() - t0, 3)
        status = "ok" if proc.returncode == 0 else "failed"
        error_message = "" if status == "ok" else _read_log_prefix(output_log)
        row = {
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
        results.append(row)

        if status == "ok":
            print(f"  OK -> {output_xlsx.name}")
        else:
            print(
                f"  FAILED (exit_code={proc.returncode}) -> "
                f"{_first_non_empty_log_line(output_log)}"
            )
            if args.stop_on_error:
                break

    summary_path = write_summary(output_dir, results, args.summary_format)
    return results, summary_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results, summary_path = run_all(args)
    failed = sum(1 for row in results if row["status"] == "failed")
    print(f"\nDone: {len(results) - failed} OK, {failed} failed")
    print(f"Summary: {summary_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
