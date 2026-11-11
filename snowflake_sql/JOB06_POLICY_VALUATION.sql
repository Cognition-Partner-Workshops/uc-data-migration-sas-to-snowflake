/*=====================================================================
  JOB06_POLICY_VALUATION.sql — Snowflake conversion of
  policy_valuation.sas (Insurance monthly policy book valuation)

  Source: ts-sas-legacy-analytics/Programs/Insurance/policy_valuation.sas
  SAS schedule: header says "Monthly 5th business day via Control-M
                INS_MONTHLY_01"; the estate actually runs it as step 2 of
                BatchJobs/run_daily_insurance.sas (daily 07:00, INS_MASTER,
                after claims_processing) — see docs/JOB06_policy_valuation.md.
  Snowflake: Runs as a Snowflake Task (orchestration/tasks.sql is owned by
             the integration child; not edited here).

  Inputs:  RAW_INS.POLICIES, RAW_INS.CLAIMS, RAW_INS.PREMIUMS
           (TERA_DW.ACTUARIAL_TABLES is listed in the SAS header but never
            referenced by the program body — nothing to read.)
  Outputs: STG_INS.POLICY_VALUATION, REPORTS.LOSS_RATIO_SUMMARY
           (REPORTS.RESERVE_ADEQUACY is listed in the SAS header but the
            program body never creates it — nothing to produce.)

  Why Snowflake SQL (not Snowpark):
    Steps 1-3 are PROC SQL (filter / aggregate), step 4 is a one-to-one
    MERGE BY POLICY_ID with `if a;` and no RETAIN / first. / last. /
    multi-output logic (= LEFT JOIN + scalar CASE columns), step 5 is
    PROC MEANS CLASS/nway (= GROUP BY) plus a one-pass DATA step adding
    two ratios. Everything is set-based; Snowpark would add nothing.

  Conversion notes (SAS construct -> Snowflake):
    - %policy_valuation(val_date=&CURR_DT, lob=ALL) macro parameters ->
      session variables $val_date / $lob. %parmv only validates them.
    - intck('month', a, b)            -> DATEDIFF('month', a, b)  (boundary count)
    - intnx('month', d, 3)            -> DATE_TRUNC('month', DATEADD(month, 3, d))
    - intnx('month', d, -12)          -> DATE_TRUNC('month', DATEADD(month, -12, d))
    - intnx('year', d, 0, 'B')        -> DATE_TRUNC('year', d)
    - PROC SQL max(a,b)/min(a,b) (SAS functions: ignore missing)
                                      -> GREATEST_IGNORE_NULLS / LEAST_IGNORE_NULLS
    - DATA-step max(0, x) (ignores missing) -> GREATEST_IGNORE_NULLS(0, x)
    - SAS `date <= const` is TRUE for a missing date (missing sorts low)
                                      -> explicit `OR col IS NULL` where SAS
                                         lets missing values through
    - MERGE ... BY POLICY_ID; if a;   -> LEFT JOIN (right sides are GROUP BY
                                         results, so exactly one row per key)
    - PROC MEANS CLASS POLICY_TYPE nway, n=N_POLICIES
                                      -> GROUP BY, COUNT(first VAR variable)
                                         rows with missing CLASS value dropped
    - formats ($POLTYPE., $RISKCAT., dollar18.2, percent8.2, date9.)
                                      -> presentation-layer; raw values stored
    - dataset label "Monthly Policy Valuation &val_ym"
                                      -> not reproducible as metadata with a
                                         session variable; documented only
    - proc datasets delete WORK.*     -> DROP TABLE of the TEMPORARY tables
    - %nobs / %put                    -> logging only; nothing to convert
=====================================================================*/

-- Session variables (set by the orchestrator task or caller).
-- Business date for the baseline run: CURR_DT = 31JAN2024.
-- SET val_date = '2024-01-31'::DATE;
-- SET lob      = 'ALL';   -- %parmv(_val=ALL AUTO HOME WL TL UL HLTH)

-- Derived date anchors, each the exact SAS intnx() result:
SET ytd_start      = DATE_TRUNC('year',  $val_date);                     -- intnx('year', val_date, 0, 'B')
SET renewal_cutoff = DATE_TRUNC('month', DATEADD(month, 3, $val_date));  -- intnx('month', val_date, 3)
SET claims_from    = DATE_TRUNC('month', DATEADD(month, -12, $val_date)); -- intnx('month', val_date, -12)

