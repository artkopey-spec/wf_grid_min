"""
Compatibility launcher for the canonical Phase 2 SuperTrend Tester batch CLI.

The implementation lives in ``donor TESTER/run_batch_tester.py``.  Keeping this
thin root wrapper preserves the existing ``run_tester.bat`` UX:

    python run_batch_tester.py --csv data.csv --config config_tester.yaml
"""

from pathlib import Path
import runpy
import sys


def main() -> None:
    canonical = Path(__file__).resolve().parent / "donor TESTER" / "run_batch_tester.py"
    if not canonical.is_file():
        print(
            f"Error: canonical tester entrypoint not found: {canonical}",
            file=sys.stderr,
        )
        sys.exit(1)

    runpy.run_path(str(canonical), run_name="__main__")


if __name__ == "__main__":
    main()
