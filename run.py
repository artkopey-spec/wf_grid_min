"""
WF Grid Search — точка запуска pipeline.

Использование:
    python run.py
    python run.py --config config.yaml --output results.xlsx
    python run.py --sequential
    python run.py --max-workers 4
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Добавляем donor в path для импорта supertrend_optimizer
sys.path.insert(0, str(Path(__file__).parent / "donor"))

from wf_grid.pipeline.orchestrator import run_grid_pipeline


def main():
    parser = argparse.ArgumentParser(description="WF Grid Search v3")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--output", default=None, help="Output XLSX path (optional)")
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Force sequential execution "
             "(overrides config.yaml execution.parallel_enabled).",
    )
    def _positive_int(value: str) -> int:
        try:
            n = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"--max-workers must be an integer >= 1, got {value!r}"
            )
        if n < 1:
            raise argparse.ArgumentTypeError(
                f"--max-workers must be >= 1, got {n}"
            )
        return n

    parser.add_argument(
        "--max-workers",
        type=_positive_int,
        default=None,
        dest="max_workers",
        help="Override execution.max_workers for this run only (must be >= 1).",
    )
    args = parser.parse_args()

    # Resolve CLI overrides: --sequential maps to parallel_enabled=False;
    # absent flag leaves the decision to config.yaml (parallel_enabled=None).
    parallel_enabled: bool | None = False if args.sequential else None

    print(f"Config:  {args.config}")
    print(f"Output:  {args.output or '(auto)'}")
    if parallel_enabled is not None:
        print(f"Mode:    sequential (--sequential flag)")
    if args.max_workers is not None:
        print(f"Workers: {args.max_workers}")
    print()

    result = run_grid_pipeline(
        config_path=args.config,
        output_path=args.output,
        parallel_enabled=parallel_enabled,
        max_workers=args.max_workers,
    )

    if result.error:
        print(f"\n[ERROR] Pipeline failed: {result.error}")
        sys.exit(1)

    print(f"\n[OK] XLSX saved to: {result.output_path}")

    if result.diagnostics:
        d = result.diagnostics
        print(f"\nGrid:        {d.grid_size} points x {d.n_wf_steps} WF steps")
        print(f"Step status: {d.step_status_counts}")
        print(f"Tier dist:   {d.tier_counts}")
        print(f"\nTop-5 ranked:")
        for entry in d.top5_ranked:
            print(f"  {entry}")
        print(f"\nTiming:")
        for stage, t in d.timings.items():
            print(f"  {stage:25s}: {t:.2f}s")


if __name__ == "__main__":
    main()