/* ----------------------------------------------------------
   Step 1: Extract In-Force Policies   (policy_valuation.sas:34-71)
   ---------------------------------------------------------- */
CREATE OR REPLACE TEMPORARY TABLE WORK_INFORCE AS
SELECT
    p.POLICY_ID,
    p.CUSTOMER_ID,
    p.POLICY_TYPE,
    p.EFFECTIVE_DATE,
    p.EXPIRATION_DATE,
    p.ANNUAL_PREMIUM,
    p.SUM_INSURED,
    p.DEDUCTIBLE,
    p.RISK_CATEGORY,
    p.UNDERWRITING_CLASS,
    p.AGENT_ID,
    p.BRANCH_CODE,
    DATEDIFF('month', p.EFFECTIVE_DATE, $val_date)      AS POLICY_AGE_MONTHS,
    DATEDIFF('month', $val_date, p.EXPIRATION_DATE)     AS MONTHS_TO_EXPIRY,
    CASE
        WHEN p.EXPIRATION_DATE <= $renewal_cutoff THEN 'Y'
        ELSE 'N'
    END                                                 AS RENEWAL_DUE_FLAG,
    -- Earned premium (monthly pro-rata), exactly as SAS writes it:
    -- ANNUAL_PREMIUM / 12 * min(12, intck('month',
    --     max(EFFECTIVE_DATE, year start), min(val_date, EXPIRATION_DATE)))
    -- SAS max()/min() ignore missing, hence the *_IGNORE_NULLS forms.
    p.ANNUAL_PREMIUM / 12 *
        LEAST(12, DATEDIFF('month',
            GREATEST_IGNORE_NULLS(p.EFFECTIVE_DATE, $ytd_start),
            LEAST_IGNORE_NULLS($val_date, p.EXPIRATION_DATE)))
                                                        AS YTD_EARNED_PREMIUM
FROM RAW_INS.POLICIES p
WHERE p.STATUS = 'ACTIVE'
  -- SAS: a missing EFFECTIVE_DATE compares <= any date, so it passes.
  AND (p.EFFECTIVE_DATE <= $val_date OR p.EFFECTIVE_DATE IS NULL)
  -- SAS: a missing EXPIRATION_DATE compares < val_date, so it is dropped
  -- (NULL >= x is not TRUE in Snowflake either).
  AND p.EXPIRATION_DATE >= $val_date
  -- %if &lob ne ALL %then and p.POLICY_TYPE = "&lob"
  AND ($lob = 'ALL' OR p.POLICY_TYPE = $lob)
ORDER BY p.POLICY_ID;

/* ----------------------------------------------------------
   Step 2: Claims Experience ("12-month" window)   (policy_valuation.sas:78-97)
   Window is intnx('month', val_date, -12) .. val_date, i.e. from the
   FIRST day of the month 12 months back: 13 calendar months inclusive.
   ---------------------------------------------------------- */
CREATE OR REPLACE TEMPORARY TABLE WORK_CLAIMS_EXP AS
SELECT
    c.POLICY_ID,
    COUNT(DISTINCT c.CLAIM_ID)                          AS NUM_CLAIMS,
    SUM(c.INCURRED_AMOUNT)                              AS TOTAL_INCURRED,
    SUM(c.PAID_AMOUNT)                                  AS TOTAL_PAID,
    SUM(c.RESERVED_AMOUNT)                              AS TOTAL_RESERVED,
    MAX(c.LOSS_DATE)                                    AS LAST_CLAIM_DATE,
    SUM(CASE WHEN c.CLAIM_STATUS IN ('OPEN', 'INV', 'ADJ', 'PEND')
             THEN c.RESERVED_AMOUNT ELSE 0 END)         AS OPEN_RESERVES,
    SUM(CASE WHEN c.CLAIM_STATUS = 'DENY' THEN 1 ELSE 0 END)
                                                        AS DENIED_CLAIMS
FROM RAW_INS.CLAIMS c
WHERE c.LOSS_DATE >= $claims_from
  AND c.LOSS_DATE <= $val_date
