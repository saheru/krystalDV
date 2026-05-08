"""Read Excel files into a normalized in-memory table."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl


@dataclass
class ExcelTable:
    columns: list[str]
    rows: list[dict[str, Any]]
    sheet_name: str
    source_path: str

    def __len__(self) -> int:
        return len(self.rows)

    def head(self, n: int = 5) -> list[dict[str, Any]]:
        return self.rows[:n]

    def column_values(self, name: str) -> list[Any]:
        return [r.get(name) for r in self.rows]


def _normalize_cell(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def read_excel(path: str | Path, sheet_name: str | None = None) -> ExcelTable:
    """Read first sheet (or named) into ExcelTable.

    Empty leading rows are skipped; the first non-empty row is used as headers.
    """
    path = Path(path)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[sheet_name] if sheet_name else wb[wb.sheetnames[0]]

        rows_iter = ws.iter_rows(values_only=True)
        header: list[str] = []
        for row in rows_iter:
            if any(c is not None and str(c).strip() != "" for c in row):
                header = [
                    str(c).strip() if c is not None and str(c).strip() else f"列{i + 1}"
                    for i, c in enumerate(row)
                ]
                break
        if not header:
            return ExcelTable(columns=[], rows=[], sheet_name=ws.title, source_path=str(path))

        # de-duplicate column names
        seen: dict[str, int] = {}
        unique_header: list[str] = []
        for name in header:
            if name in seen:
                seen[name] += 1
                unique_header.append(f"{name}_{seen[name]}")
            else:
                seen[name] = 1
                unique_header.append(name)

        rows: list[dict[str, Any]] = []
        for row in rows_iter:
            values = list(row) + [None] * max(0, len(unique_header) - len(row))
            values = values[: len(unique_header)]
            if all(v is None or (isinstance(v, str) and v.strip() == "") for v in values):
                continue
            rows.append({c: _normalize_cell(v) for c, v in zip(unique_header, values)})

        return ExcelTable(
            columns=unique_header,
            rows=rows,
            sheet_name=ws.title,
            source_path=str(path),
        )
    finally:
        wb.close()
