from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .config import (
    AMOUNT_TOLERANCE,
    COUNTER_ROLE_MAP,
    FACTOR_TOLERANCE,
)
from .common import (
    _cell_state,
    _currency,
    _diff,
    _normalize_columns,
    _number,
    _query_in,
    _read_optional_sheet,
    _read_sql_query,
    _sql_values,
    _text,
    _write_plain_sheets,
)
from .generation import CounterAllocator

COMPARISON_AMOUNT_COLUMNS = {
    "ACCOUNT_AMOUNT",
    "AMOUNT",
    "APPLIED",
    "DISCOUNT",
    "NETAMOUNT",
    "OPEN_BALANCE",
    "C_AMOUNT",
    "C_APPLIED",
    "C_DISCOUNT",
    "C_NETAMOUNT",
    "C_OPEN_BALANCE",
    "DOC_TOTAL",
    "C_DOC_TOTAL",
    "PAID_TOTAL",
    "C_PAID_TOTAL",
    "VOID_TOTAL",
    "C_VOID_TOTAL",
    "BALANCE_TOTAL",
    "C_BALANCE_TOTAL",
    "DISCOUNT_AMT",
    "C_DISCOUNT_AMT",
}

COMPARISON_FACTOR_COLUMNS = {
    "ACCOUNT_FACTOR",
    "CURENCY_FACTOR",
}

COMPARISON_CURRENCY_COLUMNS = {
    "ACCOUNT_CURRENCY",
    "CURENCY_BASE",
    "CURENCY_CONV",
}

COMPARISON_DATE_COLUMNS = {
    "CHECK_DATE",
    "PAYMENT_DATE",
    "PRINTED_ON",
    "CURENCY_DATE",
    "ACCOUNT_FACTOR_DATE",
    "ADDED_DTE",
    "UPDATED_DTE",
    "PAY_APPLY",
    "PAY_DOC_DATE",
    "DOC_DATE",
    "DUE_DATE",
    "BATCH_DATE",
    "PRINT_DATE",
    "POST_GL_DATE",
    "CLEARED_ON",
    "WIRE_PROCESS_DATE",
    "SIGN_1_DATE",
    "SIGN_2_DATE",
    "SIGN_3_DATE",
    "SIGN_4_DATE",
    "SIGN_5_DATE",
}


def _state_map_from_workbook(
    generated_path: str | Path,
) -> dict[tuple[str, int, str], str]:
    try:
        state = _read_optional_sheet(
            generated_path,
            ["OUTPUT_STATE"],
        )
    except Exception:
        return {}

    if state.empty:
        return {}

    state = _normalize_columns(state)
    required = {
        "SHEET",
        "OUTPUT_ROW_NUMBER",
        "COLUMN",
        "VALUE_STATE",
    }
    if not required.issubset(state.columns):
        return {}

    result: dict[tuple[str, int, str], str] = {}
    for _, row in state.iterrows():
        result[
            (
                _text(row["SHEET"]).upper(),
                int(_number(row["OUTPUT_ROW_NUMBER"], 0) or 0),
                _text(row["COLUMN"]).upper(),
            )
        ] = _text(row["VALUE_STATE"]).upper()
    return result


def _output_value_state(
    value: Any,
    explicit_state: str | None,
) -> str:
    if explicit_state in {"NULL", "BLANK"}:
        return explicit_state

    state = _cell_state(value)
    if state in {"NULL", "BLANK"}:
        # Old output files do not have enough information to distinguish
        # NULL from an Excel blank cell.
        return "EMPTY_UNKNOWN"
    return "VALUE"


def _db_value_state(value: Any) -> str:
    return _cell_state(value)


def _try_number(value: Any) -> float | None:
    try:
        return _number(value)
    except (TypeError, ValueError):
        return None


def _normalize_comparison_text(
    value: Any,
    column: str,
) -> str:
    text_value = str(value).rstrip()
    if column in COMPARISON_CURRENCY_COLUMNS:
        return _currency(text_value)
    return text_value


