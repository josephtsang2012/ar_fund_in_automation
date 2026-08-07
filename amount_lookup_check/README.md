# P2000 AR Fund In Logic Prototype

## Overview

This repository contains the modular, read-only handover version of the P2000 AR Fund In logic prototype. It reconstructs and validates the standard `AR / R2 / IV` workflow, performs live database lookups, simulates counters, and generates proposed rows in Excel.

The current code can:

- read the canonical Excel input;
- derive invoice and reference values from P2000;
- generate proposed `CHECK_HDR`, `CHECK_LINE`, `ACCOUNT_AR_AP`, and counter changes;
- validate historical amount, factor, and relationship logic;
- compare generated rows with existing database rows when the generated keys already exist.

It does **not** perform database `INSERT` or `UPDATE` operations. Transaction handling, commit/rollback, GL posting, email, and file movement are intentionally left for the next development phase.

The original single-file v22 prototype is retained unchanged under `legacy_v22/` for regression and traceability. The active code has neutral filenames so future work does not inherit a version number in every path. The modular implementation preserves the supplied v22 logic and scope.

## Supported scope

```text
CHECK_HDR.DOC_TYPE          = AR
CHECK_HDR.DOC_CATEGORY      = R2
CHECK_LINE.PAY_DOC_CATEGORY = IV
ACCOUNT_AR_AP.LINE          = 0
CUSTVEND.CUST_VEND          = C
```

Current exceptions: `AD`, `MS`, advanced payment, reversal-only, zero-payment, and non-`C` customer scenarios. These cases are EXCLUDED.

## Project structure

```text
.
├── run_p2000_ar_fund_in.py                     # Convenience launcher
├── p2000_ar_fund_in/
│   ├── __main__.py                             # Supports: python -m p2000_ar_fund_in
│   ├── cli.py                                  # Command routing and arguments
│   ├── config.py                               # Schemas, defaults, counter roles, tolerances
│   ├── common.py                               # Shared normalization, dataframe, SQL, and Excel helpers
│   ├── db.py                                   # Environment settings and read-only reference lookups
│   ├── generation.py                           # Counter simulation and proposed-row generation
│   ├── validation.py                           # Export validation and historical DB validation
│   └── comparison.py                           # Generated-output versus DB comparison
├── legacy_v22/
│   ├── p2000_ar_fund_in_pipeline_v22_reference.py
│   └── README.md
├── clean_historical_input.sql
├── requirements.txt
├── requirements-dev.txt
├── .env
└── README.md
```

### Module boundaries

| Module | Responsibility |
|---|---|
| `cli.py` | Parse CLI arguments and invoke one workflow. |
| `config.py` | Centralize schemas, defaults, tolerances, and counter mappings. |
| `common.py` | Hold shared pure helpers and workbook/SQL utility functions. |
| `db.py` | Build the SQL Server connection string and load read-only references. |
| `generation.py` | Apply the retained business logic and produce the proposed write plan. |
| `validation.py` | Recalculate historical expected values and classify results. |
| `comparison.py` | Compare generated workbook values with existing DB rows by exact keys. |

Future testing-database write logic should be added as a separate writer/service module. It should consume the generated plan instead of reimplementing the amount formulas.


## Requirements

### Runtime

- Python 3.10 or later
- Microsoft SQL Server ODBC driver installed on the machine
- Network access and read permission for the relevant P2000 database tables
- Python packages listed in `requirements.txt`:

```text
pandas
openpyxl
pyodbc
```

`pyodbc` is a Python package only. The machine must also have a compatible SQL Server ODBC driver. The driver name configured in `.env` must match the installed driver name.

### Installation

