from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .config import (
    AMOUNT_TOLERANCE,
    ARAP_SCHEMA,
    COUNTER_ROLE_MAP,
    HDR_DEFAULTS,
    HDR_SCHEMA,
    LINE_DEFAULTS,
    LINE_SCHEMA,
)
from .common import (
    _calculated_amount,
    _currency,
    _date,
    _diff,
    _init_row,
    _normalize_columns,
    _number,
    _read_first_sheet,
    _read_optional_sheet,
    _text,
    _trim_table,
    _user_doc,
    _write_plain_sheets,
)
from .db import _load_database_lookups

def _lookup_rate(
    rates: pd.DataFrame,
    base_currency: str,
    target_currency: str,
) -> tuple[float | None, pd.Timestamp | None, str]:
    base = _currency(base_currency)
    target = _currency(target_currency)
    if not base or not target:
        return None, None, "INVALID_CURRENCY"
    if base == target:
        return 1.0, None, "SAME"

    work = rates.copy()
    work["FR_CODE_N"] = work["FR_CODE"].map(_currency)
    work["TO_CODE_N"] = work["TO_CODE"].map(_currency)
    work["FACTOR_N"] = pd.to_numeric(work["FACTOR"], errors="coerce")
    work["ADDED_DTE_N"] = pd.to_datetime(work.get("ADDED_DTE"), errors="coerce")
    work["CONVDATE_N"] = pd.to_datetime(work.get("CONVDATE"), errors="coerce")
    work = work[
        (work["FR_CODE_N"] == base)
        & (work["TO_CODE_N"] == target)
        & work["FACTOR_N"].notna()
        & work["FACTOR_N"].ne(0)
    ].copy()
    if work.empty:
        return None, None, "NO_RATE"

    sort_cols = [c for c in ["ADDED_DTE_N", "CONVDATE_N", "TBLCONV_DOC_NO"] if c in work.columns]
    work = work.sort_values(sort_cols, ascending=False, na_position="last")
    row = work.iloc[0]
    factor_date = row.get("CONVDATE_N")
    if pd.isna(factor_date):
        factor_date = row.get("ADDED_DTE_N")
    return float(row["FACTOR_N"]), factor_date, "LOOKUP"


def _apply_counter_mock_overrides(
    counters: pd.DataFrame,
    mock_counters: pd.DataFrame,
) -> pd.DataFrame:
    """
    Apply optional Excel counter snapshots.

    MockCounters columns:
        ROLE, COMPANYNO, DIVISION, COUNTER_BEFORE

    A nonblank COUNTER_BEFORE overrides the database counter. A blank value
    means "use the database lookup". The supplied number is the last-used
    counter value, so allocation still starts at COUNTER_BEFORE + 1.
    """
    if mock_counters.empty:
        return counters

    mock = _normalize_columns(mock_counters)
    required = {"ROLE", "COMPANYNO", "DIVISION", "COUNTER_BEFORE"}
    missing = required - set(mock.columns)
    if missing:
        raise ValueError(
            "MockCounters sheet is missing columns: " + ", ".join(sorted(missing))
        )

    result = counters.copy()
    if "COUNTER_SOURCE" not in result.columns:
        result["COUNTER_SOURCE"] = "DB"

    for _, row in mock.iterrows():
        role = _text(row.get("ROLE")).upper()
        before = _number(row.get("COUNTER_BEFORE"))
        if not role or before is None:
            # Blank COUNTER_BEFORE means use DB.
            continue
        if role not in COUNTER_ROLE_MAP:
            raise ValueError(f"MockCounters contains unsupported ROLE={role}")

        companyno = row.get("COMPANYNO")
        division = row.get("DIVISION")
        company_key = CounterAllocator._key_part(companyno)
        division_key = CounterAllocator._key_part(division)

        if result.empty:
            exact_mask = pd.Series(dtype=bool)
        else:
            exact_mask = result.apply(
                lambda current: (
                    _text(current.get("ROLE")).upper() == role
                    and CounterAllocator._key_part(current.get("COMPANYNO")) == company_key
                    and CounterAllocator._key_part(current.get("DIVISION")) == division_key
                ),
                axis=1,
            )

        matched_indexes = result.index[exact_mask].tolist() if len(exact_mask) else []

        # Permit the same unique NULL/BLANK fallback used by CounterAllocator.
        if not matched_indexes and division_key in {"<NULL>", "<BLANK>"} and not result.empty:
            fallback_mask = result.apply(
                lambda current: (
                    _text(current.get("ROLE")).upper() == role
                    and CounterAllocator._key_part(current.get("COMPANYNO")) == company_key
                    and CounterAllocator._key_part(current.get("DIVISION"))
                        in {"<NULL>", "<BLANK>"}
                ),
                axis=1,
            )
            matched_indexes = result.index[fallback_mask].tolist()

        if matched_indexes:
            # Update every duplicate physical DB row for the same logical key.
            result.loc[matched_indexes, "COUNTER"] = int(before)
            result.loc[matched_indexes, "COUNTER_SOURCE"] = "EXCEL_MOCK"
            continue

        doc_category, doc_type = COUNTER_ROLE_MAP[role]
        new_row = {
            "ROLE": role,
            "DOC_CATEGORY": doc_category,
            "DOC_TYPE": doc_type,
            "COMPANYNO": companyno,
            "DIVISION": division,
            "COUNTER": int(before),
            "INCREMENT": 1,
            "COUNTER_SOURCE": "EXCEL_MOCK",
        }
        result = pd.concat([result, pd.DataFrame([new_row])], ignore_index=True)

    return result


