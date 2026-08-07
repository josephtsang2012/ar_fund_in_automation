# AR Fund In Backend System

A backend data processing system that batch-imports customer payment data from Excel (and TBD xml files) and automatically updates P2000 database for AR (Accounts Receivable) fund in records. This eliminates the need to create AR receipts manually using the frontend P2000 ERP.

---

## 1. What This System Does

### Overview

The system reads payment data from a user-provided Excel file and inserts/updates records across three core database tables: `ACCOUNT_AR_AP`, `CHECK_HDR`, and `CHECK_LINE`. It also automatically updates 'counter' in `COUNTERSTBL` to generate and keep track of different document numbers required for those 3 tables. In addition, it retrieves reference data from related tables (such as `BANKS_ACCOUNTS`, `CUST_VEND`, `INV_HDR`, `TBLCONV`) and handles multi-currency conversions. All updating/ inserting operations are wrapped in a single transaction to ensure data integrity.

### Scope & Limitations

**Current Version Supports:**
- Batch processing via shared Excel file input
- "Apply Invoice" mode (applying payments to existing invoices)
- Three core tables: `ACCOUNT_AR_AP`, `CHECK_HDR`, `CHECK_LINE`
- Counter table (`COUNTERSTBL`) for document number/ counter update

**Not Supported (Future Enhancements):**
- User interface (UI) for XML upload
- "Advanced Payment" mode (linked to Sales Orders)
- `STOCKIMG` table operations (document image uploads)
- `GLTRANS` table operations (GL posting is achieved by setting auto-schedules in P2000)

### Complete Workflow

1. **Read Input** – Parses the user-provided Excel file containing payment data

2. **Data Validation** – Validates required fields, data types, and reference integrity

3. **Reference Lookup** – Queries reference tables for additional data

4. **Field Calculation** – Computes derived fields based on currency conversion logic