Windows PowerShell example:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r .\requirements.txt
```

### Database environment

Preferred complete-string form for `.env`:

```text
DATABASE_CONNECTION_STRING=DRIVER={ODBC Driver 17 for SQL Server};SERVER=...;DATABASE=...;UID=...;PWD=...;
```

Alternative separate fields:

```text
P2000_DB_DRIVER=ODBC Driver 17 for SQL Server
P2000_DB_SERVER=...
P2000_DB_DATABASE=...
P2000_DB_USER=...
P2000_DB_PASSWORD=...
P2000_DB_TIMEOUT=30
```

Optional table overrides:

```text
P2000_INV_HDR_TABLE=dbo.INV_HDR
P2000_CUSTOMER_TABLE=dbo.CUSTVEND
```

Do not commit the real `.env` file or database credentials.

## Quick start

Show available commands:

```powershell
python -m p2000_ar_fund_in --help
```

The convenience launcher is equivalent:

```powershell
python .\run_p2000_ar_fund_in.py --help
```

## Canonical input format

The first Excel sheet must contain:

| Column | Required | Meaning |
|---|---:|---|
| `INPUT_HDR_GROUP_ID` | Yes | Input-only grouping identifier. Rows with the same value form one receipt. Historical clean-input SQL derives this label from the source `CHECK_HDR.DOC_NO`, but the label is not inserted into P2000. |
| `BANK_ID` | Yes | Receiving bank identifier. |
| `ACCOUNT_CURRENCY` | Yes | Currency actually received from the customer. |
| `C_PAID_AMOUNT` | Yes | Current receipt amount applied to this invoice in invoice converted currency. This is a delta, not a cumulative total. The Excel value is treated as the current receipt delta. It is added to the existing cumulative `ACCOUNT_AR_AP.C_PAID_TOTAL` in the proposed ARAP update. |
| `CHECK_DATE` | Yes | Receipt/check date. |
| `PAY_USER_DOC` | Yes | User-entered invoice user document number. Treated as text. |
| `ACCTNO` | Yes | Customer account number. |
| `GL_CODE_DISC` | Yes | Discount GL code. Blank values default to `750201-00`. User may override to `790550-00` later. |
| `NOTE` | No | Optional Header note. NULL and empty values are allowed. |

### Why `INPUT_HDR_GROUP_ID` is not called `DOC_NO`

`INPUT_HDR_GROUP_ID` only groups Excel rows before a new receipt exists. It is **not a P2000 database column** and is never inserted into `CHECK_HDR`.

The real `CHECK_HDR.DOC_NO` is allocated later from `COUNTERSTBL` using the `CHECK / D` counter.

For historical test data, `clean_historical_input.sql` outputs values such as:

```text
HIST_HDR_318549
```

This preserves the source Header reference while making it visually clear that the value is not the new generated `CHECK_HDR.DOC_NO`.

---

## Invoice lookup and `PAY_USER_DOC`

### User input

The user supplies:

```text
PAY_USER_DOC
ACCTNO
```

The user does **NOT** supply `PAY_DOC_NO`.

### Normalization

`PAY_USER_DOC` is a text identifier. The code:

1. removes leading and trailing padding;
2. removes only an Excel-style numeric suffix `.0`;
3. preserves existing leading zeroes;
4. left-pads purely numeric values to eight characters;
5. performs an exact normalized match.

Examples:

```text
"08482793"
"08482793 "
"08482793       "
8482793
```

normalize to:

```text
08482793
```

This is normalization, not fuzzy matching. Similarity matching, substring matching, and deriving `PAY_USER_DOC` from `PAY_DOC_NO` are not allowed because they could select the wrong invoice.

### ARAP lookup key

The code locates one `ACCOUNT_AR_AP` row using:

```text
normalized ACCOUNT_AR_AP.USER_DOC = normalized input PAY_USER_DOC
ACCOUNT_AR_AP.ACCTNO              = input ACCTNO
ACCOUNT_AR_AP.DOC_CATEGORY        = IV
ACCOUNT_AR_AP.LINE                = 0
```

Expected result:

```text
0 matches  -> MISSING_ARAP
1 match    -> safe derivation
More than 1 matches -> stop; PAY_DOC_NO cannot be derived safely
```

After the unique match:

```text
CHECK_LINE.PAY_DOC_NO   = ACCOUNT_AR_AP.DOC_NO
CHECK_LINE.PAY_USER_DOC = ACCOUNT_AR_AP.USER_DOC
```

`PAY_DOC_NO` is therefore a derived database value, not an input field.

---

## Amount logic

### Current payment input

`C_PAID_AMOUNT` means the amount applied by the current receipt to one invoice.

It maps directly to:

```text
CHECK_LINE.C_NETAMOUNT = input C_PAID_AMOUNT
```

It must not be confused with the current database value:

```text
ACCOUNT_AR_AP.C_PAID_TOTAL
```

`C_PAID_TOTAL` is cumulative and may already contain earlier receipts after our check.

### Per-line calculation

For every matched invoice:

```text
LINE.C_AMOUNT = INV_HDR.C_DOC_TOTAL
LINE.AMOUNT   = LINE.C_AMOUNT / LINE_FACTOR