def _compare_output_db_value(
    output_value: Any,
    db_value: Any,
    column: str,
    output_state: str,
    amount_tolerance: float,
    numeric_tolerance: float,
    factor_tolerance: float,
    date_tolerance_seconds: float,
) -> tuple[str, str, Any]:
    db_state = _db_value_state(db_value)

    if output_state == "EMPTY_UNKNOWN":
        if db_state in {"NULL", "BLANK"}:
            return (
                "EMPTY_STATE_UNKNOWN",
                "Older output Excel cannot distinguish NULL from BLANK.",
                None,
            )
        return (
            "MISMATCH",
            f"Output is empty but DB state is {db_state}.",
            None,
        )

    if output_state != "VALUE" or db_state != "VALUE":
        if output_state == db_state:
            return "MATCH", "", None
        return (
            "MISMATCH",
            f"Output state={output_state}; DB state={db_state}.",
            None,
        )

    if column in COMPARISON_DATE_COLUMNS:
        try:
            output_date = pd.Timestamp(output_value)
            db_date = pd.Timestamp(db_value)
            diff_seconds = abs(
                (output_date - db_date).total_seconds()
            )
            status = (
                "MATCH"
                if diff_seconds <= date_tolerance_seconds
                else "MISMATCH"
            )
            return (
                status,
                (
                    ""
                    if status == "MATCH"
                    else f"Date difference {diff_seconds} seconds."
                ),
                diff_seconds,
            )
        except Exception:
            pass

    output_number = _try_number(output_value)
    db_number = _try_number(db_value)
    if output_number is not None and db_number is not None:
        if column in COMPARISON_AMOUNT_COLUMNS:
            tolerance = amount_tolerance
        elif column in COMPARISON_FACTOR_COLUMNS:
            tolerance = factor_tolerance
        else:
            tolerance = numeric_tolerance

        difference = output_number - db_number
        status = (
            "MATCH"
            if abs(difference) <= tolerance
            else "MISMATCH"
        )
        return (
            status,
            (
                ""
                if status == "MATCH"
                else f"Numeric difference {difference}."
            ),
            difference,
        )

    output_text = _normalize_comparison_text(
        output_value,
        column,
    )
    db_text = _normalize_comparison_text(
        db_value,
        column,
    )
    status = "MATCH" if output_text == db_text else "MISMATCH"
    return (
        status,
        (
            ""
            if status == "MATCH"
            else "Text values differ after trimming DB padding."
        ),
        None,
    )


def _key_number(value: Any) -> int | float | None:
    number = _number(value)
    if number is None:
        return None
    return int(number) if float(number).is_integer() else number


def _record_key(
    table_name: str,
    row: pd.Series,
) -> tuple[Any, ...]:
    if table_name == "CHECK_HDR":
        return (_key_number(row.get("DOC_NO")),)

    if table_name == "CHECK_LINE":
        return (
            _key_number(row.get("DOC_NO")),
            _key_number(row.get("LINE")),
        )

    if table_name == "ARAP_UPDATE":
        return (
            _key_number(row.get("DOC_NO")),
            _key_number(row.get("LINE")) or 0,
            _text(row.get("DOC_CATEGORY")),
            _text(row.get("ACCTNO")),
        )

    raise ValueError(f"Unsupported comparison table: {table_name}")


def _format_record_key(
    table_name: str,
    key: tuple[Any, ...],
) -> str:
    if table_name == "CHECK_HDR":
        return f"DOC_NO={key[0]}"
    if table_name == "CHECK_LINE":
        return f"DOC_NO={key[0]},LINE={key[1]}"
    return (
        f"DOC_NO={key[0]},LINE={key[1]},"
        f"DOC_CATEGORY={key[2]},ACCTNO={key[3]}"
    )


def _query_generated_table_rows(
    connection: Any,
    table_name: str,
    generated: pd.DataFrame,
) -> pd.DataFrame:
    sql_table = {
        "CHECK_HDR": "dbo.CHECK_HDR",
        "CHECK_LINE": "dbo.CHECK_LINE",
        "ARAP_UPDATE": "dbo.ACCOUNT_AR_AP",
    }[table_name]

    doc_nos = _sql_values(generated.get(
        "DOC_NO",
        pd.Series(dtype=object),
    ))
    if not doc_nos:
        return pd.DataFrame()

    return _normalize_columns(
        _query_in(
            connection,
            sql_table,
            "DOC_NO",
            doc_nos,
        )
    )