GROUP BY c.POLICY_ID;

/* ----------------------------------------------------------
   Step 3: Premium Collections (calendar year to date)   (policy_valuation.sas:102-118)
   ---------------------------------------------------------- */
CREATE OR REPLACE TEMPORARY TABLE WORK_PREMIUM_COLL AS
SELECT
    POLICY_ID,
    SUM(PREMIUM_AMOUNT)                                 AS COLLECTED_PREMIUM,
    SUM(CASE WHEN PAYMENT_STATUS = 'RETURNED'
             THEN PREMIUM_AMOUNT ELSE 0 END)            AS RETURNED_PREMIUM,
    MAX(PAYMENT_DATE)                                   AS LAST_PAYMENT_DATE,
    COUNT(CASE WHEN PAYMENT_STATUS = 'LATE' THEN 1 END) AS LATE_PAYMENTS
FROM RAW_INS.PREMIUMS
WHERE PAYMENT_DATE >= $ytd_start
  AND PAYMENT_DATE <= $val_date
GROUP BY POLICY_ID;

/* ----------------------------------------------------------
   Step 4: Merge and Calculate Valuation Metrics   (policy_valuation.sas:123-164)
   MERGE INFORCE(in=a) CLAIMS_EXP(in=b) PREMIUM_COLL(in=c); BY POLICY_ID; if a;
   CLAIMS_EXP and PREMIUM_COLL are GROUP BY results (one row per POLICY_ID),
   so the MERGE is exactly a LEFT JOIN from INFORCE. Duplicate POLICY_IDs in
   INFORCE (if RAW_INS.POLICIES had them) stay duplicated, as in SAS.
   The DATA step assigns LOSS_RATIO -> COMBINED_RATIO -> PREMIUM_ADEQUATE ->
   IBNR_ESTIMATE -> TOTAL_RESERVE in sequence; the CTE chain mirrors it.
   ---------------------------------------------------------- */
CREATE OR REPLACE TABLE STG_INS.POLICY_VALUATION AS
WITH merged AS (
    SELECT
        a.POLICY_ID,
        a.CUSTOMER_ID,
        a.POLICY_TYPE,
        a.EFFECTIVE_DATE,
        a.EXPIRATION_DATE,
        a.ANNUAL_PREMIUM,
        a.SUM_INSURED,
        a.DEDUCTIBLE,
        a.RISK_CATEGORY,
        a.UNDERWRITING_CLASS,
        a.AGENT_ID,
        a.BRANCH_CODE,
        a.POLICY_AGE_MONTHS,
        a.MONTHS_TO_EXPIRY,
        a.RENEWAL_DUE_FLAG,
        a.YTD_EARNED_PREMIUM,
        b.NUM_CLAIMS,
        b.TOTAL_INCURRED,
        b.TOTAL_PAID,
        b.TOTAL_RESERVED,
        b.LAST_CLAIM_DATE,
        b.OPEN_RESERVES,
        b.DENIED_CLAIMS,
        c.COLLECTED_PREMIUM,
        c.RETURNED_PREMIUM,
        c.LAST_PAYMENT_DATE,
        c.LATE_PAYMENTS
    FROM WORK_INFORCE a
    LEFT JOIN WORK_CLAIMS_EXP   b ON b.POLICY_ID = a.POLICY_ID
    LEFT JOIN WORK_PREMIUM_COLL c ON c.POLICY_ID = a.POLICY_ID
),
ratios AS (
    SELECT
        m.*,
        -- if YTD_EARNED_PREMIUM > 0 then LOSS_RATIO = coalesce(TOTAL_INCURRED,0)/YTD_EARNED_PREMIUM; else .;
        CASE WHEN m.YTD_EARNED_PREMIUM > 0
             THEN COALESCE(m.TOTAL_INCURRED, 0) / m.YTD_EARNED_PREMIUM
        END                                             AS LOSS_RATIO
    FROM merged m
),
combined AS (
    SELECT
        r.*,
        -- if YTD_EARNED_PREMIUM > 0 then COMBINED_RATIO = LOSS_RATIO + 0.30; else .;
        CASE WHEN r.YTD_EARNED_PREMIUM > 0
             THEN r.LOSS_RATIO + 0.30
        END                                             AS COMBINED_RATIO
    FROM ratios r
)
SELECT
    x.*,
    -- if COMBINED_RATIO = . then 'N'; else if COMBINED_RATIO > 1.0 then 'N'; else 'Y';
    CASE
        WHEN x.COMBINED_RATIO IS NULL THEN 'N'
        WHEN x.COMBINED_RATIO > 1.0   THEN 'N'
        ELSE 'Y'
    END                                                 AS PREMIUM_ADEQUATE,
    -- IBNR_ESTIMATE = max(0, YTD_EARNED_PREMIUM * 0.15 - coalesce(TOTAL_PAID, 0));
    -- SAS max() ignores missing: a missing earned premium yields 0, not missing.
    GREATEST_IGNORE_NULLS(0, x.YTD_EARNED_PREMIUM * 0.15 - COALESCE(x.TOTAL_PAID, 0))
                                                        AS IBNR_ESTIMATE,
    -- TOTAL_RESERVE = coalesce(OPEN_RESERVES, 0) + IBNR_ESTIMATE;
    COALESCE(x.OPEN_RESERVES, 0)
        + GREATEST_IGNORE_NULLS(0, x.YTD_EARNED_PREMIUM * 0.15 - COALESCE(x.TOTAL_PAID, 0))
                                                        AS TOTAL_RESERVE,
    $val_date                                           AS VALUATION_DATE