LINE.C_NETAMOUNT = input C_PAID_AMOUNT
LINE.NETAMOUNT   = LINE.C_NETAMOUNT / LINE_FACTOR

LINE.C_DISCOUNT = LINE.C_AMOUNT - LINE.C_NETAMOUNT
LINE.DISCOUNT   = LINE.AMOUNT - LINE.NETAMOUNT
```

The current payment amount in `ACCOUNT_CURRENCY` is calculated as:

```text
LINE_ACCOUNT_AMOUNT = LINE.NETAMOUNT * ACCOUNT_FACTOR
```

### ARAP cumulative update

The prototype assumes the input is the current receipt delta and P2000 stores cumulative paid totals:

```text
new ACCOUNT_AR_AP.C_PAID_TOTAL
= existing ACCOUNT_AR_AP.C_PAID_TOTAL
+ CHECK_LINE.C_NETAMOUNT

new ACCOUNT_AR_AP.PAID_TOTAL
= existing ACCOUNT_AR_AP.PAID_TOTAL
+ CHECK_LINE.NETAMOUNT
```

The direction of this cumulative update is confirmed. The complete historical inclusion/exclusion rules for every transaction type remain unresolved; see **Remaining unresolved business rules**.

### Header aggregation

For one `INPUT_HDR_GROUP_ID`:

```text
CHECK_HDR.APPLIED      = SUM(CHECK_LINE.AMOUNT)
CHECK_HDR.AMOUNT       = SUM(CHECK_LINE.NETAMOUNT)
CHECK_HDR.NETAMOUNT    = CHECK_HDR.AMOUNT
CHECK_HDR.DISCOUNT     = SUM(CHECK_LINE.DISCOUNT)

CHECK_HDR.C_APPLIED    = SUM(CHECK_LINE.C_AMOUNT)
CHECK_HDR.C_AMOUNT     = SUM(CHECK_LINE.C_NETAMOUNT)
CHECK_HDR.C_NETAMOUNT  = CHECK_HDR.C_AMOUNT
CHECK_HDR.C_DISCOUNT   = SUM(CHECK_LINE.C_DISCOUNT)

CHECK_HDR.ACCOUNT_AMOUNT
= SUM(calculated line payment amounts in ACCOUNT_CURRENCY)
```

---

## Currency roles and factors

`CHECK_HDR` has enough columns for three currency roles:

| Field | Role |
|---|---|
| `CURENCY_BASE` | Base/accounting currency, historically often USD. |
| `CURENCY_CONV` | Invoice converted currency. |
| `ACCOUNT_CURRENCY` | Currency actually received from the customer. |

The corresponding factors are:

| Field | Role |
|---|---|
| `CURENCY_FACTOR` | Base currency to invoice converted currency. |
| `ACCOUNT_FACTOR` | Base currency to actual payment currency. |

### Line factor source

Each invoice line resolves its own factor in this order:

```text
1. same CURENCY_BASE and CURENCY_CONV -> 1
2. INV_HDR.CURENCY_FACTOR
3. ACCOUNT_AR_AP.CURENCY_FACTOR
4. latest matching TBLCONV row as fallback
```

Different invoices in one receipt may legitimately use different historical factors, for example:

```text
USD -> CNY: 6.59
USD -> CNY: 6.76
```

This is not an error.

### Header `CURENCY_FACTOR`

Historical receipt `318549` confirmed that no single line factor had to equal the Header factor. The Header effective relationship was:

```text
CHECK_HDR.CURENCY_FACTOR
≈ CHECK_HDR.C_AMOUNT / CHECK_HDR.AMOUNT
```

therefore the prototype uses:

```text
one distinct line factor  -> common line factor
multiple line factors     -> effective C_AMOUNT / AMOUNT
```

The ratio is both a generated relationship and a historical validation check. Small residuals must be checked with `ABS(...)`, not a one-sided `>` or `<` comparison.

### Header `ACCOUNT_FACTOR`

`ACCOUNT_FACTOR` is sourced from the base-to-payment-currency `TBLCONV` lookup.

The relationship:

```text
ACCOUNT_FACTOR
≈ ACCOUNT_AMOUNT / AMOUNT
```

is retained as validation rather than treated as the primary lookup source.

### `CURENCY_STATE`

The exact business meaning of `CURENCY_STATE = 0/1` is not confirmed. It is not equivalent to “same currency” as we checked.

Current provisional rule:

```text
any matched INV_HDR/ARAP state is 1 -> Header state 1
all available states are 0          -> Header state 0
all states missing                  -> fallback 1
```

This is implemented as the maximum available state and should be confirmed before production writes.

---

## Confirmed defaults and current scope decisions

```text
CHECK_LINE.GL_CODE_AMT = 110101-00
GL_CODE_DISC default   = 750201-00
CUSTVEND.CUST_VEND     = C
```

`NOTE` is optional. The generated Header keeps it NULL when the column is absent or blank. When multiple input rows share one `INPUT_HDR_GROUP_ID`, they must not provide conflicting nonblank notes.

Accounts that exist only under `CUST_VEND = B` or `V` are treated as unsupported exceptions.

---

## Table relationships

```text
CHECK_HDR.DOC_NO + CHECK_HDR.DOC_CATEGORY
    -> CHECK_LINE.DOC_NO + CHECK_LINE.DOC_CATEGORY

