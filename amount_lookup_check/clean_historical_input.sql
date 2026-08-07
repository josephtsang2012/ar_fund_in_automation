/*
P2000 AR Fund In clean historical input
Clean historical input extraction for the supported AR/R2/IV prototype scope.

Purpose
-------
Return historical rows in the canonical user-input format:

    INPUT_HDR_GROUP_ID
    BANK_ID
    ACCOUNT_CURRENCY
    C_PAID_AMOUNT
    CHECK_DATE
    PAY_USER_DOC
    ACCTNO
    GL_CODE_DISC
    NOTE

Important rules
---------------
1. INPUT_HDR_GROUP_ID is an input-only grouping identifier. It is not inserted
   into CHECK_HDR.DOC_NO. For historical samples, this query prefixes the old
   CHECK_HDR.DOC_NO as HIST_HDR_<DOC_NO> so the origin remains clear.
2. PAY_DOC_NO is not user input. The production lookup derives it from the
   unique ACCOUNT_AR_AP row matched by:
       RTRIM(USER_DOC) + RTRIM(ACCTNO) + DOC_CATEGORY='IV' + LINE=0
3. C_PAID_AMOUNT is the current receipt delta in invoice converted currency.
   It maps directly to CHECK_LINE.C_NETAMOUNT. It is not the current cumulative
   ACCOUNT_AR_AP.C_PAID_TOTAL value.
4. PAY_USER_DOC remains text. RTRIM removes variable SQL padding while
   preserving leading zeroes. No fuzzy matching is used.
5. NOTE is optional. NULL and empty values are allowed.
6. This query only extracts conservative, clean historical examples. It does
   not reconstruct payment amounts and does not update the database.
*/

DECLARE @TopReceipts int = 500;
DECLARE @DateFrom date = NULL;  -- example: '2025-01-01'
DECLARE @DateTo   date = NULL;  -- inclusive