@dataclass
class CounterAllocator:
    frame: pd.DataFrame

    @staticmethod
    def _key_part(value: Any) -> str:
        """
        Preserve SQL NULL and blank text as different counter-key values.

        COUNTERSTBL may legitimately contain both:
            DIVISION IS NULL
            DIVISION = '' / spaces

        They must not collapse into the same key.
        """
        if value is None:
            return "<NULL>"

        try:
            if pd.isna(value):
                return "<NULL>"
        except (TypeError, ValueError):
            pass

        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else "<BLANK>"

        number = _number(value)
        if number is not None and float(number).is_integer():
            return str(int(number))

        text_value = _text(value)
        return text_value if text_value else "<BLANK>"

    def __post_init__(self) -> None:
        self.frame = self.frame.copy()
        required = {"ROLE", "COMPANYNO", "DIVISION", "COUNTER"}
        missing = required - set(self.frame.columns)
        if missing:
            raise ValueError(f"COUNTERSTBL lookup is missing columns: {sorted(missing)}")

        self.frame["ROLE"] = self.frame["ROLE"].map(_text)
        self.frame["COUNTER_KEY"] = self.frame.apply(
            lambda row: (
                _text(row.get("ROLE")),
                self._key_part(row.get("COMPANYNO")),
                self._key_part(row.get("DIVISION")),
            ),
            axis=1,
        )
        # COUNTERSTBL can contain duplicate physical rows for the same logical
        # counter key. If every duplicate carries the same COUNTER value, treat
        # them as one logical counter for read-only simulation. If their values
        # differ, stop because selecting one would be unsafe.
        if self.frame["COUNTER_KEY"].duplicated().any():
            collapsed_rows: list[pd.Series] = []
            conflicting_groups: list[pd.DataFrame] = []

            for _, group in self.frame.groupby("COUNTER_KEY", sort=False, dropna=False):
                counter_values = {
                    int(_number(value, 0) or 0)
                    for value in group["COUNTER"].tolist()
                }
                increment_values = {
                    int(_number(value, 1) or 1)
                    for value in group.get(
                        "INCREMENT",
                        pd.Series([1] * len(group), index=group.index),
                    ).tolist()
                }

                if len(counter_values) > 1 or len(increment_values) > 1:
                    conflicting_groups.append(group.copy())
                    continue

                row = group.iloc[0].copy()
                row["SOURCE_ROW_COUNT"] = len(group)
                collapsed_rows.append(row)

            if conflicting_groups:
                conflict = pd.concat(conflicting_groups, ignore_index=True)
                columns = [
                    column
                    for column in [
                        "ROLE",
                        "DOC_CATEGORY",
                        "DOC_TYPE",
                        "COMPANYNO",
                        "DIVISION",
                        "COUNTER",
                        "INCREMENT",
                    ]
                    if column in conflict.columns
                ]
                raise ValueError(
                    "COUNTERSTBL contains duplicate logical keys with different "
                    "COUNTER or INCREMENT values:\n"
                    + conflict[columns].to_string(index=False)
                )

            self.frame = pd.DataFrame(collapsed_rows).reset_index(drop=True)
        else:
            self.frame["SOURCE_ROW_COUNT"] = 1

        self.before = {
            row["COUNTER_KEY"]: int(_number(row.get("COUNTER"), 0) or 0)
            for _, row in self.frame.iterrows()
        }
        self.current = self.before.copy()
        self.increment = {
            row["COUNTER_KEY"]: int(_number(row.get("INCREMENT"), 1) or 1)
            for _, row in self.frame.iterrows()
        }

    def _resolve_key(
        self,
        role: str,
        companyno: Any,
        division: Any,
    ) -> tuple[str, str, str]:
        role_key = _text(role)
        company_key = self._key_part(companyno)
        division_key = self._key_part(division)

        exact_key = (role_key, company_key, division_key)
        if exact_key in self.current:
            return exact_key

        # SQL NULL and fixed-width blank text are distinct in storage, but many
        # P2000 counter rows use one form while the source business row uses the
        # other. Only fall back between these two forms when exactly one logical
        # candidate exists.
        if division_key in {"<NULL>", "<BLANK>"}:
            candidates = [
                key
                for key in self.current
                if key[0] == role_key
                and key[1] == company_key
                and key[2] in {"<NULL>", "<BLANK>"}
            ]

            if len(candidates) == 1:
                return candidates[0]

            if len(candidates) > 1:
                details = [
                    {
                        "DIVISION_KEY": key[2],
                        "COUNTER": self.current[key],
                        "INCREMENT": self.increment[key],
                    }
                    for key in candidates
                ]
                raise ValueError(
                    "COUNTERSTBL has ambiguous NULL/BLANK rows for "
                    f"ROLE={role}, COMPANYNO={companyno}: {details}"
                )

        available = [
            key[2]
            for key in self.current
            if key[0] == role_key and key[1] == company_key
        ]
        raise ValueError(
            "COUNTERSTBL has no unique row for "
            f"ROLE={role}, COMPANYNO={companyno}, DIVISION={division!r}. "
            f"Available division keys: {available}"
        )

    def next(self, role: str, companyno: Any, division: Any) -> int:
        key = self._resolve_key(role, companyno, division)
        self.current[key] += self.increment[key]
        return self.current[key]

    def output(self) -> pd.DataFrame:
        result = self.frame.copy()
        result["COUNTER_BEFORE"] = result["COUNTER_KEY"].map(self.before)
        result["COUNTER_AFTER"] = result["COUNTER_KEY"].map(self.current)
        result["INCREMENT_USED"] = (
            result["COUNTER_AFTER"] - result["COUNTER_BEFORE"]
        )
        result = result[result["INCREMENT_USED"].ne(0)].copy()
        result["STATUS"] = "SIMULATED_UPDATE"

        columns = [
            "ROLE",
            "DOC_CATEGORY",
            "DOC_TYPE",
            "COMPANYNO",
            "DIVISION",
            "COUNTER_SOURCE",
            "COUNTER_BEFORE",
            "COUNTER_AFTER",
            "INCREMENT_USED",
            "SOURCE_ROW_COUNT",
            "STATUS",
        ]
        return result[
            [column for column in columns if column in result.columns]
        ].reset_index(drop=True)


def _single(group: pd.DataFrame, column: str, errors: list[str], required: bool = True) -> Any:
    if column not in group.columns:
        if required:
            errors.append(f"MISSING_{column}")
        return None
    values = [v for v in group[column].tolist() if not pd.isna(v) and _text(v) != ""]
    normalized = list(dict.fromkeys(_text(v) if isinstance(v, str) else v for v in values))
    if len(normalized) > 1:
        errors.append(f"INCONSISTENT_{column}")
    if not normalized:
        if required:
            errors.append(f"MISSING_{column}")
        return None
    return normalized[0]


def _valid_factor(value: Any) -> float | None:
    factor = _number(value)
    return factor if factor not in (None, 0) else None


def _resolve_line_currency_factor(
    source: pd.Series,
    rates: pd.DataFrame,
    default_base_currency: str,
) -> tuple[
    float | None,
    pd.Timestamp | None,
    str,
    str,
    str,
]:
    """Resolve one invoice line's own currency factor."""
    line_base = (
        _currency(source.get("INV_CURENCY_BASE"))
        or _currency(source.get("CURENCY_BASE"))
        or _currency(default_base_currency)
        or "USD"
    )
    line_conv = (
        _currency(source.get("INV_CURENCY_CONV"))
        or _currency(source.get("CURENCY_CONV"))
        or line_base
    )

    inv_date = _date(source.get("INV_CURENCY_DATE"))
    arap_date = _date(source.get("CURENCY_DATE"))

    if line_base == line_conv:
        return (
            1.0,
            inv_date or arap_date,
            "SAME_CURRENCY_PAIR",
            line_base,
            line_conv,
        )

    inv_factor = _valid_factor(source.get("INV_CURENCY_FACTOR"))
    if inv_factor is not None:
        return (
            inv_factor,
            inv_date,
            "INV_HDR",
            line_base,
            line_conv,
        )

    arap_factor = _valid_factor(source.get("CURENCY_FACTOR"))
    if arap_factor is not None:
        return (
            arap_factor,
            arap_date,
            "ARAP",
            line_base,
            line_conv,
        )

    factor, factor_date, rate_status = _lookup_rate(
        rates,
        line_base,
        line_conv,
    )
    return (
        factor,
        factor_date,
        "TBLCONV_FALLBACK:" + rate_status,
        line_base,
        line_conv,
    )