input PAY_USER_DOC + input ACCTNO
    -> ACCOUNT_AR_AP.USER_DOC + ACCOUNT_AR_AP.ACCTNO
       where DOC_CATEGORY='IV' and LINE=0

ACCOUNT_AR_AP.DOC_NO
    -> INV_HDR.DOC_NO
```

The generated `CHECK_LINE` relationship fields are populated from the matched ARAP row:

```text
PAY_DOC_NO
PAY_USER_DOC
PAY_DOC_CATEGORY
ACCTNO
SUBC
COMPANYNO
DIVISION
DEPART
```

`INV_LINE` is not required by the current workflow.

---

## Counter allocation

The prototype treats `COUNTERSTBL.COUNTER` as the last-used value and allocates `COUNTER + 1`.

| Role | Counter key | Allocation |
|---|---|---|
| Header `DOC_NO` | `CHECK / D` | once per receipt |
| Header `CHECK_NO` | `R2 / D` | once per receipt |
| Header `CHECK_COUNTER` | `ACRN / D` | once per receipt |
| Batch `BATCH_NO` | `BC / D` | once per upload/company/division |
| Line `LINE` | `R2 / L` | once per generated line |

The optional `MockCounters` sheet may be used for deterministic tests.

---

## Historical database validation

### What the validation does

The historical validator performs a **database self-consistency check**:

```text
actual values stored in the historical database
VS.
expected values recalculated from related historical database rows
```

It does **NOT** represent a generated-row-versus-post-insert comparison. The code does not write to the production database, so NO newly generated `CHECK_HDR`, `CHECK_LINE`, or `ACCOUNT_AR_AP` rows have been inserted and compared back field by field.

### CHECK_LINE comparisons

For each historical `CHECK_LINE`, the validator checks the following stored values and relationships:

| Actual historical DB value or relationship | Expected / comparison rule |
|---|---|
| `CHECK_LINE.C_DISCOUNT` | `CHECK_LINE.C_AMOUNT - CHECK_LINE.C_NETAMOUNT` |
| `CHECK_LINE.DISCOUNT` | `CHECK_LINE.AMOUNT - CHECK_LINE.NETAMOUNT` |
| `CHECK_LINE.C_AMOUNT / CHECK_LINE.AMOUNT` | `CHECK_LINE.C_NETAMOUNT / CHECK_LINE.NETAMOUNT` within `0.000001` |
| Header relationship | Exactly one `CHECK_HDR` for `DOC_NO + DOC_CATEGORY` |
| ARAP relationship | Exactly one `ACCOUNT_AR_AP` for `PAY_DOC_NO + normalized PAY_USER_DOC + PAY_DOC_CATEGORY + ACCTNO`, with `LINE = 0` |

Nonzero discrepancy/original fields are reported as historical warnings. They do not fail the confirmed clean IV line formulas.

Latest result:

```text
CHECK_LINE_CORE
TOTAL:    3,841
MATCH:    3,841
MISMATCH: 0
```

### CHECK_HDR comparisons

For every historical `CHECK_HDR`, the validator groups **all** related `CHECK_LINE` rows by `DOC_NO + DOC_CATEGORY`, recalculates the expected receipt totals, and compares them with the stored Header values:

| Actual historical `CHECK_HDR` value | Expected from historical `CHECK_LINE` rows |
|---|---|
| `APPLIED` | `SUM(CHECK_LINE.AMOUNT)` |
| `AMOUNT` | `SUM(CHECK_LINE.NETAMOUNT)` |
| `NETAMOUNT` | `CHECK_HDR.AMOUNT` |
| `DISCOUNT` | `SUM(CHECK_LINE.DISCOUNT)` |
| `C_APPLIED` | `SUM(CHECK_LINE.C_AMOUNT)` |
| `C_AMOUNT` | `SUM(CHECK_LINE.C_NETAMOUNT)` |
| `C_NETAMOUNT` | `CHECK_HDR.C_AMOUNT` |
| `C_DISCOUNT` | `SUM(CHECK_LINE.C_DISCOUNT)` |
| `ACCOUNT_FACTOR` | `ACCOUNT_AMOUNT / AMOUNT` within `0.000001` |
| `CURENCY_FACTOR` | `C_AMOUNT / AMOUNT` within `0.000001` |

Latest result:

```text
CHECK_HDR_CORE
TOTAL:                          1,000
EXACT MATCH:                      876
MATCH WITHIN FACTOR PRECISION:     75
OUT OF CURRENT IV SCOPE:           49
UNEXPLAINED MISMATCH:               0
```

Therefore, all 951 in-scope Header rows either match exactly or match within the confirmed currency-factor precision tolerance.

### CHECK_HDR exception archive

The historical Header differences were classified into two explained groups.

#### Non-IV receipt lines

Some receipts contain `PAY_DOC_CATEGORY = AD` and/or `MS` in addition to `IV`. An IV-only sum omits these lines while the stored Header includes them. These receipts are classified as:

```text
OUT_OF_SCOPE_NON_IV_LINES:AD
OUT_OF_SCOPE_NON_IV_LINES:MS
```

They are scope differences, not failures of the standard AR/R2/IV logic.

Earlier detailed inspection found examples with:

```text
C_APPLIED / C_AMOUNT differences caused by omitted MS rows
```

and:

```text
APPLIED / AMOUNT / C_APPLIED / C_AMOUNT differences caused by omitted AD and/or MS rows
```

The latest historical-validation run classified 49 receipts as outside the current IV-only automation scope.

#### Currency-factor precision

The remaining in-scope Header differences affected only:

```text
APPLIED_EQ_SUM_LINE_AMOUNT
AMOUNT_EQ_SUM_LINE_NETAMOUNT
```

while converted-side totals matched. The Header factor identities produced no material residual above `0.000001` when checked with absolute differences:

```sql
ABS(H.APPLIED - H.C_APPLIED / NULLIF(H.CURENCY_FACTOR, 0))
ABS(H.AMOUNT  - H.C_AMOUNT  / NULLIF(H.CURENCY_FACTOR, 0))
```

These 75 receipts are classified as:

```text
MATCH_WITHIN_FACTOR_PRECISION
```

Historical precision checks must use `ABS(...)`. A one-sided condition such as `actual - expected > tolerance` can miss an equally small negative residual.

### ARAP cumulative diagnostic comparisons

For each matched invoice-level `ACCOUNT_AR_AP` row, the validator sums the available historical payment lines with the same composite invoice key and compares:

| Actual historical ARAP value | Expected from available historical `CHECK_LINE` rows |
|---|---|
| `ACCOUNT_AR_AP.PAID_TOTAL` | `SUM(CHECK_LINE.NETAMOUNT)` |
| `ACCOUNT_AR_AP.C_PAID_TOTAL` | `SUM(CHECK_LINE.C_NETAMOUNT)` |

Latest result:

```text
ARAP_CUMULATIVE_DIAGNOSTIC
GATING:                   NO
TOTAL:                 3,730
EXACT MATCH:           3,343
MATCH WITHIN ROUNDING:     6
REVIEW:                  381
MISMATCH:                  0
```

The six rounding-only cases have converted residuals approximately equal to the base residual multiplied by the currency factor. They are classified using:

```text
base tolerance = 0.05
converted tolerance = max(0.05, ABS(currency factor) * 0.05)
```

The remaining 381 rows are retained as `REVIEW`, not as confirmed failures, because the complete historical inclusion/exclusion rules for voids, reversals, adjustments, negative applications, and other payment categories are not yet known.

### What has not been compared with the database

The current validation does not prove the final persisted values of:

```text
new COUNTERSTBL values
production timestamps and user fields
all optional/default database columns
transaction commit and rollback behavior
post-insert ARAP cumulative totals
```

These items require a controlled write test or sandbox database environment.

---

## Error and warning explanations

### `CUSTOMER_LOOKUP_NO_MATCH ... | MISSING_HDR_FIELDS:PAYEE`

Example:

```text
CUSTOMER_LOOKUP_NO_MATCH:
ACCTNO=AAR SUPPLY,
SUBC=US,
MATCH_COUNT=0,
UNIQUE_NAMES=[]
|
MISSING_HDR_FIELDS:PAYEE
```

This is one root cause producing two messages:

1. no `CUSTVEND` row exists for `CUST_VEND='C' + ACCTNO + SUBC`;
2. `PAYEE` cannot be populated, so the required Header field is missing.

The account may exist only under `B` or `V`, which is outside the current scope.

### `MISSING_ARAP`

No unique ARAP row could be matched from normalized:

```text
PAY_USER_DOC + ACCTNO + IV + LINE=0
```

`PAY_DOC_NO` therefore cannot be derived safely.

### Duplicate ARAP lookup key

More than one ARAP row matches the same normalized user-facing key. The code stops rather than selecting one arbitrarily.

### `NONZERO_DEFAULTS:NETAMOUNT_DISCREPANCY`

The historical line uses a discrepancy field. It is not part of the standard clean IV path.

### `NONZERO_DEFAULTS:ORIG_NETAMOUNT,ORIG_AMOUNT,C_ORIG_NETAMOUNT,C_ORIG_AMOUNT`

The historical row contains original or adjusted amount fields. These are retained as warnings and excluded from the clean historical input query.

### `MISSING_CHECK_HDR`

The exported or selected `CHECK_LINE` scope did not contain a corresponding Header. This is a coverage problem rather than proof that the line formula is wrong.

### Zero-payment receipt errors

A receipt whose effective base amount is zero may result from zero input, positive and negative lines cancelling, reversal-only activity, or a special workflow. Mixed-factor receipts with zero effective amount cannot derive one effective Header `CURENCY_FACTOR` from `C_AMOUNT / AMOUNT` and remain outside scope.

---

## Remaining unresolved business rule

The main unresolved issue is:

> Which historical transaction types contribute to or reduce `ACCOUNT_AR_AP.PAID_TOTAL` and `C_PAID_TOTAL`?

The 381 review rows should be classified by:

```text
receipt/document category
AD / MS / other non-IV transaction types
void status
reversal or negative applications
DOC_STATUS
manual adjustments
discount-related updates
later correction timestamps
```

Until this is confirmed, ARAP historical cumulative validation remains **diagnostic and non-gating**. The current delta-addition direction is retained for the simulation output.

A secondary open item is the business meaning of `CURENCY_STATE = 0/1`; the current maximum-state rule is provisional.

---

## Clean historical input SQL

Run:

```text
clean_historical_input.sql
```

The query:

- outputs only the canonical input columns;
- maps `CHECK_LINE.C_NETAMOUNT` directly to `C_PAID_AMOUNT`;
- does not reconstruct `ACCOUNT_AMOUNT`;
- does not output `PAY_DOC_NO` as user input;
- verifies that `PAY_USER_DOC + ACCTNO` uniquely derives the historical ARAP `DOC_NO`;
- excludes receipts with `AD` or `MS` lines;
- excludes nonstandard discrepancy/original amount fields;
- requires unique bank and `C` customer lookups;
- requires complete ARAP and `INV_HDR` references;
- requires clean line and Header amount chains.

The SQL is intended for logic testing. Re-running a historical receipt against current ARAP totals will add the receipt delta again, so generated cumulative ARAP totals should not be compared directly with the current historical database state.

In the latest handover run, the rows extracted by this clean SQL passed the generation input checks and returned `READY_TO_INSERT = Y`. This confirms the selected samples fit the current standard scope; it does not mean every historical P2000 receipt is clean or supported.

---

## Commands

### Generate proposed rows

```powershell
python -m p2000_ar_fund_in generate `
  --input ".\input.xlsx" `
  --output ".\generated_output.xlsx" `
  --env-file ".\.env" `
  --user "TEST"
