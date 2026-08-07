from __future__ import annotations

import argparse

from .config import AMOUNT_TOLERANCE, FACTOR_TOLERANCE
from .db import _connection_string_from_env
from .generation import generate_insert_ready_excel
from .comparison import compare_generated_output_to_db
from .validation import (
    validate_existing_chain,
    validate_existing_chain_from_db,
)

def main() -> None:
    parser = argparse.ArgumentParser(description="P2000 AR Fund In Excel validator and generator")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate existing ARAP/CHECK_LINE/CHECK_HDR Excel exports")
    validate.add_argument("--arap", required=True)
    validate.add_argument("--check-line", required=True)
    validate.add_argument("--check-hdr", required=True)
    validate.add_argument("--output", required=True)

    validate_db = sub.add_parser(
        "validate-db",
        help=(
            "Query historical CHECK_HDR, CHECK_LINE, and ACCOUNT_AR_AP "
            "directly from the database and validate the confirmed "
            "amount chain."
        ),
    )
    validate_db.add_argument("--output", required=True)
    validate_db.add_argument(
        "--env-file",
        default=".env",
        help="Path to the .env file containing P2000 SQL Login settings.",
    )
    validate_db.add_argument(
        "--top",
        type=int,
        default=1000,
        help="Number of recent supported receipts to select.",
    )
    validate_db.add_argument(
        "--doc-no",
        required=False,
        help="Optional comma-separated CHECK_HDR DOC_NO values.",
    )
    validate_db.add_argument("--date-from", required=False)
    validate_db.add_argument("--date-to", required=False)
    validate_db.add_argument(
        "--currency-mode",
        choices=["all", "same", "cross"],
        default="all",
    )

    generate = sub.add_parser(
        "generate",
        help="Generate full rows from the simple one-sheet input",
    )
    generate.add_argument("--input", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument(
        "--env-file",
        default=".env",
        help="Path to the .env file containing P2000 SQL Login settings.",
    )
    generate.add_argument("--user", default="ILC")
    generate.add_argument("--process-time", required=False)

    compare_db = sub.add_parser(
        "compare-db",
        help=(
            "Compare every generated output column against current "
            "database rows."
        ),
    )
    compare_db.add_argument("--generated", required=True)
    compare_db.add_argument("--output", required=True)
    compare_db.add_argument(
        "--env-file",
        default=".env",
        help="Path to the .env file containing P2000 SQL Login settings.",
    )
    compare_db.add_argument(
        "--amount-tolerance",
        type=float,
        default=AMOUNT_TOLERANCE,
    )
    compare_db.add_argument(
        "--numeric-tolerance",
        type=float,
        default=1e-8,
    )
    compare_db.add_argument(
        "--factor-tolerance",
        type=float,
        default=FACTOR_TOLERANCE,
    )
    compare_db.add_argument(
        "--date-tolerance-seconds",
        type=float,
        default=120.0,
    )

    args = parser.parse_args()
    if args.command == "validate":
        validate_existing_chain(
            args.arap,
            args.check_line,
            args.check_hdr,
            args.output,
        )
    elif args.command == "validate-db":
        connection_string = _connection_string_from_env(
            args.env_file
        )
        validate_existing_chain_from_db(
            output_path=args.output,
            connection_string=connection_string,
            top=args.top,
            doc_no=args.doc_no,
            date_from=args.date_from,
            date_to=args.date_to,
            currency_mode=args.currency_mode,
        )
    elif args.command == "generate":
        connection_string = _connection_string_from_env(args.env_file)
        generate_insert_ready_excel(
            args.input,
            args.output,
            connection_string,
            user=args.user,
            process_time=args.process_time,
        )
    else:
        connection_string = _connection_string_from_env(args.env_file)
        compare_generated_output_to_db(
            generated_path=args.generated,
            output_path=args.output,
            connection_string=connection_string,
            amount_tolerance=args.amount_tolerance,
            numeric_tolerance=args.numeric_tolerance,
            factor_tolerance=args.factor_tolerance,
            date_tolerance_seconds=args.date_tolerance_seconds,
        )
