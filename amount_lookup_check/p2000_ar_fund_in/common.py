from __future__ import annotations

from copy import copy
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .config import AMOUNT_TOLERANCE

__all__ = [
    "_text",
    "_currency",
    "_currency_db_aliases",
    "_number",
    "_date",
    "_money",
    "_calculated_amount",
    "_user_doc",
    "_status",
    "_diff",
    "_read_first_sheet",
    "_read_optional_sheet",
    "_normalize_columns",
    "_sql_values",
    "_read_sql_query",
    "_query_in",
    "_read_alias",
    "_trim_table",
    "_init_row",
    "_plain_header",
    "_resize_plain",
    "_format_text_columns",
    "_cell_state",
    "_build_output_state_sheet",
    "_write_plain_sheets",
    "_write_validation_one_sheet",
    "_key_number",
]

def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _currency(value: Any) -> str:
    code = _text(value).upper()
    return "CNY" if code == "RMB" else code


def _currency_db_aliases(value: Any) -> set[str]:
    """
    Return all database spellings that should be treated as one currency.

    P2000 data may use RMB and CNY interchangeably. Internal comparisons use
    CNY, while SQL retrieval includes both values so neither spelling is lost.
    """
    code = _currency(value)
    if code == "CNY":
        return {"CNY", "RMB"}
    return {code} if code else set()


def _number(value: Any, default: float | None = None) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or _text(value) == "":
        return default
    return float(value)


def _date(value: Any) -> pd.Timestamp | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or _text(value) == "":
        return None
    return pd.Timestamp(value)


def _money(value: Any) -> float | None:
    number = _number(value)
    return None if number is None else round(number, 2)


def _calculated_amount(value: Any) -> float | None:
    """Preserve useful precision for currency-converted database amounts."""
    number = _number(value)
    return None if number is None else round(number, 12)


def _user_doc(value: Any) -> str:
    """Normalize USER_DOC safely without fuzzy matching.

    - strip variable SQL/Excel outer padding;
    - remove only a numeric Excel-style trailing `.0`;
    - preserve existing leading zeroes;
    - left-pad numeric values to eight characters.
    """
    text = _text(value)
    if not text:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(8) if text.isdigit() else text


def _status(actual: Any, expected: Any, tolerance: float = AMOUNT_TOLERANCE) -> str:
    a = _number(actual)
    e = _number(expected)
    if a is None or e is None:
        return "NOT_TESTED"
    return "MATCH" if abs(a - e) <= tolerance else "MISMATCH"


def _diff(actual: Any, expected: Any) -> float | None:
    a = _number(actual)
    e = _number(expected)
    return None if a is None or e is None else round(a - e, 10)


def _read_first_sheet(path: str | Path) -> pd.DataFrame:
    xls = pd.ExcelFile(path)
    return pd.read_excel(path, sheet_name=xls.sheet_names[0]).dropna(how="all").copy()


def _read_optional_sheet(
    path: str | Path,
    aliases: Iterable[str],
) -> pd.DataFrame:
    """Return an optional sheet, or an empty DataFrame when it is absent."""
    xls = pd.ExcelFile(path)
    by_upper = {name.strip().upper(): name for name in xls.sheet_names}
    for alias in aliases:
        actual = by_upper.get(str(alias).strip().upper())
        if actual:
            return pd.read_excel(path, sheet_name=actual).dropna(how="all").copy()
    return pd.DataFrame()


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [str(column).strip().upper() for column in result.columns]
    return result