```

Generated sheets:

```text
Validation
CHECK_HDR
CHECK_LINE
ARAP_UPDATE
COUNTER_UPDATE
```

### Validate historical database rows

```powershell
python -m p2000_ar_fund_in validate-db `
  --top 1000 `
  --currency-mode all `
  --output ".\historical_validation.xlsx" `
  --env-file ".\.env"
```

Optional modes:

```text
--currency-mode same
--currency-mode cross
--doc-no <CHECK_HDR.DOC_NO>
--date-from YYYY-MM-DD
--date-to YYYY-MM-DD
```

### Validate existing Excel exports

```powershell
python -m p2000_ar_fund_in validate `
  --arap ".\ACCOUNT_AR_AP.xlsx" `
  --check-line ".\CHECK_LINE.xlsx" `
  --check-hdr ".\CHECK_HDR.xlsx" `
  --output ".\export_validation.xlsx"
```

This command validates files already exported from P2000. It does not query or modify the database.

### Compare a generated workbook with existing DB rows

```powershell
python -m p2000_ar_fund_in compare-db `
  --generated ".\generated_output.xlsx" `
  --output ".\generated_vs_db.xlsx" `
  --env-file ".\.env"
```

Use `compare-db` only when the generated keys already exist in the target database, such as after a controlled future testing-environment insert. A normal read-only generation uses simulated new counters, so the corresponding rows usually do not yet exist and will return `ROW_NOT_FOUND`.