def _build_db_index(
    table_name: str,
    db_rows: pd.DataFrame,
) -> dict[tuple[Any, ...], list[pd.Series]]:
    result: dict[tuple[Any, ...], list[pd.Series]] = {}
    for _, row in db_rows.iterrows():
        key = _record_key(table_name, row)
        result.setdefault(key, []).append(row)
    return result


def _compare_standard_table(
    table_name: str,
    generated: pd.DataFrame,
    db_rows: pd.DataFrame,
    state_map: dict[tuple[str, int, str], str],
    amount_tolerance: float,
    numeric_tolerance: float,
    factor_tolerance: float,
    date_tolerance_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    comparisons: list[dict[str, Any]] = []
    row_summaries: list[dict[str, Any]] = []

    generated = _normalize_columns(generated)
    db_rows = _normalize_columns(db_rows)
    db_index = _build_db_index(table_name, db_rows)
    db_columns = set(db_rows.columns)

    for row_index, output_row in generated.reset_index(drop=True).iterrows():
        excel_row_number = row_index + 2
        key = _record_key(table_name, output_row)
        key_text = _format_record_key(table_name, key)
        matches = db_index.get(key, [])

        mismatch_columns: list[str] = []
        uncertain_columns: list[str] = []
        output_only_columns: list[str] = []

        for column in generated.columns:
            output_value = output_row.get(column)
            explicit_state = state_map.get(
                (
                    table_name,
                    excel_row_number,
                    column,
                )
            )
            output_state = _output_value_state(
                output_value,
                explicit_state,
            )

            if column not in db_columns:
                status = "OUTPUT_ONLY_COLUMN"
                detail = "Column does not exist in the database table."
                db_value = None
                db_state = "NOT_APPLICABLE"
                difference = None
                output_only_columns.append(column)
            elif len(matches) == 0:
                status = "ROW_NOT_FOUND"
                detail = "No database row matched the output key."
                db_value = None
                db_state = "NOT_FOUND"
                difference = None
                mismatch_columns.append(column)
            elif len(matches) > 1:
                status = "ROW_NOT_UNIQUE"
                detail = f"{len(matches)} database rows matched the output key."
                db_value = None
                db_state = "MULTIPLE"
                difference = None
                mismatch_columns.append(column)
            else:
                db_value = matches[0].get(column)
                db_state = _db_value_state(db_value)
                status, detail, difference = _compare_output_db_value(
                    output_value=output_value,
                    db_value=db_value,
                    column=column,
                    output_state=output_state,
                    amount_tolerance=amount_tolerance,
                    numeric_tolerance=numeric_tolerance,
                    factor_tolerance=factor_tolerance,
                    date_tolerance_seconds=date_tolerance_seconds,
                )
                if status == "MISMATCH":
                    mismatch_columns.append(column)
                elif status == "EMPTY_STATE_UNKNOWN":
                    uncertain_columns.append(column)

            comparisons.append(
                {
                    "TABLE": table_name,
                    "RECORD_KEY": key_text,
                    "OUTPUT_ROW_NUMBER": excel_row_number,
                    "COLUMN": column,
                    "OUTPUT_STATE": output_state,
                    "OUTPUT_VALUE": output_value,
                    "DB_STATE": db_state,
                    "DB_VALUE": db_value,
                    "DIFFERENCE": difference,
                    "STATUS": status,
                    "DETAIL": detail,
                }
            )

        if mismatch_columns:
            row_status = "MISMATCH"
        elif uncertain_columns:
            row_status = "EMPTY_STATE_UNKNOWN"
        else:
            row_status = "MATCH"

        row_summaries.append(
            {
                "TABLE": table_name,
                "RECORD_KEY": key_text,
                "OUTPUT_ROW_NUMBER": excel_row_number,
                "DB_MATCH_COUNT": len(matches),
                "ROW_STATUS": row_status,
                "MISMATCH_COLUMNS": ", ".join(mismatch_columns),
                "EMPTY_STATE_UNKNOWN_COLUMNS":
                    ", ".join(uncertain_columns),
                "OUTPUT_ONLY_COLUMNS":
                    ", ".join(output_only_columns),
            }
        )

    return comparisons, row_summaries


def _counter_division_state(
    row: pd.Series,
    sheet_name: str,
    excel_row_number: int,
    state_map: dict[tuple[str, int, str], str],
) -> tuple[str, Any]:
    explicit_state = state_map.get(
        (
            sheet_name,
            excel_row_number,
            "DIVISION",
        )
    )
    state = _output_value_state(
        row.get("DIVISION"),
        explicit_state,
    )
    return state, row.get("DIVISION")


def _compare_counter_table(
    connection: Any,
    generated: pd.DataFrame,
    state_map: dict[tuple[str, int, str], str],
    numeric_tolerance: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    comparisons: list[dict[str, Any]] = []
    row_summaries: list[dict[str, Any]] = []
    generated = _normalize_columns(generated)

    for row_index, output_row in generated.reset_index(drop=True).iterrows():
        excel_row_number = row_index + 2
        role = _text(output_row.get("ROLE"))
        companyno = output_row.get("COMPANYNO")
        doc_category, doc_type = COUNTER_ROLE_MAP.get(
            role,
            ("", ""),
        )
        division_state, division_value = _counter_division_state(
            output_row,
            "COUNTER_UPDATE",
            excel_row_number,
            state_map,
        )

        sql = """
            SELECT *
            FROM dbo.COUNTERSTBL
            WHERE RTRIM(DOC_CATEGORY) = ?
              AND RTRIM(DOC_TYPE) = ?
              AND COMPANYNO = ?
        """
        params: list[Any] = [
            doc_category,
            doc_type,
            companyno,
        ]

        if division_state == "NULL":
            sql += " AND DIVISION IS NULL"
        elif division_state == "BLANK":
            sql += (
                " AND DIVISION IS NOT NULL"
                " AND RTRIM(DIVISION) = ''"
            )
        else:
            sql += " AND RTRIM(DIVISION) = ?"
            params.append(_text(division_value))

        matches = _normalize_columns(
            _read_sql_query(connection, sql, params)
        )
        key_text = (
            f"ROLE={role},COMPANYNO={companyno},"
            f"DIVISION_STATE={division_state}"
        )

        mismatch_columns: list[str] = []
        output_only_columns: list[str] = []

        counter_values = (
            matches["COUNTER"].dropna().tolist()
            if "COUNTER" in matches.columns
            else []
        )
        unique_counter_values = sorted(
            {
                _key_number(value)
                for value in counter_values
            }
        )

        counter_after = output_row.get("COUNTER_AFTER")
        if len(matches) == 0:
            mapped_status = "ROW_NOT_FOUND"
            mapped_detail = "No COUNTERSTBL row matched."
            db_counter = None
        elif len(unique_counter_values) > 1:
            mapped_status = "ROW_NOT_UNIQUE"
            mapped_detail = (
                "Matched rows contain different COUNTER values."
            )
            db_counter = None
        else:
            db_counter = (
                unique_counter_values[0]
                if unique_counter_values
                else None
            )
            mapped_status, mapped_detail, _ = (
                _compare_output_db_value(
                    output_value=counter_after,
                    db_value=db_counter,
                    column="COUNTER",
                    output_state="VALUE",
                    amount_tolerance=numeric_tolerance,
                    numeric_tolerance=numeric_tolerance,
                    factor_tolerance=numeric_tolerance,
                    date_tolerance_seconds=0,
                )
            )

        mapped_columns = {
            "DOC_CATEGORY": (
                output_row.get("DOC_CATEGORY"),
                doc_category,
            ),
            "DOC_TYPE": (
                output_row.get("DOC_TYPE"),
                doc_type,
            ),
            "COMPANYNO": (
                output_row.get("COMPANYNO"),
                (
                    matches.iloc[0].get("COMPANYNO")
                    if len(matches) >= 1
                    else None
                ),
            ),
            "DIVISION": (
                output_row.get("DIVISION"),
                (
                    matches.iloc[0].get("DIVISION")
                    if len(matches) >= 1
                    else None
                ),
            ),
            "COUNTER_AFTER": (
                counter_after,
                db_counter,
            ),
            "SOURCE_ROW_COUNT": (
                output_row.get("SOURCE_ROW_COUNT"),
                len(matches),
            ),
        }

        for column in generated.columns:
            output_value = output_row.get(column)
            explicit_state = state_map.get(
                (
                    "COUNTER_UPDATE",
                    excel_row_number,
                    column,
                )
            )
            output_state = _output_value_state(
                output_value,
                explicit_state,
            )

            if column not in mapped_columns:
                status = "OUTPUT_ONLY_COLUMN"
                detail = (
                    "Counter simulation metadata has no single DB column."
                )
                db_value = None
                db_state = "NOT_APPLICABLE"
                difference = None
                output_only_columns.append(column)
            elif len(matches) == 0:
                status = "ROW_NOT_FOUND"
                detail = mapped_detail
                db_value = None
                db_state = "NOT_FOUND"
                difference = None
                mismatch_columns.append(column)
            else:
                output_mapped, db_value = mapped_columns[column]
                db_state = _db_value_state(db_value)

                if column == "COUNTER_AFTER":
                    status = mapped_status
                    detail = mapped_detail
                    difference = (
                        None
                        if db_counter is None
                        else _diff(output_mapped, db_counter)
                    )
                elif column == "DIVISION":
                    status, detail, difference = (
                        _compare_output_db_value(
                            output_value=output_mapped,
                            db_value=db_value,
                            column=column,
                            output_state=output_state,
                            amount_tolerance=numeric_tolerance,
                            numeric_tolerance=numeric_tolerance,
                            factor_tolerance=numeric_tolerance,
                            date_tolerance_seconds=0,
                        )
                    )
                else:
                    status, detail, difference = (
                        _compare_output_db_value(
                            output_value=output_mapped,
                            db_value=db_value,
                            column=column,
                            output_state=output_state,
                            amount_tolerance=numeric_tolerance,
                            numeric_tolerance=numeric_tolerance,
                            factor_tolerance=numeric_tolerance,
                            date_tolerance_seconds=0,
                        )
                    )

                if status in {"MISMATCH", "ROW_NOT_UNIQUE"}:
                    mismatch_columns.append(column)

            comparisons.append(
                {
                    "TABLE": "COUNTER_UPDATE",
                    "RECORD_KEY": key_text,
                    "OUTPUT_ROW_NUMBER": excel_row_number,
                    "COLUMN": column,
                    "OUTPUT_STATE": output_state,
                    "OUTPUT_VALUE": output_value,
                    "DB_STATE": db_state,
                    "DB_VALUE": db_value,
                    "DIFFERENCE": difference,
                    "STATUS": status,
                    "DETAIL": detail,
                }
            )

        row_summaries.append(
            {
                "TABLE": "COUNTER_UPDATE",
                "RECORD_KEY": key_text,
                "OUTPUT_ROW_NUMBER": excel_row_number,
                "DB_MATCH_COUNT": len(matches),
                "ROW_STATUS": (
                    "MISMATCH"
                    if mismatch_columns
                    else "MATCH"
                ),
                "MISMATCH_COLUMNS": ", ".join(mismatch_columns),
                "EMPTY_STATE_UNKNOWN_COLUMNS": "",
                "OUTPUT_ONLY_COLUMNS":
                    ", ".join(output_only_columns),
            }
        )

    return comparisons, row_summaries


def _comparison_summaries(
    cell_comparison: pd.DataFrame,
    row_summary: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if cell_comparison.empty:
        return pd.DataFrame(), pd.DataFrame()

    table_summary = (
        cell_comparison.groupby(
            ["TABLE", "STATUS"],
            dropna=False,
        )
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )

    for column in [
        "MATCH",
        "MISMATCH",
        "EMPTY_STATE_UNKNOWN",
        "OUTPUT_ONLY_COLUMN",
        "ROW_NOT_FOUND",
        "ROW_NOT_UNIQUE",
    ]:
        if column not in table_summary.columns:
            table_summary[column] = 0

    if not row_summary.empty:
        row_counts = (
            row_summary.groupby("TABLE")
            .agg(
                OUTPUT_ROW_COUNT=("RECORD_KEY", "count"),
                MATCHED_ROW_COUNT=(
                    "ROW_STATUS",
                    lambda values: sum(value == "MATCH" for value in values),
                ),
                MISMATCH_ROW_COUNT=(
                    "ROW_STATUS",
                    lambda values: sum(
                        value == "MISMATCH"
                        for value in values
                    ),
                ),
                UNCERTAIN_ROW_COUNT=(
                    "ROW_STATUS",
                    lambda values: sum(
                        value == "EMPTY_STATE_UNKNOWN"
                        for value in values
                    ),
                ),
            )
            .reset_index()
        )
        table_summary = table_summary.merge(
            row_counts,
            how="left",
            on="TABLE",
        )

    column_summary = (
        cell_comparison.groupby(
            ["TABLE", "COLUMN", "STATUS"],
            dropna=False,
        )
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    return table_summary, column_summary


def compare_generated_output_to_db(
    generated_path: str | Path,
    output_path: str | Path,
    connection_string: str,
    amount_tolerance: float = AMOUNT_TOLERANCE,
    numeric_tolerance: float = 1e-8,
    factor_tolerance: float = FACTOR_TOLERANCE,
    date_tolerance_seconds: float = 120.0,
) -> None:
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError(
            "pyodbc is required. Install it with: pip install pyodbc"
        ) from exc

    generated_sheets: dict[str, pd.DataFrame] = {}
    for sheet_name in [
        "CHECK_HDR",
        "CHECK_LINE",
        "ARAP_UPDATE",
        "COUNTER_UPDATE",
    ]:
        generated_sheets[sheet_name] = _read_optional_sheet(
            generated_path,
            [sheet_name],
        )

    state_map = _state_map_from_workbook(generated_path)
    comparisons: list[dict[str, Any]] = []
    row_summaries: list[dict[str, Any]] = []

    with pyodbc.connect(connection_string) as connection:
        for table_name in [
            "CHECK_HDR",
            "CHECK_LINE",
            "ARAP_UPDATE",
        ]:
            generated = generated_sheets[table_name]
            if generated.empty:
                continue

            db_rows = _query_generated_table_rows(
                connection,
                table_name,
                _normalize_columns(generated),
            )
            table_comparisons, table_rows = (
                _compare_standard_table(
                    table_name=table_name,
                    generated=generated,
                    db_rows=db_rows,
                    state_map=state_map,
                    amount_tolerance=amount_tolerance,
                    numeric_tolerance=numeric_tolerance,
                    factor_tolerance=factor_tolerance,
                    date_tolerance_seconds=date_tolerance_seconds,
                )
            )
            comparisons.extend(table_comparisons)
            row_summaries.extend(table_rows)

        counter_generated = generated_sheets["COUNTER_UPDATE"]
        if not counter_generated.empty:
            counter_comparisons, counter_rows = (
                _compare_counter_table(
                    connection=connection,
                    generated=counter_generated,
                    state_map=state_map,
                    numeric_tolerance=numeric_tolerance,
                )
            )
            comparisons.extend(counter_comparisons)
            row_summaries.extend(counter_rows)

    cell_comparison = pd.DataFrame(comparisons)
    row_summary = pd.DataFrame(row_summaries)
    table_summary, column_summary = _comparison_summaries(
        cell_comparison,
        row_summary,
    )

    _write_plain_sheets(
        output_path,
        {
            "Table_Summary": table_summary,
            "Row_Summary": row_summary,
            "Column_Summary": column_summary,
            "Cell_Comparison": cell_comparison,
        },
    )