def _sql_values(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        key = _text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _read_sql_query(
    connection: Any,
    sql: str,
    params: Iterable[Any] = (),
) -> pd.DataFrame:
    """Read a SQL query through a pyodbc cursor without pandas' DBAPI warning."""
    cursor = connection.cursor()
    try:
        cursor.execute(sql, list(params))
        columns = [item[0] for item in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return pd.DataFrame.from_records(rows, columns=columns)
    finally:
        cursor.close()


def _query_in(
    connection: Any,
    table: str,
    column: str,
    values: Iterable[Any],
    extra_where: str = "",
    extra_params: Iterable[Any] = (),
) -> pd.DataFrame:
    unique_values = _sql_values(values)
    if not unique_values:
        return pd.DataFrame()

    parts: list[pd.DataFrame] = []
    # Stay comfortably under SQL Server's 2,100-parameter limit.
    for start in range(0, len(unique_values), 900):
        batch = unique_values[start:start + 900]
        placeholders = ",".join("?" for _ in batch)
        sql = f"SELECT * FROM {table} WHERE {column} IN ({placeholders})"
        if extra_where:
            sql += f" AND ({extra_where})"
        params = list(batch) + list(extra_params)
        parts.append(_read_sql_query(connection, sql, params))

    return _normalize_columns(pd.concat(parts, ignore_index=True))
def _read_alias(path: str | Path, aliases: list[str]) -> pd.DataFrame:
    xls = pd.ExcelFile(path)
    by_upper = {name.upper(): name for name in xls.sheet_names}
    for alias in aliases:
        actual = by_upper.get(alias.upper())
        if actual:
            return pd.read_excel(path, sheet_name=actual).dropna(how="all").copy()
    raise ValueError(f"Reference workbook is missing one of these sheets: {aliases}")


def _trim_table(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    if "TableName" in result.columns:
        mask = result["TableName"].map(_text).str.startswith("dbo.")
        if mask.any():
            result = result[mask].copy()
    return result.reset_index(drop=True)


def _init_row(columns: Iterable[str], defaults: dict[str, Any]) -> dict[str, Any]:
    row = {column: None for column in columns}
    for key, value in defaults.items():
        if key in row:
            row[key] = value
    return row


def _plain_header(ws, row_number: int = 1) -> None:
    for cell in ws[row_number]:
        if cell.value is None:
            continue
        font = copy(cell.font)
        font.bold = True
        cell.font = font
        fill = copy(cell.fill)
        fill.fill_type = "solid"
        fill.fgColor.rgb = "E7E6E6"
        cell.fill = fill


def _resize_plain(ws) -> None:
    for column_cells in ws.columns:
        values = [_text(cell.value) for cell in list(column_cells)[:200]]
        width = min(max(max((len(v) for v in values), default=8) + 2, 10), 28)
        ws.column_dimensions[column_cells[0].column_letter].width = width


def _format_text_columns(ws) -> None:
    text_headers = {
        "CHECK_NO", "PAY_USER_DOC", "USER_DOC", "ACCOUNT_NO",
        "GL_CODE", "GL_CODE_AMT", "GL_CODE_DISC", "TRANSFER_GL_CODE",
        "BANK_ID", "ACCTNO", "DOC_CATEGORY", "PAY_DOC_CATEGORY",
    }
    header_map = {cell.value: cell.column for cell in ws[1] if cell.value is not None}
    for header in text_headers:
        column = header_map.get(header)
        if column is None:
            continue
        for row in range(2, ws.max_row + 1):
            ws.cell(row=row, column=column).number_format = "@"


def _cell_state(value: Any) -> str:
    if value is None:
        return "NULL"
    try:
        if pd.isna(value):
            return "NULL"
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and value.strip() == "":
        return "BLANK"
    return "VALUE"


def _build_output_state_sheet(
    sheets: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    comparable_sheets = {
        "CHECK_HDR",
        "CHECK_LINE",
        "ARAP_UPDATE",
        "COUNTER_UPDATE",
    }

    for sheet_name, frame in sheets.items():
        if sheet_name not in comparable_sheets:
            continue
        for row_index, row in frame.reset_index(drop=True).iterrows():
            for column in frame.columns:
                state = _cell_state(row.get(column))
                if state in {"NULL", "BLANK"}:
                    rows.append(
                        {
                            "SHEET": sheet_name,
                            "OUTPUT_ROW_NUMBER": row_index + 2,
                            "COLUMN": column,
                            "VALUE_STATE": state,
                        }
                    )

    return pd.DataFrame(
        rows,
        columns=[
            "SHEET",
            "OUTPUT_ROW_NUMBER",
            "COLUMN",
            "VALUE_STATE",
        ],
    )


def _write_plain_sheets(path: str | Path, sheets: dict[str, pd.DataFrame]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    sheets_to_write = dict(sheets)
    sheets_to_write["OUTPUT_STATE"] = _build_output_state_sheet(
        sheets_to_write
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets_to_write.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            _plain_header(ws, 1)
            _format_text_columns(ws)
            _resize_plain(ws)


def _write_validation_one_sheet(
    path: str | Path,
    header_validation: pd.DataFrame,
    line_validation: pd.DataFrame,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        header_validation.to_excel(writer, sheet_name="Validation", index=False, startrow=1)
        line_start = len(header_validation) + 5
        line_validation.to_excel(writer, sheet_name="Validation", index=False, startrow=line_start)
        ws = writer.book["Validation"]
        ws["A1"] = "HEADER VALIDATION"
        ws.cell(row=line_start, column=1, value="LINE VALIDATION")
        _plain_header(ws, 2)
        _plain_header(ws, line_start + 1)
        ws.freeze_panes = "A3"
        _resize_plain(ws)

def _key_number(value: Any) -> int | float | None:
    number = _number(value)
    if number is None:
        return None
    return int(number) if float(number).is_integer() else number