### Root launcher

Every command can also be run through:

```powershell
python .\run_p2000_ar_fund_in.py <command> ...
```

---

## Modular handover note

The retained business logic remains read-only and unchanged in scope. The main purpose of this refactor is maintainability for the next developer.

Recommended next step after Accounting confirms the remaining rules:

```text
add a separate testing-DB writer
reuse the existing generation output as the write plan
wrap CHECK_HDR / CHECK_LINE / ARAP / COUNTER operations in one transaction
read the inserted rows back and compare them with the generated plan
```

Do not duplicate the amount formulas inside the future writer.

---

## Accounting questions and next development phase

The current modular code is ready to be maintained, but Accounting confirmation is still required before a testing-database writer is implemented.

Questions to confirm:

1. How should `ACCOUNT_AR_AP.PAID_TOTAL`, `C_PAID_TOTAL`, and related amount fields behave when the existing/default values are `0`, `NULL`, or already contain prior payments?
2. Which historical transaction categories contribute to, reverse, or otherwise change the cumulative paid totals?
3. What are the exact business meanings and update rules for `AD` and `MS` payment categories?
4. Should any discrepancy, original amount, void, balance, or discount fields that currently default to zero also be updated for those scenarios?
5. What is the business meaning of `CURENCY_STATE = 0/1`?