FROM combined x
ORDER BY x.POLICY_ID;

/* ----------------------------------------------------------
   Step 5: Loss Ratio Summary by Line of Business   (policy_valuation.sas:169-193)
   PROC MEANS noprint nway; CLASS POLICY_TYPE; VAR YTD_EARNED_PREMIUM
   TOTAL_INCURRED TOTAL_PAID TOTAL_RESERVE IBNR_ESTIMATE;
     - rows whose CLASS value is missing (blank POLICY_TYPE) are excluded
       (PROC MEANS default, no MISSING option)
     - n=N_POLICIES names a single statistic, so it is N of the FIRST
       analysis variable (YTD_EARNED_PREMIUM): non-missing count, not row count
     - sum() of an all-missing group is missing (NULL), as SUM() in SQL
   Second DATA step: AGG_LOSS_RATIO / AGG_COMBINED_RATIO only when TOTAL_EARNED > 0.
   ---------------------------------------------------------- */
CREATE OR REPLACE TABLE REPORTS.LOSS_RATIO_SUMMARY AS
WITH means AS (
    SELECT
        POLICY_TYPE,
        COUNT(YTD_EARNED_PREMIUM)                       AS N_POLICIES,
        SUM(YTD_EARNED_PREMIUM)                         AS TOTAL_EARNED,
        SUM(TOTAL_INCURRED)                             AS TOTAL_INCURRED,
        SUM(TOTAL_PAID)                                 AS TOTAL_PAID,
        SUM(TOTAL_RESERVE)                              AS TOTAL_RESERVES,
        SUM(IBNR_ESTIMATE)                              AS TOTAL_IBNR
    FROM STG_INS.POLICY_VALUATION
    WHERE POLICY_TYPE IS NOT NULL
      AND POLICY_TYPE <> ''        -- SAS character missing is blank
    GROUP BY POLICY_TYPE
)
SELECT
    POLICY_TYPE,
    N_POLICIES,
    TOTAL_EARNED,
    TOTAL_INCURRED,
    TOTAL_PAID,
    TOTAL_RESERVES,
    TOTAL_IBNR,
    CASE WHEN TOTAL_EARNED > 0 THEN TOTAL_INCURRED / TOTAL_EARNED        END AS AGG_LOSS_RATIO,
    CASE WHEN TOTAL_EARNED > 0 THEN TOTAL_INCURRED / TOTAL_EARNED + 0.30 END AS AGG_COMBINED_RATIO
FROM means
ORDER BY POLICY_TYPE;

/* proc datasets lib=WORK nolist; delete INFORCE CLAIMS_EXP PREMIUM_COLL; */
DROP TABLE IF EXISTS WORK_INFORCE;
DROP TABLE IF EXISTS WORK_CLAIMS_EXP;
DROP TABLE IF EXISTS WORK_PREMIUM_COLL;
