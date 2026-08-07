from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

from .config import COUNTER_ROLE_MAP
from .common import (
    _currency,
    _currency_db_aliases,
    _normalize_columns,
    _query_in,
    _read_sql_query,
    _text,
    _user_doc,
)

def _load_env_file(path: str | Path) -> None:
    """Load a simple KEY=VALUE .env file without adding another dependency."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if key:
            # Existing operating-system variables take priority over the file.
            os.environ.setdefault(key, value)


def _odbc_value(value: str) -> str:
    """Safely quote a SQL Server ODBC connection-string value."""
    return "{" + str(value).replace("}", "}}") + "}"


def _connection_string_from_env(env_file: str | Path = ".env") -> str:
    """
    Load the SQL Server connection string from .env.

    Preferred:
        DATABASE_CONNECTION_STRING=DRIVER={SQL Server};SERVER=...;DATABASE=...;UID=...;PWD=...

    Fallback:
        Build it from separate P2000_DB_* variables.
    """
    _load_env_file(env_file)

    # Prefer the exact full connection string already known to work locally.
    full_connection_string = (
        os.getenv("DATABASE_CONNECTION_STRING", "").strip()
        or os.getenv("P2000_DATABASE_CONNECTION_STRING", "").strip()
    )
    if full_connection_string:
        return full_connection_string.rstrip(";") + ";"

    driver = os.getenv("P2000_DB_DRIVER", "SQL Server").strip()
    server = os.getenv("P2000_DB_SERVER", "").strip()
    database = os.getenv("P2000_DB_DATABASE", "").strip()
    username = os.getenv("P2000_DB_USER", "").strip()
    password = os.getenv("P2000_DB_PASSWORD", "")
    timeout = os.getenv("P2000_DB_TIMEOUT", "30").strip()

    missing = [
        name
        for name, value in {
            "P2000_DB_SERVER": server,
            "P2000_DB_DATABASE": database,
            "P2000_DB_USER": username,
            "P2000_DB_PASSWORD": password,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing database settings: " + ", ".join(missing)
            + f". Add DATABASE_CONNECTION_STRING or the separate P2000_DB_* values to {env_file}."
        )

    return ";".join(
        [
            f"DRIVER={_odbc_value(driver)}",
            f"SERVER={_odbc_value(server)}",
            f"DATABASE={_odbc_value(database)}",
            f"UID={_odbc_value(username)}",
            f"PWD={_odbc_value(password)}",
            f"Connection Timeout={timeout}",
        ]
    ) + ";"

def _prepare_counter_rows(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw

    required = {"DOC_CATEGORY", "DOC_TYPE", "COMPANYNO", "DIVISION", "COUNTER"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"COUNTERSTBL is missing columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    for role, (doc_category, doc_type) in COUNTER_ROLE_MAP.items():
        matches = raw[
            raw["DOC_CATEGORY"].map(_text).eq(doc_category)
            & raw["DOC_TYPE"].map(_text).eq(doc_type)
        ]
        for _, row in matches.iterrows():
            record = row.to_dict()
            record["ROLE"] = role
            record["INCREMENT"] = 1
            record["COUNTER_SOURCE"] = "DB"
            rows.append(record)
    return pd.DataFrame(rows)


def _load_database_lookups(
    connection: Any,
    receipt_input: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    """Load references using PAY_USER_DOC as the user-facing invoice key.

    PAY_DOC_NO is deliberately not read from the input. ACCOUNT_AR_AP is first
    located by normalized USER_DOC + ACCTNO + DOC_CATEGORY='IV' + LINE=0; its
    DOC_NO is then used to load INV_HDR and later populate CHECK_LINE.PAY_DOC_NO.
    """
    user_docs = [
        _user_doc(value)
        for value in receipt_input["PAY_USER_DOC"].tolist()
        if _user_doc(value)
    ]
    bank_ids = receipt_input["BANK_ID"].tolist()
    acctnos = receipt_input["ACCTNO"].tolist()

    arap = _query_in(
        connection,
        "dbo.ACCOUNT_AR_AP",
        "RTRIM(USER_DOC)",
        user_docs,
        extra_where=(
            "ISNULL(LINE, 0) = 0 "
            "AND RTRIM(DOC_CATEGORY) = 'IV'"
        ),
    )

    arap_doc_nos = (
        pd.to_numeric(
            arap.get("DOC_NO", pd.Series(dtype=object)),
            errors="coerce",
        )
        .dropna()
        .astype(int)
        .tolist()
    )

    inv_hdr_table = os.getenv(
        "P2000_INV_HDR_TABLE",
        "dbo.INV_HDR",
    ).strip()
    inv_hdr = _query_in(
        connection,
        inv_hdr_table,
        "DOC_NO",
        arap_doc_nos,
    )

    banks = _query_in(connection, "dbo.BANKS_ACCOUNTS", "BANK_ID", bank_ids)
    customer_table = os.getenv(
        "P2000_CUSTOMER_TABLE",
        "dbo.CUSTVEND",
    ).strip()

    customers = _query_in(
        connection,
        customer_table,
        "ACCTNO",
        acctnos,
        extra_where="RTRIM(CUST_VEND) = ?",
        extra_params=["C"],
    )

    normalized_base_currencies = {
        _currency(value)
        for value in arap.get(
            "CURENCY_BASE",
            pd.Series(dtype=object),
        )
    }
    normalized_base_currencies.discard("")
    if not normalized_base_currencies:
        normalized_base_currencies = {"USD"}

    normalized_target_currencies = {
        _currency(value)
        for value in receipt_input["ACCOUNT_CURRENCY"]
    }
    normalized_target_currencies.update(
        _currency(value)
        for value in arap.get(
            "CURENCY_CONV",
            pd.Series(dtype=object),
        )
    )
    normalized_target_currencies.discard("")

    base_currencies = sorted(
        {
            alias
            for currency in normalized_base_currencies
            for alias in _currency_db_aliases(currency)
        }
    )
    target_currencies = sorted(
        {
            alias
            for currency in normalized_target_currencies
            for alias in _currency_db_aliases(currency)
        }
    )

    if target_currencies:
        fr_placeholders = ",".join("?" for _ in base_currencies)
        to_placeholders = ",".join("?" for _ in target_currencies)
        rate_sql = (
            "SELECT * FROM dbo.TBLCONV "
            f"WHERE UPPER(RTRIM(FR_CODE)) IN ({fr_placeholders}) "
            f"AND UPPER(RTRIM(TO_CODE)) IN ({to_placeholders})"
        )
        rates = _read_sql_query(
            connection,
            rate_sql,
            list(base_currencies) + list(target_currencies),
        )
        rates = _normalize_columns(rates)
    else:
        rates = pd.DataFrame()

    counter_sql = """
        SELECT *
        FROM dbo.COUNTERSTBL
        WHERE (RTRIM(DOC_CATEGORY) = 'CHECK' AND RTRIM(DOC_TYPE) = 'D')
           OR (RTRIM(DOC_CATEGORY) = 'R2'    AND RTRIM(DOC_TYPE) IN ('D', 'L'))
           OR (RTRIM(DOC_CATEGORY) = 'ACRN'  AND RTRIM(DOC_TYPE) = 'D')
           OR (RTRIM(DOC_CATEGORY) = 'BC'    AND RTRIM(DOC_TYPE) = 'D')
    """
    counters = _prepare_counter_rows(
        _normalize_columns(_read_sql_query(connection, counter_sql))
    )

    return arap, inv_hdr, banks, customers, rates, counters
