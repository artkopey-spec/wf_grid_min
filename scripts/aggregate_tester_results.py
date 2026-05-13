"""
Aggregate tester result workbooks into one xlsx.

Reads every result workbook from "results tester", copies the data rows from
the workbook Summary sheet, and writes one consolidated workbook with a
lowercase "summary" sheet.
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
TESTER_DIR = ROOT_DIR / "results tester"
INPUT_SHEET = "Summary"
OUTPUT_SHEET = "summary"
OUTPUT_PREFIX = "tester_summary_"
SOURCE_COLUMN = "source_file"


def _is_aggregate_output(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith(OUTPUT_PREFIX) and name.endswith(".xlsx")


def _iter_input_files(tester_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in tester_dir.glob("*.xlsx")
        if not path.name.startswith("~$") and not _is_aggregate_output(path)
    )


def _read_summary(path: Path) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=INPUT_SHEET, engine="openpyxl")
    except ValueError:
        sheets = pd.ExcelFile(path, engine="openpyxl").sheet_names
        match = next((sheet for sheet in sheets if sheet.lower() == "summary"), None)
        if match is None:
            raise
        return pd.read_excel(path, sheet_name=match, engine="openpyxl")


def collect_rows(tester_dir: Path) -> tuple[list[pd.DataFrame], list[str]]:
    xlsx_files = _iter_input_files(tester_dir)
    if not xlsx_files:
        print(f"No xlsx files found in {tester_dir}")
        return [], []

    rows: list[pd.DataFrame] = []
    skipped: list[str] = []

    for i, path in enumerate(xlsx_files, 1):
        print(f"[{i}/{len(xlsx_files)}] {path.name}", end=" ... ")
        try:
            df = _read_summary(path)
        except Exception as exc:
            print(f"SKIP - cannot read Summary: {exc}")
            skipped.append(path.name)
            continue

        df = df.dropna(how="all")
        if df.empty:
            print("SKIP - empty Summary")
            skipped.append(path.name)
            continue

        if SOURCE_COLUMN in df.columns:
            df = df.drop(columns=[SOURCE_COLUMN])
        df.insert(0, SOURCE_COLUMN, path.name)
        rows.append(df)
        print("ok")

    return rows, skipped


def main() -> None:
    print(f"Scanning: {TESTER_DIR}\n")
    rows, skipped = collect_rows(TESTER_DIR)

    if not rows:
        print("Nothing to write - no valid files found.")
        sys.exit(1)

    result = pd.concat(rows, ignore_index=True)
    if len(sys.argv) > 1:
        out_path = Path(sys.argv[1])
        if not out_path.is_absolute():
            out_path = ROOT_DIR / out_path
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = TESTER_DIR / f"{OUTPUT_PREFIX}{timestamp}.xlsx"

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        result.to_excel(writer, sheet_name=OUTPUT_SHEET, index=False)

    print("\nDone.")
    print(f"  Processed : {len(rows)}")
    print(f"  Skipped   : {len(skipped)}")
    if skipped:
        for name in skipped:
            print(f"    - {name}")
    print(f"  Output    : {out_path}")


if __name__ == "__main__":
    main()