5. **Sequential Table Updates** – Executes in strict order:
   - `CHECK_HDR` → `COUNTERSTBL` → `CHECK_LINE` → `COUNTERSTBL` → `ACCOUNT_AR_AP` → `COUNTERSTBL`
   - This is based on logical deduction, see [methodology.md](https://github.com/joseph-tsang-topcast/AR-Fund-In/blob/ac68d047f826f71e78683262887d4da060a615df/methodology.md)

6. **Transaction Commit** – Commits all changes atomically; rolls back entirely if any step fails

7. **Notification** – Sends success confirmation with record count or failure alert with error details

---

## 2. Project Layout

### File Structure

| File | Purpose |
|------|---------|
| `main.py` | Entry point, orchestrates the entire workflow |
| `excel_reader.py` | Reads and validates payment data from Excel files |
| `field_mapper.py` | Maps Excel columns to database fields |
| `reference_lookup.py` | Queries reference tables (`BANKS_ACCOUNTS`, `CUST_VEND`, `INV_HDR`, `TBLCONV`) |


---

## 3. Data Model & Field Mapping

### 3.1 CHECK_HDR Table

| Field | Data Source | Description / Logic |
|-------|-------------|---------------------|
| `ACCTNO` | User Input | Customer Code inputted by user; also lookup from `ACCOUNT_AR_AP.ACCTNO` |
| `ACCOUNT_CURRENCY` | User Input | Currency the customer actually pays in |
| `ACCOUNT_AMOUNT` | User Input | Payment amount in account currency |
| `ACCOUNT_FACTOR` | From Reference | `TBLCONV.FACTOR` (using latest exchange rate) matching the `FR_CODE` = USD and `TO_CODE` = user input |
| `APPLIED` | From Reference & Calculated | = Sum of `INV_HDR.DOC_TOTAL` for all invoices linked to the receipt. `INV_HDR` is matched through `CHECK_LINE.PAY_DOC_NO` → `ACCOUNT_AR_AP.DOC_NO` → `INV_HDR.DOC_NO` |
| `C_APPLIED` | From Reference & Calculated | = Sum of `INV_HDR.C_DOC_TOTAL` for all invoices linked to the receipt, using the same lookup relationship as `APPLIED` |
| `AMOUNT` | Calculated | `ACCOUNT_AMOUNT` / `ACCOUNT_FACTOR` |
| `NETAMOUNT` | Calculated | = `AMOUNT` |
| `DISCOUNT` | Calculated | `APPLIED` - `AMOUNT` |
| `C_AMOUNT` | Calculated | `ACCOUNT_AMOUNT` / `ACCOUNT_FACTOR` * `CHECK_HDR.CURENCY_FACTOR` |
| `C_NETAMOUNT` | Calculated | = `C_AMOUNT` |
| `C_DISCOUNT` | Calculated | `C_APPLIED` - `C_AMOUNT` |
| `BANK_ID` | User Input | Bank identifier |
| `NOTE` | User Input | Text |
| `ADDED_USR` | User Input | P2000 user code |
| `DOC_NO` | Counter | From `COUNTERSTBL.COUNTER` where `DOC_CATEGORY` = 'CHECK' |
| `CHECK_NO` | Counter | From `COUNTERSTBL.COUNTER` where `DOC_CATEGORY` = 'R2' and `DOC_TYPE` = 'D'|
| `CHECK_COUNTER` | Counter | From `COUNTERSTBL.COUNTER` where `DOC_CATEGORY` = 'ACRN' |
| `BATCH_NO` | Counter | From `COUNTERSTBL.COUNTER` where `DOC_CATEGORY` = 'BC' |
| `PAYEE` | From Reference | From `dbo.CUSTVEND.NAME` where `CUST_VEND` = 'C', `ACCTNO` = user input,  `SUBC` and `COMPANYNO`  obtained from the matched existing `ACCOUNT_AR_AP` invoice row |
| `COMPANYNO` | Lookup Value | From `ACCOUNT_AR_AP.COMPANYNO` |
| `SUBC` | Lookup Value | From `ACCOUNT_AR_AP.SUBC` |
| `ACCOUNT_NO` | From Reference | From `BANKS_ACCOUNTS.ACCOUNT_NO`, where `BANK_ID` = user input, `ACCOUNT_CURRENCY`= user input |
| `GL_CODE` | From Reference | From `BANKS_ACCOUNTS.GL_CODE` where `BANK_ID` = user input, `ACCOUNT_CURRENCY`= user input |
| `CURENCY_STATE` | Lookup Value | From `ACCOUNT_AR_AP.CURENCY_STATE` |
| `CURENCY_BASE` | Lookup Value | From `ACCOUNT_AR_AP.CURENCY_BASE` |
| `CURENCY_CONV` | Lookup Value | From `ACCOUNT_AR_AP.CURENCY_CONV` |
| `CURENCY_FACTOR` | Lookup Value | From `ACCOUNT_AR_AP.CURENCY_FACTOR` |
| `CHECK_DATE`, `PAYMENT_DATE`, `PRINTED_ON`, `CURENCY_DATE`, `ACCOUNT_FACTOR_DATE`, `ADDED_DTE` | Calculated | Timestamp; might have slight delta |
| `BATCH_DATE` | Calculated | Date |
| `DOC_TYPE` | Default | 'AR' |
| `DOC_CATEGORY` | Default | Fixed as 'R2' |
| `DOC_STATUS`, `CLEARED_CURRENCY_STATE` | Default | Fixed as 1 |
| `MANUAL_AUTO` | Default | 'M' |
| `CUST_VEND` | Default | 'C' |
| `POST_GL`, `CLEARED`, `PAID_BY_ACH`, `SIGN_1_APR`, `SIGN_2_APR`, `SIGN_3_APR`, `SIGN_4_APR`, `SIGN_5_APR` | Default | 'N' |
| `CRDT_LINE`, `LAST_STATEMENT_BALANCE`, `DISCOUNT_DISCREPANCY`, `NETAMOUNT_DISCREPANCY`, `CURRENCY_DISCREPANCY`, `OPEN_BALANCE`, `C_DISCOUNT_DISCREPANCY`, `C_NETAMOUNT_DISCREPANCY`, `C_CURRENCY_DISCREPANCY`, `C_OPEN_BALANCE`, `ORIG_DISCOUNT`, `ORIG_DISCOUNT_DISCREPANCY`, `ORIG_NETAMOUNT`, `ORIG_NETAMOUNT_DISCREPANCY`, `ORIG_AMOUNT`, `ORIG_APPLIED`, `ORIG_OPEN_BALANCE`, `C_ORIG_AMOUNT`, `C_ORIG_DISCOUNT`, `C_ORIG_DISCOUNT_DISCREPANCY`, `C_ORIG_NETAMOUNT`, `C_ORIG_NETAMOUNT_DISCREPANCY`, `C_ORIG_APPLIED`, `C_ORIG_OPEN_BALANCE`, `ACCOUNT_ORIG_AMOUNT` | Default | 0 |
| `CRDT_DOC_NO`, `CRDT_DOC_TYPE`, `CRDT_DOC_CATEGORY`, `CRDT_CHECK_NO`, `AUTO_BATCH`, `STATUS`, `CHECKBOOK_NO`, `PAYTO`, `PAYTO_SUBC`, `PAYTO_CCODE`, `INFO_BANK_ID`, `INFO_BANK_NAME`, `INFO_SWIFT`, `INFO_ACCOUNT_NO`, `INFO_BRANCH_NO`, `WIRE_PROCESS_DATE`, `WIRE_FILE_NAME`, `CC_CREDITCARD`, `CC_NAME`, `CC_NUMBER`, `CC_EXP`, `PRINT_DATE`, `POST_GL_DATE`, `STATEMENT_DOC_NO`, `DEPOSIT_DOC_NO`, `CLEARED_BY`, `CLEARED_ON`, `ACH_ACCOUNT_NO`, `ACH_ACCOUNT_NAME`, `ACH_TRANSACTION_CODE`, `ACH_BANK_NAME`, `ACH_ROUTING_NO`, `BRANCH_NO`, `BANK_NO`, `BANK_NAME`, `DOCUMENT_REFERENCE`, `MEMO`, `SIGN_1`, `SIGN_2`, `SIGN_3`, `SIGN_4`, `SIGN_5`, `SIGN_1_DATE`, `SIGN_2_DATE`, `SIGN_3_DATE`, `SIGN_4_DATE`, `SIGN_5_DATE`, `APPLIED_BY`, `ACH_PAYMENT_TYPE`, `RECLOCK`, `UPDATED_USR`, `UPDATED_DTE` | Default | NULL |
| `DIVISION`, `DEPART`, `TRANSFER_GL_CODE` | Default | BLANK |

#### Invoice Header Lookup

For each `CHECK_LINE`, the related invoice header is retrieved through the following relationship:

```text
CHECK_LINE.PAY_DOC_NO
    → ACCOUNT_AR_AP.DOC_NO
    → INV_HDR.DOC_NO
```

`ACCOUNT_AR_AP.USER_DOC` and `INV_HDR.USER_DOC` may be used as additional validation fields. `USER_DOC` must be treated as text because leading zeros must be preserved.

The receipt-level values are then calculated as:

```text
CHECK_HDR.APPLIED
    = SUM(INV_HDR.DOC_TOTAL for all linked invoice rows)

CHECK_HDR.C_APPLIED
    = SUM(INV_HDR.C_DOC_TOTAL for all linked invoice rows)
```


### 3.2 CHECK_LINE Table

| Field | Data Source | Description / Logic |
|-------|-------------|---------------------|
| `ACCTNO` | User Input | Customer Code inputted by user; also lookup from `ACCOUNT_AR_AP.ACCTNO` |
| `C_DISCOUNT` | Calculated | `C_AMOUNT` - `C_NETAMOUNT`; confirmed with 100% historical match |
| `C_NETAMOUNT` | User Input | Same user input = ACCOUNT_AR_AP.C_PAID_TOTAL` |
| `C_AMOUNT` | Lookup Value | From `ACCOUNT_AR_AP.C_DOC_TOTAL`; working logic with 96.37% historical match |
| `DISCOUNT` | Lookup & Calculated | `C_DISCOUNT / CHECK_HDR.CURENCY_FACTOR` |
| `NETAMOUNT` | Lookup & Calculated | `C_NETAMOUNT / CHECK_HDR.CURENCY_FACTOR` |
| `AMOUNT` | Lookup & Calculated | `C_AMOUNT / CHECK_HDR.CURENCY_FACTOR` |
| `GL_CODE_DISC` | User Input | Default '750201-00', user may override to '790550-00' |
| `ADDED_USR` | User Input | User who added the record; same user as the `UPDATED_USR` in the `ACCOUNT_AR_AP` Table|
| `DOC_NO` | Lookup Value | From `CHECK_HDR.DOC_NO` |
| `CHECK_NO` | Lookup Value | From `CHECK_HDR.CHECK_NO` |
| `PAY_USER_DOC` | Lookup Value | From `ACCOUNT_AR_AP.USER_DOC` |
| `LINE` | Counter | Line number, incremented per `COUNTERSTBL.COUNTER` filtering `DOC_CATEGORY`=R2 and `DOC_TYPE`=L |
| `PAY_DOC_NO` | Lookup Value | From `ACCOUNT_AR_AP.DOC_NO` |
| `PAY_TYPE` | Lookup Value | From `ACCOUNT_AR_AP.DOC_TYPE` |
| `SUBC` | Lookup Value | From `ACCOUNT_AR_AP.SUBC` |
| `COMPANYNO` | Lookup Value | From `ACCOUNT_AR_AP.COMPANYNO` |
| `PAY_APPLY` | Calculated | Timestamp of payment application |
| `PAY_DOC_DATE` | Calculated | `ACCOUNT_AR_AP.DOC_DATE + 2 days` |
| `ADDED_DTE` | Calculated | Timestamp when record was added |
| `DOC_TYPE` | Default | 'AR' |
| `DOC_CATEGORY` | Default | Fixed as 'R2' |
| `PAY_DOC_CATEGORY` | Default | 'IV' |
| `GL_CODE_AMT` | Default | '110101-00' |
| `GL_CODE_AMT_DISCREPANCY`, `GL_CODE_DISC_DISCREPANCY_DB`, `GL_CODE_DISC_DISCREPANCY_CR` | Default | '790550-00' |
| `CLEARED` | Default | 'N' |
| `DISCOUNT_DISCREPANCY`, `NETAMOUNT_DISCREPANCY`, `CURRENCY_DISCREPANCY`, `ORIG_DISCOUNT`, `ORIG_DISCOUNT_DISCREPANCY`, `ORIG_NETAMOUNT`, `ORIG_NETAMOUNT_DISCREPANCY`, `ORIG_AMOUNT`, `ORIG_CURRENCY_DISCREPANCY`, `C_DISCOUNT_DISCREPANCY`, `C_NETAMOUNT_DISCREPANCY`, `C_CURRENCY_DISCREPANCY`, `C_ORIG_DISCOUNT`, `C_ORIG_DISCOUNT_DISCREPANCY`, `C_ORIG_NETAMOUNT`, `C_ORIG_NETAMOUNT_DISCREPANCY`, `C_ORIG_AMOUNT`, `C_ORIG_CURRENCY_DISCREPANCY` | Default | 0 |
| `DOC_STATUS`, `DIVISION`, `DEPART`, `FORM_1099TYPE`, `TRANSACTION_ID`, `TRANSACTION_ID_SOURCE`, `STATEMENT_DOC_NO`, `CLEARED_BY`, `CLEARED_ON`, `RECLOCK`, `UPDATED_USR`, `UPDATED_DTE` | Default | NULL |

### 3.3 ACCOUNT_AR_AP Table

Different from the above two tables, ACCOUNT_AR_AP table actually pre-exists before the AR receipts are being created. This program does not insert new rows to this table but only updates certain columns that contain blank / null / zero values which require user input. The program updates the specified payment-related fields of the matched invoice rows and preserves all unrelated fields.   

| Field | Data Source | Description / Logic |
|-------|-------------|---------------------|
| `ACCTNO` | User Input | Customer Code; used to identify the rows to update |
| `USER_DOC` | User Input | Text format, with leading character '0'; used to identify the rows to update |
| `UPDATED_USR` | User Input | Corresponding to P2000 user code |
| `C_PAID_TOTAL` | User Input | Total paid amount in converted currency (according to the invoice) |
| `PAID_TOTAL` | Calculated | `C_PAID_TOTAL × CURRENCY_FACTOR` |
| `UPDATED_DTE` | Calculated | Corresponding to the timestamp when the update is executed |
| `DOC_TOTAL` | Lookup Value | From `INV_HDR.DOC_TOTAL`, matched through `ACCOUNT_AR_AP.DOC_NO = INV_HDR.DOC_NO` |
| `C_DOC_TOTAL` | Lookup Value | From `INV_HDR.C_DOC_TOTAL`, using the same lookup relationship as `DOC_TOTAL` |
| `CURENCY_FACTOR` | Lookup Value | Obtained from `TBLCONV` with latest corresponding value |
| **Other fields** | No Update | Existing table values remain unchanged |



### 3.4 Currency Logic

> This section explains the multi-currency handling referenced in Section 3.1-2, clarifying why calculations are needed and how the three currency scenarios differ.


| Scenario | Base Currency (P2000 Default) | Invoice Currency (INV_HDR) | Payment Currency (Customer) |
|----------|-------------------------------|---------------------------|-----------------------------|
| 1 | USD | USD | USD |
| 2 | USD | HKD | HKD |
| 3 | USD | USD | HKD |


- **Scenario 1** – All currencies are identical; no exchange rate conversion required.

- **Scenario 2** – Customer pays an invoice using the converted currency (i.e., pay local currency denominated in the invoice). Requires conversion from HKD to USD (i.e., `CURRENCY_FACTOR` between `CURRENCY_BASE` and `CURRENCY_CONV`) as P2000 reconciles the book in USD.

- **Scenario 3** – Customer pays in a currency different from the invoice currency. Accounting team must perform "currency exchange" adjustment (i.e., `ACCOUNT_CURRENCY` (HKD actually paid) -> `CURENCY_CONV` (HKD as stated in the invoice) -> `CURENCY_BASE` (USD as defaulted in P2000)


| Factor | Purpose | Source |
|--------|---------|--------|
| `CURENCY_FACTOR` | Converts **invoice currency (or converted currency)** → **base currency** | From `TBLCONV` (using latest exchange rate) |
| `ACCOUNT_FACTOR` | Converts **payment currency (or account currency)** → **base currency** | From `TBLCONV` (using latest exchange rate) |

---

## 4. Setup Guide

### System Requirements

- Windows OS with SQL Server access
- SQL Server instance with ODBC connectivity
- Python 3.8 or higher
- Access to the target P2000 database

### Python Dependencies

```
pandas
openpyxl
pyodbc
```

Install with:

```bash
pip install -r requirements.txt
```

### Database Configuration (`config.py`)

Set the database connection string:

```python
DATABASE_CONNECTION_STRING = r'DRIVER={SQL Server};SERVER=YOUR_SERVER;DATABASE=P2000_DB;UID=sa;PWD=your_password;'
```

### File Paths (`config.py`)

```python
INPUT_DIR = r"C:\ar_fund_in\input"
PROCESSED_DIR = r"C:\ar_fund_in\processed"
ERROR_DIR = r"C:\ar_fund_in\errors"
```

---

## 5. Input File Format

---

## 6. Usage Guide

### Basic Usage

1. Place the Excel file in the `INPUT_DIR` directory
2. Run the script:

```bash
python main.py
```

3. Check the output:
   - Success → File moved to `PROCESSED_DIR`, notification sent
   - Failure → File moved to `ERROR_DIR`, alert sent

### Command Line Arguments

```bash
python main.py --file "C:\ar_fund_in\input\payment_data.xlsx"
python main.py --test "C:\ar_fund_in\input\payment_data.xlsx"  # Test mode (no commit)
python main.py --dry-run "C:\ar_fund_in\input\payment_data.xlsx"  # Validate only
```

### Execution Flow

1. **Read Excel** – Load payment data from the specified file
2. **Validate Data** – Check all required fields and reference integrity
3. **Lookup References** – Query `BANKS_ACCOUNTS`, `CUST_VEND`, `INV_HDR`, `TBLCONV`, etc.
4. **Calculate Fields** – Compute all derived fields using currency logic
5. **Generate DOC_NO** – Get new document numbers from `COUNTERSTBL`
6. **Sequential Updates** – Execute in strict order:
   - `INSERT CHECK_HDR` → `UPDATE COUNTERSTBL`
   - `INSERT CHECK_LINE` → `UPDATE COUNTERSTBL`
   - `UPDATE ACCOUNT_AR_AP` → `UPDATE COUNTERSTBL`
7. **Commit Transaction** – Commit all changes atomically
8. **Send Notification** – Email success report with record count

---

## 7. Error Handling & Notifications

### Transaction Integrity

All database operations are wrapped in a single transaction:
- **Success** → All changes committed
- **Failure** → All changes rolled back (no partial updates)

### Error Types & Handling

| Error Type | Detection Point | Handling |
|------------|-----------------|----------|
| Missing required fields | Excel validation | Reject file, send alert |
| Reference not found | Reference lookup | Reject record, rollback transaction |
| Data type mismatch | Excel validation | Reject file, send alert |
| Currency conversion error | Calculation step | Rollback transaction |
| Database connection failure | Any step | Rollback transaction |
| COUNTERSTBL update failure | DOC_NO generation | Rollback the entire transaction, including counter updates (the counter deduction based on the number of lines impacted) |

### Notifications

**Success Notification:**
- Subject: `[AR Fund In] Processing Complete - YYYY-MM-DD`
- Content: Total records processed, record count per table, file location

**Failure Alert:**
- Subject: `[AR Fund In] Processing Failed - YYYY-MM-DD`
- Content: Error description, affected record, file location

---

## 8. Important Notes

### Update Sequence

- **Must follow the exact order**: `CHECK_HDR` → `COUNTERSTBL` → `CHECK_LINE` → `COUNTERSTBL` → `ACCOUNT_AR_AP` → `COUNTERSTBL`
- This ensures foreign key dependencies are satisfied and document numbers are generated correctly

### Timing

- Add sufficient `time.sleep()` intervals between INSERT/UPDATE operations
- This gives the database adequate time to complete each operation before the next begins

### Data Type & Constraints

- `DOC_NO` – Integer document identifier, pay attention to maximum length
- `USER_DOC` – Text field, starting with character '0' (not a number)
- Verify all column limits and data types against the actual database schema carefully before inserting
- NULL VS BLANK
   - **NULL** = Missing value (allowed in some fields)
   - **BLANK** = Empty string (not allowed in certain fields)