def _max_currency_state(group: pd.DataFrame) -> tuple[Any, str]:
    candidates: list[float] = []
    sources: list[str] = []

    for column, source_name in [
        ("INV_CURENCY_STATE", "INV_HDR"),
        ("CURENCY_STATE", "ARAP"),
    ]:
        if column not in group.columns:
            continue
        column_values: list[float] = []
        for value in group[column].tolist():
            number = _number(value)
            if number is not None:
                column_values.append(number)
        if column_values:
            candidates.extend(column_values)
            sources.append(source_name)

    if not candidates:
        return 1, "DEFAULT_1"

    state = max(candidates)
    if float(state).is_integer():
        state = int(state)
    return state, "MAX_" + "+".join(sources)


def generate_insert_ready_excel(
    input_path: str | Path,
    output_path: str | Path,
    connection_string: str,
    user: str = "ILC",
    process_time: str | pd.Timestamp | None = None,
) -> None:
    process_ts = (
        pd.Timestamp(process_time)
        if process_time
        else pd.Timestamp.now().floor("s")
    )

    receipt_input = _normalize_columns(_read_first_sheet(input_path))

    # Canonical input names. Accept the old grouping label only as a convenience;
    # it is never inserted into CHECK_HDR.DOC_NO.
    if (
        "INPUT_HDR_GROUP_ID" not in receipt_input.columns
        and "RECEIPT_KEY" in receipt_input.columns
    ):
        receipt_input = receipt_input.rename(
            columns={"RECEIPT_KEY": "INPUT_HDR_GROUP_ID"}
        )
    if (
        "C_PAID_AMOUNT" not in receipt_input.columns
        and "C_PAID_TOTAL" in receipt_input.columns
    ):
        receipt_input = receipt_input.rename(
            columns={"C_PAID_TOTAL": "C_PAID_AMOUNT"}
        )

    required = {
        "INPUT_HDR_GROUP_ID",
        "BANK_ID",
        "ACCOUNT_CURRENCY",
        "C_PAID_AMOUNT",
        "CHECK_DATE",
        "PAY_USER_DOC",
        "ACCTNO",
        "GL_CODE_DISC",
    }
    missing = sorted(required - set(receipt_input.columns))
    if missing:
        raise ValueError(f"Input sheet is missing columns: {missing}")

    if "NOTE" not in receipt_input.columns:
        receipt_input["NOTE"] = None

    receipt_input = receipt_input.dropna(
        subset=["INPUT_HDR_GROUP_ID", "PAY_USER_DOC", "ACCTNO"]
    ).copy()
    receipt_input["INPUT_HDR_GROUP_ID"] = receipt_input[
        "INPUT_HDR_GROUP_ID"
    ].map(_text)
    receipt_input["PAY_USER_DOC_KEY"] = receipt_input["PAY_USER_DOC"].map(
        _user_doc
    )
    receipt_input["PAY_DOC_CATEGORY_KEY"] = "IV"
    receipt_input["ACCTNO_KEY"] = receipt_input["ACCTNO"].map(_text)

    mock_counters = _read_optional_sheet(
        input_path,
        ["MockCounters", "Mock_Counters", "CounterMock"],
    )

    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError(
            "pyodbc is required for database lookups: pip install pyodbc"
        ) from exc

    if not connection_string:
        raise ValueError(
            "SQL Server connection settings are missing. Configure the "
            "P2000_DB_* variables in .env or in the operating-system "
            "environment."
        )

    with pyodbc.connect(connection_string) as connection:
        (
            arap,
            inv_hdr,
            banks,
            customers,
            rates,
            counters,
        ) = _load_database_lookups(connection, receipt_input)

    arap = _trim_table(arap)
    inv_hdr = _trim_table(inv_hdr)
    counters = _apply_counter_mock_overrides(counters, mock_counters)

    arap["USER_DOC_KEY"] = arap["USER_DOC"].map(_user_doc)
    arap["DOC_CATEGORY_KEY"] = arap["DOC_CATEGORY"].map(_text)
    arap["ACCTNO_KEY"] = arap["ACCTNO"].map(_text)
    arap = arap[
        arap.get("LINE", 0).fillna(0).eq(0)
        & arap["DOC_CATEGORY_KEY"].eq("IV")
    ].copy()

    inv_required = {"DOC_NO", "DOC_TOTAL", "C_DOC_TOTAL"}
    inv_missing = sorted(inv_required - set(inv_hdr.columns))
    if inv_missing:
        raise ValueError(
            "INV_HDR lookup is missing columns: "
            + ", ".join(inv_missing)
        )

    inv_optional = [
        "CURENCY_STATE",
        "CURENCY_BASE",
        "CURENCY_CONV",
        "CURENCY_FACTOR",
        "CURENCY_DATE",
    ]
    inv_columns = ["DOC_NO", "DOC_TOTAL", "C_DOC_TOTAL"] + [
        column
        for column in inv_optional
        if column in inv_hdr.columns
    ]
    inv_details = inv_hdr[inv_columns].copy()

    if inv_details["DOC_NO"].duplicated().any():
        duplicates = (
            inv_details.loc[
                inv_details["DOC_NO"].duplicated(False),
                "DOC_NO",
            ]
            .drop_duplicates()
            .tolist()
        )
        raise ValueError(
            "INV_HDR contains duplicate DOC_NO rows for input invoices: "
            + ", ".join(str(value) for value in duplicates)
        )

    inv_rename = {
        "DOC_TOTAL": "INV_DOC_TOTAL",
        "C_DOC_TOTAL": "INV_C_DOC_TOTAL",
        "CURENCY_STATE": "INV_CURENCY_STATE",
        "CURENCY_BASE": "INV_CURENCY_BASE",
        "CURENCY_CONV": "INV_CURENCY_CONV",
        "CURENCY_FACTOR": "INV_CURENCY_FACTOR",
        "CURENCY_DATE": "INV_CURENCY_DATE",
    }
    inv_details = inv_details.rename(columns=inv_rename)

    arap = arap.merge(
        inv_details,
        how="left",
        on="DOC_NO",
        validate="many_to_one",
        indicator="INV_HDR_MERGE",
    )

    try:
        merged = receipt_input.merge(
            arap,
            how="left",
            left_on=[
                "PAY_USER_DOC_KEY",
                "PAY_DOC_CATEGORY_KEY",
                "ACCTNO_KEY",
            ],
            right_on=[
                "USER_DOC_KEY",
                "DOC_CATEGORY_KEY",
                "ACCTNO_KEY",
            ],
            suffixes=("_INPUT", "_ARAP"),
            indicator=True,
            validate="many_to_one",
        )
    except pd.errors.MergeError as exc:
        raise ValueError(
            "ACCOUNT_AR_AP is not unique for normalized "
            "PAY_USER_DOC + ACCTNO + DOC_CATEGORY='IV' + LINE=0. "
            "PAY_DOC_NO cannot be derived safely."
        ) from exc

    allocator = CounterAllocator(counters)
    hdr_rows: list[dict[str, Any]] = []
    line_rows: list[dict[str, Any]] = []
    arap_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    batch_numbers: dict[tuple[str, str], int] = {}

    for input_hdr_group_id, group in merged.groupby("INPUT_HDR_GROUP_ID", sort=False):
        errors: list[str] = []
        if (group["_merge"] != "both").any():
            errors.append("MISSING_ARAP")

        matched_arap = group[group["_merge"] == "both"]
        if (
            not matched_arap.empty
            and matched_arap["INV_HDR_MERGE"].ne("both").any()
        ):
            errors.append("MISSING_INV_HDR")

        bank_id = _single(group, "BANK_ID", errors)
        account_currency = _currency(
            _single(group, "ACCOUNT_CURRENCY", errors)
        )

        check_date = _date(_single(group, "CHECK_DATE", errors)) or process_ts
        acctno = _text(_single(group, "ACCTNO_INPUT", errors))
        note_value = _single(group, "NOTE", errors, required=False)
        note_value = _text(note_value) or None

        matched = group[group["_merge"] == "both"].copy()
        arap_base_values = sorted(
            {
                _currency(value)
                for value in matched.get(
                    "CURENCY_BASE",
                    pd.Series(dtype=object),
                )
                if _currency(value)
            }
        )
        default_base_currency = (
            arap_base_values[0]
            if arap_base_values
            else "USD"
        )

        subc_values = [
            value
            for value in matched.get(
                "SUBC",
                pd.Series(dtype=object),
            ).tolist()
            if not pd.isna(value)
        ]
        header_subc = subc_values[0] if subc_values else None
        if len({_text(value) for value in subc_values}) > 1:
            errors.append("MIXED_SUBC")

        bank_id_matches = banks[
            banks["BANK_ID"].map(_text) == _text(bank_id)
        ]
        bank_currency_column = "CURENCY_BASE"

        if bank_currency_column not in banks.columns:
            bank_matches = bank_id_matches.iloc[0:0]
            errors.append("BANKS_ACCOUNTS_MISSING_CURENCY_BASE_COLUMN")
        else:
            bank_matches = bank_id_matches[
                bank_id_matches[bank_currency_column]
                .map(_currency)
                .eq(account_currency)
            ]

        bank = (
            bank_matches.iloc[0]
            if len(bank_matches) == 1
            else pd.Series(dtype=object)
        )
        if len(bank_matches) != 1:
            errors.append(
                "BANK_LOOKUP_NOT_UNIQUE:"
                f"BANK_ID={_text(bank_id)},"
                f"CURENCY_BASE={account_currency},"
                f"MATCH_COUNT={len(bank_matches)}"
            )

        customer_matches = customers[
            (customers["CUST_VEND"].map(_text) == "C")
            & (customers["ACCTNO"].map(_text) == acctno)
        ]

        if "SUBC" in customers.columns:
            customer_matches = customer_matches[
                customer_matches["SUBC"]
                .map(_text)
                .eq(_text(header_subc))
            ]
        else:
            errors.append("CUSTVEND_MISSING_SUBC_COLUMN")
            customer_matches = customer_matches.iloc[0:0]

        unique_customer_names = sorted(
            {
                _text(value)
                for value in customer_matches.get(
                    "NAME",
                    pd.Series(dtype=object),
                ).tolist()
                if _text(value)
            }
        )

        if len(unique_customer_names) == 1:
            customer = customer_matches.iloc[0].copy()
            customer["NAME"] = unique_customer_names[0]
            customer_lookup_status = (
                "UNIQUE_ROW"
                if len(customer_matches) == 1
                else "DUPLICATE_ROWS_SAME_NAME"
            )
        else:
            customer = pd.Series(dtype=object)
            customer_lookup_status = (
                "NO_MATCH"
                if len(customer_matches) == 0
                else "MULTIPLE_NAMES"
            )
            customer_error_name = (
                "CUSTOMER_LOOKUP_NO_MATCH"
                if len(customer_matches) == 0
                else "CUSTOMER_LOOKUP_NOT_UNIQUE"
            )
            errors.append(
                customer_error_name + ":"
                f"ACCTNO={acctno},"
                f"SUBC={_text(header_subc)},"
                f"MATCH_COUNT={len(customer_matches)},"
                f"UNIQUE_NAMES={unique_customer_names}"
            )

        (
            payment_lookup_factor,
            payment_lookup_factor_date,
            payment_lookup_status,
        ) = _lookup_rate(
            rates,
            default_base_currency,
            account_currency,
        )

        hdr_currency_state, currency_state_source = _max_currency_state(
            matched
        )

        companyno_values = [
            value
            for value in matched.get(
                "COMPANYNO",
                pd.Series(dtype=object),
            ).tolist()
            if not pd.isna(value)
        ]
        companyno = companyno_values[0] if companyno_values else None
        if len(
            {
                CounterAllocator._key_part(value)
                for value in companyno_values
            }
        ) > 1:
            errors.append("MIXED_COMPANYNO")

        counter_division = bank.get("DIVISION")
        if counter_division is None or _text(counter_division) == "":
            division_values = [
                value
                for value in matched.get(
                    "DIVISION",
                    pd.Series(dtype=object),
                ).tolist()
                if not pd.isna(value)
            ]
            counter_division = (
                division_values[0]
                if division_values
                else None
            )

        try:
            batch_key = (
                CounterAllocator._key_part(companyno),
                CounterAllocator._key_part(counter_division),
            )
            if batch_key not in batch_numbers:
                batch_numbers[batch_key] = allocator.next(
                    "BC_BATCH_NO",
                    companyno,
                    counter_division,
                )

            batch_no = batch_numbers[batch_key]
            doc_no = allocator.next(
                "CHECK_DOC_NO",
                companyno,
                counter_division,
            )
            check_no = str(
                allocator.next(
                    "R2_CHECK_NO",
                    companyno,
                    counter_division,
                )
            ).zfill(6)
            check_counter = allocator.next(
                "ACRN_CHECK_COUNTER",
                companyno,
                counter_division,
            )
        except ValueError as exc:
            errors.append(str(exc))
            batch_no = None
            doc_no = None
            check_no = None
            check_counter = None

        calculated: list[dict[str, Any]] = []

        for _, source in group.iterrows():
            if source.get("_merge") != "both":
                continue

            pay_doc_no = int(source["DOC_NO"])
            current_c_paid_amount = _calculated_amount(
                source.get("C_PAID_AMOUNT")
            )
            inv_doc_total = _calculated_amount(
                source.get("INV_DOC_TOTAL")
            )
            inv_c_doc_total = _calculated_amount(
                source.get("INV_C_DOC_TOTAL")
            )

            if source.get("INV_HDR_MERGE") != "both":
                errors.append(f"MISSING_INV_HDR_{pay_doc_no}")
                continue
            if inv_doc_total is None:
                errors.append(f"MISSING_INV_DOC_TOTAL_{pay_doc_no}")
                continue
            if inv_c_doc_total is None:
                errors.append(f"MISSING_INV_C_DOC_TOTAL_{pay_doc_no}")
                continue
            if current_c_paid_amount is None:
                errors.append(f"MISSING_C_PAID_AMOUNT_{pay_doc_no}")
                continue

            (
                line_factor,
                line_factor_date,
                line_factor_source,
                line_base_currency,
                line_invoice_currency,
            ) = _resolve_line_currency_factor(
                source,
                rates,
                default_base_currency,
            )

            if line_factor in (None, 0):
                errors.append(
                    f"NO_LINE_CURENCY_FACTOR_{pay_doc_no}:"
                    f"BASE={line_base_currency},"
                    f"CONV={line_invoice_currency}"
                )
                continue

            if payment_lookup_factor in (None, 0):
                errors.append(
                    f"NO_PAYMENT_ACCOUNT_FACTOR_{pay_doc_no}:"
                    f"BASE={line_base_currency},"
                    f"ACCOUNT_CURRENCY={account_currency}"
                )
                continue

            # C_PAID_AMOUNT is the current receipt delta in invoice converted
            # currency. ARAP.C_PAID_TOTAL is the cumulative value after adding
            # this delta; it is not a per-receipt input.
            line_c_netamount = current_c_paid_amount
            line_netamount = _calculated_amount(
                line_c_netamount / line_factor
            )
            line_account_amount = _calculated_amount(
                line_netamount * payment_lookup_factor
            )
            conversion_mode = (
                "PAYMENT_EQUALS_INVOICE_CURRENCY"
                if account_currency == line_invoice_currency
                else "PAYMENT_DIFFERS_FROM_INVOICE_CURRENCY"
            )

            line_c_amount = inv_c_doc_total
            line_amount = _calculated_amount(
                line_c_amount / line_factor
            )
            line_c_discount = _calculated_amount(
                line_c_amount - line_c_netamount
            )
            line_discount = _calculated_amount(
                line_amount - line_netamount
            )

            existing_paid_total = _number(
                source.get("PAID_TOTAL_ARAP")
                if "PAID_TOTAL_ARAP" in source.index
                else source.get("PAID_TOTAL"),
                0,
            ) or 0.0
            existing_c_paid_total = _number(
                source.get("C_PAID_TOTAL_ARAP")
                if "C_PAID_TOTAL_ARAP" in source.index
                else source.get("C_PAID_TOTAL"),
                0,
            ) or 0.0

            new_paid_total = _calculated_amount(
                existing_paid_total + line_netamount
            )
            new_c_paid_total = _calculated_amount(
                existing_c_paid_total + line_c_netamount
            )

            calculated.append(
                {
                    "source": source,
                    "LINE_ACCOUNT_AMOUNT": line_account_amount,
                    "ACCOUNT_CURRENCY": account_currency,
                    "LINE_BASE_CURRENCY": line_base_currency,
                    "INVOICE_CURRENCY": line_invoice_currency,
                    "LINE_FACTOR": line_factor,
                    "LINE_FACTOR_DATE": line_factor_date,
                    "LINE_FACTOR_SOURCE": line_factor_source,
                    "CONVERSION_MODE": conversion_mode,
                    "INV_DOC_TOTAL": inv_doc_total,
                    "INV_C_DOC_TOTAL": inv_c_doc_total,
                    "INV_DOC_TOTAL_DIFF": _diff(
                        line_amount,
                        inv_doc_total,
                    ),
                    "EXISTING_ARAP_PAID_TOTAL": existing_paid_total,
                    "EXISTING_ARAP_C_PAID_TOTAL": existing_c_paid_total,
                    "NEW_ARAP_PAID_TOTAL": new_paid_total,
                    "NEW_ARAP_C_PAID_TOTAL": new_c_paid_total,
                    "LINE_AMOUNT": line_amount,
                    "LINE_NETAMOUNT": line_netamount,
                    "LINE_DISCOUNT": line_discount,
                    "LINE_C_AMOUNT": line_c_amount,
                    "LINE_C_NETAMOUNT": line_c_netamount,
                    "LINE_C_DISCOUNT": line_c_discount,
                }
            )

        line_base_values = sorted(
            {
                item["LINE_BASE_CURRENCY"]
                for item in calculated
                if item["LINE_BASE_CURRENCY"]
            }
        )
        base_currency = (
            line_base_values[0]
            if line_base_values
            else default_base_currency
        )
        if len(line_base_values) > 1:
            errors.append("MIXED_BASE_CURRENCY")

        line_conv_values = sorted(
            {
                item["INVOICE_CURRENCY"]
                for item in calculated
                if item["INVOICE_CURRENCY"]
            }
        )
        header_conv = (
            line_conv_values[0]
            if line_conv_values
            else base_currency
        )
        if len(line_conv_values) > 1:
            errors.append("MIXED_CURENCY_CONV_REVIEW")

        applied = _calculated_amount(
            sum(item["LINE_AMOUNT"] or 0 for item in calculated)
        ) or 0.0
        amount = _calculated_amount(
            sum(item["LINE_NETAMOUNT"] or 0 for item in calculated)
        ) or 0.0
        discount = _calculated_amount(
            sum(item["LINE_DISCOUNT"] or 0 for item in calculated)
        ) or 0.0
        c_applied = _calculated_amount(
            sum(item["LINE_C_AMOUNT"] or 0 for item in calculated)
        ) or 0.0
        c_amount = _calculated_amount(
            sum(item["LINE_C_NETAMOUNT"] or 0 for item in calculated)
        ) or 0.0
        c_discount = _calculated_amount(
            sum(item["LINE_C_DISCOUNT"] or 0 for item in calculated)
        ) or 0.0

        conversion_modes = sorted(
            {item["CONVERSION_MODE"] for item in calculated}
        )
        factor_sources = sorted(
            {item["LINE_FACTOR_SOURCE"] for item in calculated}
        )
        factor_values = sorted(
            {
                round(float(item["LINE_FACTOR"]), 9)
                for item in calculated
                if item["LINE_FACTOR"] not in (None, 0)
            }
        )

        account_amount = _calculated_amount(
            sum(item["LINE_ACCOUNT_AMOUNT"] or 0 for item in calculated)
        ) or 0.0

        zero_effective_amount = abs(amount) <= AMOUNT_TOLERANCE
        effective_account_factor = (
            None
            if zero_effective_amount
            else _calculated_amount(account_amount / amount)
        )
        effective_currency_factor = (
            None
            if zero_effective_amount
            else _calculated_amount(c_amount / amount)
        )

        # ACCOUNT_FACTOR is sourced from the base-to-payment TBLCONV lookup.
        # ACCOUNT_AMOUNT / AMOUNT is retained as a validation relationship.
        account_factor = _calculated_amount(payment_lookup_factor)
        account_factor_date = payment_lookup_factor_date
        account_rate_status = (
            "TBLCONV_PAYMENT_LOOKUP:" + payment_lookup_status
        )
        if account_factor in (None, 0):
            errors.append("NO_ACCOUNT_FACTOR")

        if len(factor_values) == 1:
            currency_factor = factor_values[0]
            common_factor_dates = [
                item["LINE_FACTOR_DATE"]
                for item in calculated
                if item["LINE_FACTOR_DATE"] is not None
            ]
            currency_date = (
                common_factor_dates[0]
                if common_factor_dates
                else check_date
            )
            currency_rate_status = "COMMON_LINE_FACTOR"
        elif not zero_effective_amount:
            currency_factor = effective_currency_factor
            currency_date = check_date
            currency_rate_status = "EFFECTIVE_FROM_LINES"
        else:
            currency_factor = None
            currency_date = None
            currency_rate_status = "UNAVAILABLE_ZERO_MIXED_FACTOR_AMOUNT"
            errors.append(
                "OUT_OF_SCOPE_ZERO_PAYMENT_RECEIPT:"
                f"ACCOUNT_AMOUNT={account_amount},"
                f"SUM_LINE_NETAMOUNT={amount},"
                f"SUM_LINE_C_NETAMOUNT={c_amount}"
            )

        if currency_factor in (None, 0):
            errors.append("NO_CURENCY_FACTOR")

        generated_lines: list[dict[str, Any]] = []
        for item in calculated:
            source = item["source"]
            line_row = _init_row(LINE_SCHEMA, LINE_DEFAULTS)
            line_row.update(
                {
                    "DOC_NO": doc_no,
                    "CHECK_NO": check_no,
                    "PAY_USER_DOC": _user_doc(source.get("USER_DOC")),
                    "PAY_DOC_CATEGORY": "IV",
                    "LINE": allocator.next(
                        "R2_LINE_NO",
                        companyno,
                        counter_division,
                    ),
                    "PAY_DOC_NO": int(source.get("DOC_NO")),
                    "PAY_APPLY": check_date,
                    "PAY_DOC_DATE": (
                        (_date(source.get("DOC_DATE")) or check_date)
                        .normalize()
                        + timedelta(days=2)
                    ),
                    "PAY_TYPE": _text(source.get("DOC_TYPE")) or "I",
                    "DISCOUNT": item["LINE_DISCOUNT"],
                    "NETAMOUNT": item["LINE_NETAMOUNT"],
                    "AMOUNT": item["LINE_AMOUNT"],
                    "ACCTNO": (
                        _text(source.get("ACCTNO_INPUT"))
                        or _text(source.get("ACCTNO_ARAP"))
                    ),
                    "SUBC": source.get("SUBC"),
                    "GL_CODE_AMT": "110101-00",
                    "GL_CODE_DISC": (
                        _text(source.get("GL_CODE_DISC"))
                        or "750201-00"
                    ),
                    "COMPANYNO": source.get("COMPANYNO"),
                    "DIVISION": source.get("DIVISION"),
                    "DEPART": source.get("DEPART"),
                    "C_DISCOUNT": item["LINE_C_DISCOUNT"],
                    "C_NETAMOUNT": item["LINE_C_NETAMOUNT"],
                    "C_AMOUNT": item["LINE_C_AMOUNT"],
                    "ADDED_USR": user,
                    "ADDED_DTE": process_ts,
                }
            )
            generated_lines.append(line_row)
            line_rows.append(line_row)

            arap_row: dict[str, Any] = {}
            for column in ARAP_SCHEMA:
                arap_column = f"{column}_ARAP"
                arap_row[column] = (
                    source.get(arap_column)
                    if arap_column in source.index
                    else source.get(column)
                )
            arap_row.update(
                {
                    "USER_DOC": _user_doc(source.get("USER_DOC")),
                    "DOC_TOTAL": item["INV_DOC_TOTAL"],
                    "C_DOC_TOTAL": item["INV_C_DOC_TOTAL"],
                    "PAID_TOTAL": item["NEW_ARAP_PAID_TOTAL"],
                    "C_PAID_TOTAL": item["NEW_ARAP_C_PAID_TOTAL"],
                    "UPDATED_USR": user,
                    "UPDATED_DTE": process_ts,
                }
            )
            arap_rows.append(arap_row)

        matched_divisions = [
            value
            for value in matched.get(
                "DIVISION",
                pd.Series(dtype=object),
            ).tolist()
            if not pd.isna(value)
        ]
        matched_departs = [
            value
            for value in matched.get(
                "DEPART",
                pd.Series(dtype=object),
            ).tolist()
            if not pd.isna(value)
        ]

        hdr_company = bank.get("COMPANY")
        if hdr_company is None or _text(hdr_company) == "":
            hdr_company = companyno

        hdr_division = bank.get("DIVISION")
        if hdr_division is None or _text(hdr_division) == "":
            hdr_division = (
                matched_divisions[0]
                if matched_divisions
                else bank.get("DIVISION")
            )

        hdr_depart = bank.get("DEPART")
        if hdr_depart is None or _text(hdr_depart) == "":
            hdr_depart = (
                matched_departs[0]
                if matched_departs
                else bank.get("DEPART")
            )

        hdr_row = _init_row(HDR_SCHEMA, HDR_DEFAULTS)
        hdr_row.update(
            {
                "DOC_NO": doc_no,
                "CHECK_NO": check_no,
                "CHECK_COUNTER": check_counter,
                "BATCH_NO": batch_no,
                "BANK_ID": bank_id,
                "ACCOUNT_NO": bank.get("ACCOUNT_NO"),
                "ACCTNO": acctno,
                "SUBC": header_subc,
                "CUST_VEND": "C",
                "CHECK_DATE": check_date,
                "PAYMENT_DATE": check_date,
                "NOTE": note_value,
                "PAYEE": customer.get("NAME"),
                "GL_CODE": bank.get("GL_CODE"),
                "COMPANY": hdr_company,
                "DIVISION": hdr_division,
                "DEPART": hdr_depart,
                "CURENCY_STATE": hdr_currency_state,
                "CURENCY_BASE": base_currency,
                "CURENCY_CONV": header_conv,
                "CURENCY_FACTOR": currency_factor,
                "CURENCY_DATE": currency_date,
                "DISCOUNT": discount,
                "NETAMOUNT": amount,
                "AMOUNT": amount,
                "APPLIED": applied,
                "C_AMOUNT": c_amount,
                "C_DISCOUNT": c_discount,
                "C_NETAMOUNT": c_amount,
                "C_APPLIED": c_applied,
                "ACCOUNT_CURRENCY": account_currency,
                "ACCOUNT_AMOUNT": account_amount,
                "ACCOUNT_FACTOR": account_factor,
                "ACCOUNT_FACTOR_DATE": account_factor_date,
                "ADDED_USR": user,
                "ADDED_DTE": process_ts,
            }
        )
        hdr_rows.append(hdr_row)

        sum_line_amount = sum(
            _number(row.get("AMOUNT"), 0)
            for row in generated_lines
        )
        sum_line_net = sum(
            _number(row.get("NETAMOUNT"), 0)
            for row in generated_lines
        )
        sum_line_discount = sum(
            _number(row.get("DISCOUNT"), 0)
            for row in generated_lines
        )
        sum_line_c_amount = sum(
            _number(row.get("C_AMOUNT"), 0)
            for row in generated_lines
        )
        sum_line_c_net = sum(
            _number(row.get("C_NETAMOUNT"), 0)
            for row in generated_lines
        )
        sum_line_c_discount = sum(
            _number(row.get("C_DISCOUNT"), 0)
            for row in generated_lines
        )

        line_payment_detail = " | ".join(
            (
                f"PAY_DOC_NO_DERIVED={int(item['source']['DOC_NO'])}:"
                f"PAY_USER_DOC_INPUT={_user_doc(item['source'].get('PAY_USER_DOC'))},"
                f"PAY_USER_DOC_ARAP={_user_doc(item['source'].get('USER_DOC'))},"
                f"C_PAID_AMOUNT_INPUT={item['LINE_C_NETAMOUNT']},"
                f"LINE_ACCOUNT_AMOUNT_CALCULATED={item['LINE_ACCOUNT_AMOUNT']},"
                f"ACCOUNT_CURRENCY={item['ACCOUNT_CURRENCY']},"
                f"BASE={item['LINE_BASE_CURRENCY']},"
                f"INVOICE_CURRENCY={item['INVOICE_CURRENCY']},"
                f"LINE_FACTOR={item['LINE_FACTOR']},"
                f"FACTOR_SOURCE={item['LINE_FACTOR_SOURCE']},"
                f"MODE={item['CONVERSION_MODE']},"
                f"LINE_C_NETAMOUNT={item['LINE_C_NETAMOUNT']},"
                f"LINE_NETAMOUNT={item['LINE_NETAMOUNT']},"
                f"ARAP_C_PAID_TOTAL:"
                f"{item['EXISTING_ARAP_C_PAID_TOTAL']}"
                f"->{item['NEW_ARAP_C_PAID_TOTAL']},"
                f"ARAP_PAID_TOTAL:"
                f"{item['EXISTING_ARAP_PAID_TOTAL']}"
                f"->{item['NEW_ARAP_PAID_TOTAL']}"
            )
            for item in calculated
        )

        expected_account_factor = effective_account_factor
        expected_currency_factor = effective_currency_factor

        amount_chain_checks = {
            "HDR_ACCOUNT_AMOUNT_VS_SUM_LINE_ACCOUNT_AMOUNT": abs(
                _number(hdr_row.get("ACCOUNT_AMOUNT"), 0)
                - sum(
                    _number(item.get("LINE_ACCOUNT_AMOUNT"), 0)
                    for item in calculated
                )
            ) <= AMOUNT_TOLERANCE,
            "HDR_APPLIED_VS_SUM_LINE_AMOUNT": abs(
                _number(hdr_row.get("APPLIED"), 0)
                - sum_line_amount
            ) <= AMOUNT_TOLERANCE,
            "HDR_AMOUNT_VS_SUM_LINE_NETAMOUNT": abs(
                _number(hdr_row.get("AMOUNT"), 0)
                - sum_line_net
            ) <= AMOUNT_TOLERANCE,
            "HDR_DISCOUNT_VS_SUM_LINE_DISCOUNT": abs(
                _number(hdr_row.get("DISCOUNT"), 0)
                - sum_line_discount
            ) <= AMOUNT_TOLERANCE,
            "HDR_C_APPLIED_VS_SUM_LINE_C_AMOUNT": abs(
                _number(hdr_row.get("C_APPLIED"), 0)
                - sum_line_c_amount
            ) <= AMOUNT_TOLERANCE,
            "HDR_C_AMOUNT_VS_SUM_LINE_C_NETAMOUNT": abs(
                _number(hdr_row.get("C_AMOUNT"), 0)
                - sum_line_c_net
            ) <= AMOUNT_TOLERANCE,
            "HDR_C_DISCOUNT_VS_SUM_LINE_C_DISCOUNT": abs(
                _number(hdr_row.get("C_DISCOUNT"), 0)
                - sum_line_c_discount
            ) <= AMOUNT_TOLERANCE,
            "HDR_NETAMOUNT_VS_AMOUNT": abs(
                _number(hdr_row.get("NETAMOUNT"), 0)
                - _number(hdr_row.get("AMOUNT"), 0)
            ) <= AMOUNT_TOLERANCE,
            "HDR_C_NETAMOUNT_VS_C_AMOUNT": abs(
                _number(hdr_row.get("C_NETAMOUNT"), 0)
                - _number(hdr_row.get("C_AMOUNT"), 0)
            ) <= AMOUNT_TOLERANCE,
            "HDR_ACCOUNT_FACTOR_EFFECTIVE": (
                True
                if expected_account_factor is None
                else abs(
                    _number(hdr_row.get("ACCOUNT_FACTOR"), 0)
                    - expected_account_factor
                ) <= 1e-6
            ),
            "HDR_CURENCY_FACTOR_EFFECTIVE": (
                True
                if expected_currency_factor is None
                else abs(
                    _number(hdr_row.get("CURENCY_FACTOR"), 0)
                    - expected_currency_factor
                ) <= 1e-6
            ),
        }

        failed_amount_checks = [
            name
            for name, passed in amount_chain_checks.items()
            if not passed
        ]
        if failed_amount_checks:
            errors.append(
                "AMOUNT_CHAIN_MISMATCH:"
                + ",".join(failed_amount_checks)
            )

        required_hdr = [
            "DOC_NO",
            "DOC_TYPE",
            "DOC_CATEGORY",
            "CHECK_NO",
            "DOC_STATUS",
            "CHECK_COUNTER",
            "BANK_ID",
            "ACCOUNT_NO",
            "ACCTNO",
            "SUBC",
            "CHECK_DATE",
            "PAYEE",
            "GL_CODE",
            "COMPANY",
            "CURENCY_BASE",
            "CURENCY_CONV",
            "CURENCY_FACTOR",
            "AMOUNT",
            "APPLIED",
            "C_AMOUNT",
            "C_APPLIED",
            "ACCOUNT_CURRENCY",
            "ACCOUNT_AMOUNT",
            "ACCOUNT_FACTOR",
            "ADDED_USR",
            "ADDED_DTE",
        ]
        missing_hdr = [
            field
            for field in required_hdr
            if hdr_row.get(field) is None
            or _text(hdr_row.get(field)) == ""
        ]
        if missing_hdr:
            errors.append(
                "MISSING_HDR_FIELDS:" + ",".join(missing_hdr)
            )

        unique_errors = list(dict.fromkeys(errors))
        reference_messages: list[str] = []
        out_of_scope_messages: list[str] = []
        core_logic_messages: list[str] = []

        for message in unique_errors:
            if message.startswith((
                "CUSTOMER_LOOKUP_NO_MATCH:",
                "CUSTOMER_LOOKUP_NOT_UNIQUE:",
            )):
                reference_messages.append(message)
                continue

            if message.startswith(
                "OUT_OF_SCOPE_ZERO_PAYMENT_RECEIPT:"
            ):
                out_of_scope_messages.append(message)
                continue

            if message.startswith("MISSING_HDR_FIELDS:"):
                missing_fields = {
                    field.strip()
                    for field in message.split(":", 1)[1].split(",")
                    if field.strip()
                }
                reference_fields = missing_fields & {"PAYEE"}
                zero_factor_fields = (
                    missing_fields
                    & {"ACCOUNT_FACTOR", "CURENCY_FACTOR"}
                    if out_of_scope_messages
                    else set()
                )
                remaining_fields = (
                    missing_fields
                    - reference_fields
                    - zero_factor_fields
                )
                if reference_fields:
                    reference_messages.append(
                        "MISSING_HDR_FIELDS:"
                        + ",".join(sorted(reference_fields))
                    )
                if zero_factor_fields:
                    out_of_scope_messages.append(
                        "MISSING_HDR_FIELDS:"
                        + ",".join(sorted(zero_factor_fields))
                    )
                if remaining_fields:
                    core_logic_messages.append(
                        "MISSING_HDR_FIELDS:"
                        + ",".join(sorted(remaining_fields))
                    )
                continue

            core_logic_messages.append(message)

        logic_status = (
            "FAIL"
            if core_logic_messages
            else (
                "OUT_OF_SCOPE"
                if out_of_scope_messages
                else "PASS"
            )
        )
        reference_status = (
            "REVIEW" if reference_messages else "PASS"
        )

        validation_rows.append(
            {
                "INPUT_HDR_GROUP_ID": input_hdr_group_id,
                "DOC_NO": doc_no,
                "CHECK_NO": check_no,
                "CHECK_COUNTER": check_counter,
                "BATCH_NO": batch_no,
                "LINE_COUNT": len(generated_lines),
                "BANK_CURRENCY_COLUMN": bank_currency_column,
                "BANK_MATCH_COUNT": len(bank_matches),
                "CUSTOMER_LOOKUP_KEYS": (
                    f"ACCTNO={acctno};"
                    f"SUBC={_text(header_subc)};"
                    "CUST_VEND=C"
                ),
                "CUSTOMER_MATCH_COUNT": len(customer_matches),
                "CUSTOMER_UNIQUE_NAME_COUNT":
                    len(unique_customer_names),
                "CUSTOMER_LOOKUP_STATUS": customer_lookup_status,
                "ACCOUNT_RATE_STATUS": account_rate_status,
                "PAYMENT_LOOKUP_FACTOR": payment_lookup_factor,
                "CURENCY_RATE_STATUS": currency_rate_status,
                "CURENCY_STATE_SOURCE": currency_state_source,
                "CURENCY_STATE_USED": hdr_currency_state,
                "ACCOUNT_FACTOR_USED": account_factor,
                "CURENCY_FACTOR_USED": currency_factor,
                "DISTINCT_LINE_FACTOR_COUNT": len(factor_values),
                "LINE_FACTORS": ",".join(
                    str(value) for value in factor_values
                ),
                "LINE_FACTOR_SOURCES": ",".join(factor_sources),
                "PAYMENT_CONVERSION_MODE": ",".join(conversion_modes),
                "LINE_PAYMENT_DETAIL": line_payment_detail,
                "INV_HDR_LINE_COUNT": sum(
                    1
                    for item in calculated
                    if item.get("INV_C_DOC_TOTAL") is not None
                ),
                "GENERATED_HDR_AMOUNT": hdr_row.get("AMOUNT"),
                "SUM_LINE_AMOUNT": sum_line_amount,
                "HDR_APPLIED": hdr_row.get("APPLIED"),
                "APPLIED_DIFF": _diff(
                    hdr_row.get("APPLIED"),
                    sum_line_amount,
                ),
                "SUM_LINE_NETAMOUNT": sum_line_net,
                "HDR_AMOUNT": hdr_row.get("AMOUNT"),
                "LINE_NET_TO_HDR_AMOUNT_DIFF": _diff(
                    hdr_row.get("AMOUNT"),
                    sum_line_net,
                ),
                "SUM_LINE_DISCOUNT": sum_line_discount,
                "HDR_DISCOUNT": hdr_row.get("DISCOUNT"),
                "DISCOUNT_DIFF": _diff(
                    hdr_row.get("DISCOUNT"),
                    sum_line_discount,
                ),
                "SUM_LINE_C_AMOUNT": sum_line_c_amount,
                "HDR_C_APPLIED": hdr_row.get("C_APPLIED"),
                "C_APPLIED_DIFF": _diff(
                    hdr_row.get("C_APPLIED"),
                    sum_line_c_amount,
                ),
                "SUM_LINE_C_NETAMOUNT": sum_line_c_net,
                "HDR_C_AMOUNT": hdr_row.get("C_AMOUNT"),
                "LINE_C_NET_TO_HDR_C_AMOUNT_DIFF": _diff(
                    hdr_row.get("C_AMOUNT"),
                    sum_line_c_net,
                ),
                "SUM_LINE_C_DISCOUNT": sum_line_c_discount,
                "HDR_C_DISCOUNT": hdr_row.get("C_DISCOUNT"),
                "C_DISCOUNT_DIFF": _diff(
                    hdr_row.get("C_DISCOUNT"),
                    sum_line_c_discount,
                ),
                "AMOUNT_CHAIN_DETAIL": (
                    f"HDR_AMOUNT={_number(hdr_row.get('AMOUNT'), 0)};"
                    f"SUM_LINE_NETAMOUNT={sum_line_net};"
                    f"HDR_C_AMOUNT={_number(hdr_row.get('C_AMOUNT'), 0)};"
                    f"SUM_LINE_C_NETAMOUNT={sum_line_c_net};"
                    f"ACCOUNT_FACTOR={account_factor};"
                    f"CURENCY_FACTOR={currency_factor}"
                ),
                "LOGIC_STATUS": logic_status,
                "REFERENCE_STATUS": reference_status,
                "CORE_LOGIC_ERRORS":
                    " | ".join(core_logic_messages),
                "REFERENCE_WARNINGS":
                    " | ".join(reference_messages),
                "OUT_OF_SCOPE_REASON":
                    " | ".join(out_of_scope_messages),
                "READY_TO_INSERT":
                    "Y" if not unique_errors else "N",
                "ERRORS": " | ".join(unique_errors),
            }
        )

    _write_plain_sheets(
        output_path,
        {
            "Validation": pd.DataFrame(validation_rows),
            "CHECK_HDR": pd.DataFrame(hdr_rows, columns=HDR_SCHEMA),
            "CHECK_LINE": pd.DataFrame(line_rows, columns=LINE_SCHEMA),
            "ARAP_UPDATE": pd.DataFrame(arap_rows, columns=ARAP_SCHEMA),
            "COUNTER_UPDATE": allocator.output(),
        },
    )
