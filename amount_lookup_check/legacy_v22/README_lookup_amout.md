> NOTE: Legacy reference, this version is used for easy mode running the whole pipeline.

> `p2000_ar_fund_in_pipeline_v22_reference.py` is an unchanged copy of the final single-file v22 prototype supplied for the refactor.

> Use the modular package at the project root for future maintenance. Keep this file only as a regression and traceability baseline. Do not add new features to the legacy file.


# P2000 AR Fund In - Amount Logic Check

## 1. Purpose and current status

`p2000_ar_fund_in_pipeline_db_lookup_env.py` is a **read-only logic reconstruction, Excel generation, and historical validation prototype** for the P2000 AR Fund In workflow.

It can:

- read a simple Excel input;
- use live P2000 reference lookups;
- simulate counter allocation;
- generate proposed `CHECK_HDR`, `CHECK_LINE`, and `ACCOUNT_AR_AP` update rows;
- validate the core amount and currency relationships;
- compare generated rows with database rows when exact keys are available.

It does **NOT** insert or update production tables. It does not commit or roll back database transactions, post to GL, send email, or move files.

Supported business scope:

```text
CHECK_HDR.DOC_TYPE       = AR
CHECK_HDR.DOC_CATEGORY   = R2
CHECK_LINE.PAY_DOC_CATEGORY = IV
ACCOUNT_AR_AP.LINE       = 0
CUSTVEND.CUST_VEND       = C
```

Exceptions: `AD`, `MS`, advanced payment, reversal-only, zero-payment, and non-`C` customer scenarios are outside the current automated scope.

---

## 2. Files

```text
p2000_ar_fund_in_pipeline_db_lookup_env.py
README_lookup_amount.md
clean_historical_input.sql -> refer to the root folder file
```

Pipeline: run `clean_historical_input.sql1` -> save the query results as .xlsx -> run `p2000_ar_fund_in_pipeline_db_lookup_env.py` to see the generated mock data

---

## 3. Canonical input format

The first Excel sheet must contain:

| Column | Required | Meaning |
|---|---:|---|
| `INPUT_HDR_GROUP_ID` | Yes | Input-only grouping identifier. Rows with the same value form one receipt.The sql lookup from CHECK_HDR.DOC_NO |
| `BANK_ID` | Yes | Receiving bank identifier. |
| `ACCOUNT_CURRENCY` | Yes | Currency actually received from the customer. |
| `C_PAID_AMOUNT` | Yes | Current receipt amount applied to this invoice in invoice converted currency. This is a delta, not a cumulative total. We assume that user input `ACCOUNT_AR_AP.C_PAID_TOTAL` is the delta amount of each invoice amount. |
| `CHECK_DATE` | Yes | Receipt/check date. |
| `PAY_USER_DOC` | Yes | User-entered invoice user document number. Treated as text. |
| `ACCTNO` | Yes | Customer account number. |
| `GL_CODE_DISC` | Yes | Discount GL code. Blank values default to `750201-00`. User may override to `790550-00`later. |
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

## 4. Commands

### Generate proposed rows

```powershell
python .\p2000_ar_fund_in_pipeline_db_lookup_env.py generate `
  --input ".\input.xlsx" `
  --output ".\generated_output.xlsx" `
  --env-file "..\.env" `
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
python .\p2000_ar_fund_in_pipeline_db_lookup_env_v22.py validate-db `
  --top 1000 `
  --currency-mode all `
  --output ".\historical_validation_v22.xlsx" `
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

---

## 5. Handover conclusion

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

Version 22 should therefore be described as:

> A read-only AR Fund In logic reconstruction, generation, and historical validation prototype for the standard AR/R2/IV workflow.