After those rules are confirmed, the recommended implementation is:

```text
generated plan
-> testing-DB writer
-> one database transaction
-> CHECK_HDR insert
-> CHECK_LINE insert
-> ACCOUNT_AR_AP update
-> COUNTERSTBL update
-> read-back comparison
-> commit or rollback
```

The writer should reuse `generation.py` output and must not duplicate the amount calculations.

## Handover conclusion

Confirmed and implemented:

```text
input-only Header grouping identifier
PAY_USER_DOC normalization
PAY_DOC_NO derivation from ARAP
current C_PAID_AMOUNT delta mapping
CHECK_LINE amount chain
standard IV CHECK_HDR aggregation
invoice-level factors
Header effective factor handling
ACCOUNT_FACTOR lookup role
ARAP cumulative addition direction
CUST_VEND=C scope
GL_CODE_AMT=110101-00
counter allocation simulation
historical Header exception classification
```

Not confirmed or not implemented:

```text
complete ARAP historical inclusion/exclusion rules
business meaning of CURENCY_STATE 0/1
non-C customer PAYEE lookup
AD / MS automation
zero-payment and reversal-only workflows
production INSERT / UPDATE transaction
commit / rollback / notification / file movement
```

This project should therefore be described as:

> A read-only AR Fund In logic reconstruction, generation, and historical validation prototype for the standard AR/R2/IV workflow.
