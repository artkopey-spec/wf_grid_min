"""Trade-date membership and observed boundaries on the full FINAM time axis."""

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


_ROOT = Path(__file__).resolve().parents[2]
_DATE = re.compile(r"[0-9]{8}")


@dataclass(frozen=True)
class TradeCalendar:
    allowed: np.ndarray
    boundary: np.ndarray
    entry: np.ndarray
    metadata: dict


def load_trade_calendar(file_path: str, index: pd.DatetimeIndex) -> TradeCalendar:
    path = (_ROOT / file_path).resolve()
    dates = set()
    records = 0
    header = None
    line = 1
    last_physical_line = ""

    def physical_lines(stream):
        nonlocal last_physical_line
        inside_quotes = False
        for number, record in enumerate(stream, 1):
            last_physical_line = record.rstrip("\r\n")
            if re.search(r"[\udc80-\udcff]", record):
                raise ValueError(f"UTF-8 codec error on line {number}: {last_physical_line!r}")
            # csv.reader(strict=True) accepts a bare quote inside an unquoted
            # field. Check field starts too, preserving quoted physical newlines.
            pos = record.find('"')
            while pos != -1:
                if inside_quotes and record[pos + 1:pos + 2] == '"':
                    pos += 1
                else:
                    if not inside_quotes and pos and record[pos - 1] != ";":
                        raise ValueError(f"unexpected quote on line {number}: {last_physical_line!r}")
                    inside_quotes = not inside_quotes
                pos = record.find('"', pos + 1)
            yield record

    try:
        with path.open(encoding="utf-8-sig", errors="surrogateescape", newline="") as stream:
            reader = csv.reader(physical_lines(stream), delimiter=";", strict=True)
            while True:
                line = reader.line_num + 1
                row = next(reader, None)
                if row is None:
                    break
                if not row:
                    continue
                if header is None:
                    if row.count("<DATE>") != 1:
                        raise ValueError(f"expected exactly one <DATE> column, got {row!r}")
                    header = row.index("<DATE>")
                    continue
                if header >= len(row):
                    raise ValueError(f"missing date field in {row!r}")
                raw = row[header]
                records += 1
                value = raw.strip(" \t")
                if value in dates:
                    continue
                if not _DATE.fullmatch(value):
                    raise ValueError(f"invalid YYYYMMDD date {raw!r}")
                try:
                    date(int(value[:4]), int(value[4:6]), int(value[6:]))
                except ValueError as exc:
                    raise ValueError(f"invalid calendar date {raw!r}") from exc
                dates.add(value)
    except csv.Error as exc:
        raise ValueError(f"{path}, line {line}: {exc}, near {last_physical_line!r}") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{path}, line {line}: {exc}") from exc
    if not dates:
        reason = "missing <DATE> header" if header is None else "empty date list"
        raise ValueError(f"{path}: {reason}")

    bar_dates = index.strftime("%Y%m%d")
    history_dates = set(bar_dates)
    matches = len(history_dates & dates)
    if not matches:
        raise ValueError(
            f"{path}: no matching dates (list={len(dates)}, history={len(history_dates)})"
        )
    allowed = np.asarray(bar_dates.isin(dates), dtype=bool)
    boundary = np.zeros_like(allowed)
    boundary[:-1] = allowed[:-1] & ~allowed[1:]
    digest = hashlib.sha256("\n".join(sorted(dates)).encode("utf-8")).hexdigest()
    return TradeCalendar(allowed, boundary, allowed & ~boundary, {
        "file_path": str(path), "source_rows": records,
        "unique_dates": len(dates), "matched_dates": matches,
        "allowed_bars": int(allowed.sum()), "boundary_bars": int(boundary.sum()),
        "sha256": digest,
        "ranking_rule": "Ретроспективный отбор дат по наблюдаемым барам; итоговый рейтинг по OOS",
    })
