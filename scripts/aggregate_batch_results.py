"""
Aggregate batch optimization results.

Reads all *.xlsx files from results/batch/, extracts the rank-1 row
from each file's 'summary' sheet, and writes a consolidated xlsx.
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BATCH_DIR = Path(__file__).resolve().parent.parent / "results" / "batch"
SUMMARY_SHEET = "summary"
RANK_COLUMN = "grid_rank"
SOURCE_COLUMN = "source_file"


def _is_batch_summary_output(path: Path) -> bool:
    """Skip our own aggregation files so re-runs do not merge summaries into summaries."""
    name = path.name
    return name.startswith("batch_summary_") and name.lower().endswith(".xlsx")


def collect_rank1_rows(batch_dir: Path) -> tuple[list[pd.DataFrame], list[str]]:
    xlsx_files = sorted(
        f
        for f in batch_dir.glob("*.xlsx")
        if not f.name.startswith("~$") and not _is_batch_summary_output(f)
    )

    if not xlsx_files:
        print(f"No xlsx files found in {batch_dir}")
        return [], []

    rows: list[pd.DataFrame] = []
    skipped: list[str] = []

    for i, path in enumerate(xlsx_files, 1):
        print(f"[{i}/{len(xlsx_files)}] {path.name}", end=" ... ")

        try:
            df = pd.read_excel(path, sheet_name=SUMMARY_SHEET, engine="openpyxl")
        except Exception as e:
            print(f"SKIP — cannot open: {e}")
            skipped.append(path.name)
            continue

        if RANK_COLUMN not in df.columns:
            print(f"SKIP — column '{RANK_COLUMN}' not found")
            skipped.append(path.name)
            continue

        rank1 = df[df[RANK_COLUMN] == 1]
        if rank1.empty:
            print(f"SKIP — no row with {RANK_COLUMN}==1")
            skipped.append(path.name)
            continue

        row = rank1.iloc[[0]].copy()
        if SOURCE_COLUMN in row.columns:
            row = row.drop(columns=[SOURCE_COLUMN])
        row.insert(0, SOURCE_COLUMN, path.stem)
        rows.append(row)
        print("ok")

    return rows, skipped


def main() -> None:
    print(f"Scanning: {BATCH_DIR}\n")

    rows, skipped = collect_rank1_rows(BATCH_DIR)

    if not rows:
        print("Nothing to write — no valid files found.")
        sys.exit(1)

    result = pd.concat(rows, ignore_index=True).sort_values(SOURCE_COLUMN)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = BATCH_DIR / f"batch_summary_{timestamp}.xlsx"

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        result.to_excel(writer, sheet_name=SUMMARY_SHEET, index=False)

    print(f"\nDone.")
    print(f"  Processed : {len(rows)}")
    print(f"  Skipped   : {len(skipped)}")
    if skipped:
        for name in skipped:
            print(f"    - {name}")
    print(f"  Output    : {out_path}")


if __name__ == "__main__":
    main()