WITH ARAPKeyStats AS
(
    SELECT
        RTRIM(USER_DOC) AS USER_DOC_KEY,
        RTRIM(ACCTNO) AS ACCTNO_KEY,
        RTRIM(DOC_CATEGORY) AS DOC_CATEGORY_KEY,
        COUNT(*) AS MATCH_COUNT,
        MIN(DOC_NO) AS DERIVED_PAY_DOC_NO
    FROM dbo.ACCOUNT_AR_AP
    WHERE ISNULL(LINE, 0) = 0
      AND RTRIM(DOC_CATEGORY) = 'IV'
    GROUP BY
        RTRIM(USER_DOC),
        RTRIM(ACCTNO),
        RTRIM(DOC_CATEGORY)
),
CustomerUnique AS
(
    SELECT
        RTRIM(ACCTNO) AS ACCTNO_KEY,
        RTRIM(SUBC) AS SUBC_KEY,
        COUNT(DISTINCT NULLIF(RTRIM(NAME), '')) AS UNIQUE_NAME_COUNT
    FROM dbo.CUSTVEND
    WHERE RTRIM(CUST_VEND) = 'C'
    GROUP BY
        RTRIM(ACCTNO),
        RTRIM(SUBC)
    HAVING COUNT(DISTINCT NULLIF(RTRIM(NAME), '')) = 1
),
BankUnique AS
(
    SELECT
        RTRIM(BANK_ID) AS BANK_ID_KEY,
        CASE
            WHEN UPPER(RTRIM(CURENCY_BASE)) IN ('RMB', 'CNY') THEN 'CNY'
            ELSE UPPER(RTRIM(CURENCY_BASE))
        END AS ACCOUNT_CURRENCY_KEY,
        COUNT(*) AS MATCH_COUNT
    FROM dbo.BANKS_ACCOUNTS
    GROUP BY
        RTRIM(BANK_ID),
        CASE
            WHEN UPPER(RTRIM(CURENCY_BASE)) IN ('RMB', 'CNY') THEN 'CNY'
            ELSE UPPER(RTRIM(CURENCY_BASE))
        END
    HAVING COUNT(*) = 1
),
AllReceiptLines AS
(
    SELECT
        H.DOC_NO AS HDR_DOC_NO,
        H.BANK_ID,
        H.CHECK_DATE,
        H.ACCOUNT_CURRENCY,
        H.NOTE,
        H.APPLIED AS HDR_APPLIED,
        H.AMOUNT AS HDR_AMOUNT,
        H.C_APPLIED AS HDR_C_APPLIED,
        H.C_AMOUNT AS HDR_C_AMOUNT,
        H.DISCOUNT AS HDR_DISCOUNT,
        H.C_DISCOUNT AS HDR_C_DISCOUNT,
        H.CURENCY_FACTOR AS HDR_CURENCY_FACTOR,
        H.ACCOUNT_FACTOR AS HDR_ACCOUNT_FACTOR,
        L.LINE,
        L.PAY_DOC_NO,
        L.PAY_USER_DOC,
        L.PAY_DOC_CATEGORY,
        L.ACCTNO,
        L.SUBC,
        L.GL_CODE_DISC,
        L.AMOUNT,
        L.NETAMOUNT,
        L.DISCOUNT,
        L.C_AMOUNT,
        L.C_NETAMOUNT,
        L.C_DISCOUNT,
        L.DISCOUNT_DISCREPANCY,
        L.NETAMOUNT_DISCREPANCY,
        L.CURRENCY_DISCREPANCY,
        L.ORIG_DISCOUNT,
        L.ORIG_NETAMOUNT,
        L.ORIG_AMOUNT,
        L.C_DISCOUNT_DISCREPANCY,
        L.C_NETAMOUNT_DISCREPANCY,
        L.C_CURRENCY_DISCREPANCY,
        L.C_ORIG_DISCOUNT,
        L.C_ORIG_NETAMOUNT,
        L.C_ORIG_AMOUNT
    FROM dbo.CHECK_HDR H
    JOIN dbo.CHECK_LINE L
        ON L.DOC_NO = H.DOC_NO
       AND RTRIM(L.DOC_CATEGORY) = RTRIM(H.DOC_CATEGORY)
    WHERE RTRIM(H.DOC_TYPE) = 'AR'
      AND RTRIM(H.DOC_CATEGORY) = 'R2'
      AND (@DateFrom IS NULL OR H.CHECK_DATE >= @DateFrom)
      AND (@DateTo IS NULL OR H.CHECK_DATE < DATEADD(day, 1, @DateTo))
),
ResolvedLines AS
(
    SELECT
        L.*,
        K.MATCH_COUNT AS ARAP_KEY_MATCH_COUNT,
        K.DERIVED_PAY_DOC_NO,
        A.DOC_NO AS ARAP_DOC_NO,
        A.USER_DOC AS ARAP_USER_DOC,
        A.SUBC AS ARAP_SUBC,
        A.CURENCY_BASE AS ARAP_CURENCY_BASE,
        A.CURENCY_CONV AS ARAP_CURENCY_CONV,
        A.CURENCY_FACTOR AS ARAP_CURENCY_FACTOR,
        A.CURENCY_STATE AS ARAP_CURENCY_STATE,
        I.DOC_NO AS INV_DOC_NO,
        I.DOC_TOTAL AS INV_DOC_TOTAL,
        I.C_DOC_TOTAL AS INV_C_DOC_TOTAL,
        I.CURENCY_BASE AS INV_CURENCY_BASE,
        I.CURENCY_CONV AS INV_CURENCY_CONV,
        I.CURENCY_FACTOR AS INV_CURENCY_FACTOR,
        CU.UNIQUE_NAME_COUNT,
        BU.MATCH_COUNT AS BANK_MATCH_COUNT,
        CASE
            WHEN UPPER(RTRIM(COALESCE(I.CURENCY_BASE, A.CURENCY_BASE)))
                 IN ('RMB', 'CNY') THEN 'CNY'
            ELSE UPPER(RTRIM(COALESCE(I.CURENCY_BASE, A.CURENCY_BASE)))
        END AS BASE_CURRENCY_KEY,
        CASE
            WHEN UPPER(RTRIM(COALESCE(I.CURENCY_CONV, A.CURENCY_CONV)))
                 IN ('RMB', 'CNY') THEN 'CNY'
            ELSE UPPER(RTRIM(COALESCE(I.CURENCY_CONV, A.CURENCY_CONV)))
        END AS INVOICE_CURRENCY_KEY,
        CASE
            WHEN UPPER(RTRIM(L.ACCOUNT_CURRENCY)) IN ('RMB', 'CNY') THEN 'CNY'
            ELSE UPPER(RTRIM(L.ACCOUNT_CURRENCY))
        END AS PAYMENT_CURRENCY_KEY,
        CASE
            WHEN UPPER(RTRIM(COALESCE(I.CURENCY_BASE, A.CURENCY_BASE)))
                 = UPPER(RTRIM(COALESCE(I.CURENCY_CONV, A.CURENCY_CONV)))
                THEN CAST(1.0 AS decimal(28, 12))
            ELSE COALESCE(
                NULLIF(CAST(I.CURENCY_FACTOR AS decimal(28, 12)), 0),
                NULLIF(CAST(A.CURENCY_FACTOR AS decimal(28, 12)), 0)
            )
        END AS SOURCE_LINE_FACTOR
    FROM AllReceiptLines L
    LEFT JOIN ARAPKeyStats K
        ON K.USER_DOC_KEY = RTRIM(L.PAY_USER_DOC)
       AND K.ACCTNO_KEY = RTRIM(L.ACCTNO)
       AND K.DOC_CATEGORY_KEY = 'IV'
    LEFT JOIN dbo.ACCOUNT_AR_AP A
        ON A.DOC_NO = K.DERIVED_PAY_DOC_NO
       AND RTRIM(A.USER_DOC) = K.USER_DOC_KEY
       AND RTRIM(A.ACCTNO) = K.ACCTNO_KEY
       AND RTRIM(A.DOC_CATEGORY) = 'IV'
       AND ISNULL(A.LINE, 0) = 0
    LEFT JOIN dbo.INV_HDR I
        ON I.DOC_NO = A.DOC_NO
    LEFT JOIN CustomerUnique CU
        ON CU.ACCTNO_KEY = RTRIM(L.ACCTNO)
       AND CU.SUBC_KEY = RTRIM(A.SUBC)
    LEFT JOIN BankUnique BU
        ON BU.BANK_ID_KEY = RTRIM(L.BANK_ID)
       AND BU.ACCOUNT_CURRENCY_KEY =
            CASE
                WHEN UPPER(RTRIM(L.ACCOUNT_CURRENCY)) IN ('RMB', 'CNY')
                    THEN 'CNY'
                ELSE UPPER(RTRIM(L.ACCOUNT_CURRENCY))
            END
),
ReceiptStats AS
(
    SELECT
        HDR_DOC_NO,
        COUNT(*) AS TOTAL_LINE_COUNT,
        SUM(CASE WHEN RTRIM(PAY_DOC_CATEGORY) = 'IV' THEN 1 ELSE 0 END)
            AS IV_LINE_COUNT,
        SUM(CASE WHEN RTRIM(PAY_DOC_CATEGORY) <> 'IV'
                      OR PAY_DOC_CATEGORY IS NULL THEN 1 ELSE 0 END)
            AS NON_IV_LINE_COUNT,
        SUM(CASE WHEN ARAP_KEY_MATCH_COUNT = 1
                      AND DERIVED_PAY_DOC_NO = PAY_DOC_NO
                      AND ARAP_DOC_NO IS NOT NULL
                      AND INV_DOC_NO IS NOT NULL
                 THEN 1 ELSE 0 END)
            AS SAFE_DERIVATION_LINE_COUNT,
        SUM(CASE WHEN UNIQUE_NAME_COUNT = 1 THEN 1 ELSE 0 END)
            AS VALID_CUSTOMER_LINE_COUNT,
        SUM(CASE WHEN BANK_MATCH_COUNT = 1 THEN 1 ELSE 0 END)
            AS VALID_BANK_LINE_COUNT,
        COUNT(DISTINCT BASE_CURRENCY_KEY) AS DISTINCT_BASE_CURRENCY_COUNT,
        COUNT(DISTINCT INVOICE_CURRENCY_KEY) AS DISTINCT_INVOICE_CURRENCY_COUNT,
        COUNT(DISTINCT RTRIM(ARAP_SUBC)) AS DISTINCT_SUBC_COUNT,
        SUM(CASE WHEN SOURCE_LINE_FACTOR IS NULL OR SOURCE_LINE_FACTOR = 0
                 THEN 1 ELSE 0 END) AS MISSING_FACTOR_LINE_COUNT,
        SUM(CASE
                WHEN ABS(
                    CAST(C_AMOUNT AS decimal(28, 12))
                    - CAST(AMOUNT AS decimal(28, 12)) * SOURCE_LINE_FACTOR
                ) > 0.05
                THEN 1 ELSE 0
            END) AS LINE_AMOUNT_FACTOR_MISMATCH_COUNT,
        SUM(CASE
                WHEN ABS(
                    CAST(C_NETAMOUNT AS decimal(28, 12))
                    - CAST(NETAMOUNT AS decimal(28, 12)) * SOURCE_LINE_FACTOR
                ) > 0.05
                THEN 1 ELSE 0
            END) AS LINE_NET_FACTOR_MISMATCH_COUNT,
        SUM(CASE
                WHEN ABS(
                    CAST(C_DISCOUNT AS decimal(28, 12))
                    - (
                        CAST(C_AMOUNT AS decimal(28, 12))
                        - CAST(C_NETAMOUNT AS decimal(28, 12))
                    )
                ) > 0.05
                  OR ABS(
                    CAST(DISCOUNT AS decimal(28, 12))
                    - (
                        CAST(AMOUNT AS decimal(28, 12))
                        - CAST(NETAMOUNT AS decimal(28, 12))
                    )
                ) > 0.05
                THEN 1 ELSE 0
            END) AS LINE_DISCOUNT_MISMATCH_COUNT,
        SUM(CASE
                WHEN ISNULL(DISCOUNT_DISCREPANCY, 0) <> 0
                  OR ISNULL(NETAMOUNT_DISCREPANCY, 0) <> 0
                  OR ISNULL(CURRENCY_DISCREPANCY, 0) <> 0
                  OR ISNULL(ORIG_DISCOUNT, 0) <> 0
                  OR ISNULL(ORIG_NETAMOUNT, 0) <> 0
                  OR ISNULL(ORIG_AMOUNT, 0) <> 0
                  OR ISNULL(C_DISCOUNT_DISCREPANCY, 0) <> 0
                  OR ISNULL(C_NETAMOUNT_DISCREPANCY, 0) <> 0
                  OR ISNULL(C_CURRENCY_DISCREPANCY, 0) <> 0
                  OR ISNULL(C_ORIG_DISCOUNT, 0) <> 0
                  OR ISNULL(C_ORIG_NETAMOUNT, 0) <> 0
                  OR ISNULL(C_ORIG_AMOUNT, 0) <> 0
                THEN 1 ELSE 0
            END) AS NONSTANDARD_AMOUNT_FIELD_COUNT,
        SUM(CASE WHEN ISNULL(C_NETAMOUNT, 0) <= 0 THEN 1 ELSE 0 END)
            AS NONPOSITIVE_C_PAID_AMOUNT_COUNT,
        SUM(CAST(AMOUNT AS decimal(28, 12))) AS SUM_LINE_AMOUNT,
        SUM(CAST(NETAMOUNT AS decimal(28, 12))) AS SUM_LINE_NETAMOUNT,
        SUM(CAST(DISCOUNT AS decimal(28, 12))) AS SUM_LINE_DISCOUNT,
        SUM(CAST(C_AMOUNT AS decimal(28, 12))) AS SUM_LINE_C_AMOUNT,
        SUM(CAST(C_NETAMOUNT AS decimal(28, 12))) AS SUM_LINE_C_NETAMOUNT,
        SUM(CAST(C_DISCOUNT AS decimal(28, 12))) AS SUM_LINE_C_DISCOUNT,
        MAX(CAST(HDR_APPLIED AS decimal(28, 12))) AS HDR_APPLIED,
        MAX(CAST(HDR_AMOUNT AS decimal(28, 12))) AS HDR_AMOUNT,
        MAX(CAST(HDR_DISCOUNT AS decimal(28, 12))) AS HDR_DISCOUNT,
        MAX(CAST(HDR_C_APPLIED AS decimal(28, 12))) AS HDR_C_APPLIED,
        MAX(CAST(HDR_C_AMOUNT AS decimal(28, 12))) AS HDR_C_AMOUNT,
        MAX(CAST(HDR_C_DISCOUNT AS decimal(28, 12))) AS HDR_C_DISCOUNT
    FROM ResolvedLines
    GROUP BY HDR_DOC_NO
),
EligibleReceipts AS
(
    SELECT TOP (@TopReceipts)
        R.HDR_DOC_NO
    FROM ReceiptStats R
    JOIN dbo.CHECK_HDR H
        ON H.DOC_NO = R.HDR_DOC_NO
       AND RTRIM(H.DOC_CATEGORY) = 'R2'
    WHERE R.TOTAL_LINE_COUNT = R.IV_LINE_COUNT
      AND R.NON_IV_LINE_COUNT = 0
      AND R.SAFE_DERIVATION_LINE_COUNT = R.TOTAL_LINE_COUNT
      AND R.VALID_CUSTOMER_LINE_COUNT = R.TOTAL_LINE_COUNT
      AND R.VALID_BANK_LINE_COUNT = R.TOTAL_LINE_COUNT
      AND R.DISTINCT_BASE_CURRENCY_COUNT = 1
      AND R.DISTINCT_INVOICE_CURRENCY_COUNT = 1
      AND R.DISTINCT_SUBC_COUNT = 1
      AND R.MISSING_FACTOR_LINE_COUNT = 0
      AND R.LINE_AMOUNT_FACTOR_MISMATCH_COUNT = 0
      AND R.LINE_NET_FACTOR_MISMATCH_COUNT = 0
      AND R.LINE_DISCOUNT_MISMATCH_COUNT = 0
      AND R.NONSTANDARD_AMOUNT_FIELD_COUNT = 0
      AND R.NONPOSITIVE_C_PAID_AMOUNT_COUNT = 0
      AND ABS(R.HDR_APPLIED - R.SUM_LINE_AMOUNT) <= 0.05
      AND ABS(R.HDR_AMOUNT - R.SUM_LINE_NETAMOUNT) <= 0.05
      AND ABS(R.HDR_DISCOUNT - R.SUM_LINE_DISCOUNT) <= 0.05
      AND ABS(R.HDR_C_APPLIED - R.SUM_LINE_C_AMOUNT) <= 0.05
      AND ABS(R.HDR_C_AMOUNT - R.SUM_LINE_C_NETAMOUNT) <= 0.05
      AND ABS(R.HDR_C_DISCOUNT - R.SUM_LINE_C_DISCOUNT) <= 0.05
    ORDER BY H.CHECK_DATE DESC, R.HDR_DOC_NO DESC
)
SELECT
    CONCAT('HIST_HDR_', CAST(L.HDR_DOC_NO AS varchar(30)))
        AS INPUT_HDR_GROUP_ID,
    RTRIM(L.BANK_ID) AS BANK_ID,
    CASE
        WHEN UPPER(RTRIM(L.ACCOUNT_CURRENCY)) IN ('RMB', 'CNY') THEN 'CNY'
        ELSE UPPER(RTRIM(L.ACCOUNT_CURRENCY))
    END AS ACCOUNT_CURRENCY,
    CAST(L.C_NETAMOUNT AS decimal(28, 12)) AS C_PAID_AMOUNT,
    L.CHECK_DATE,
    RTRIM(L.PAY_USER_DOC) AS PAY_USER_DOC,
    RTRIM(L.ACCTNO) AS ACCTNO,
    COALESCE(NULLIF(RTRIM(L.GL_CODE_DISC), ''), '750201-00')
        AS GL_CODE_DISC,
    NULLIF(RTRIM(L.NOTE), '') AS NOTE
FROM ResolvedLines L
JOIN EligibleReceipts E
    ON E.HDR_DOC_NO = L.HDR_DOC_NO
WHERE RTRIM(L.PAY_DOC_CATEGORY) = 'IV'
ORDER BY
    L.CHECK_DATE DESC,
    L.HDR_DOC_NO DESC,
    L.LINE;
