/*=====================================================================
  JOB02_DAILY_TRANSACTIONS.sql — Snowflake conversion of
  daily_transaction_processing.sas

  Source: ts-sas-legacy-analytics/Programs/Banking/daily_transaction_processing.sas
  SAS schedule: Daily 07:30 via Control-M BANK_DAILY_02 (after BANK_DAILY_01)
  Snowflake: Runs as TASK_JOB02_DAILY_TRANSACTIONS (see orchestration/tasks.sql)

  Inputs:  RAW.TXN_FEED_<YYYYMMDD>      (SAS RAW_BANK.TXN_FEED_yyyymmdd, one table per day)
           STAGING.CUST_ACCOUNTS_DAILY  (SAS STG_BANK.CUST_ACCOUNTS_DAILY, built by JOB01)
           CURATED.DAILY_TRANSACTIONS   (90-day history, read BEFORE today's rows are appended)
  Outputs: CURATED.DAILY_TRANSACTIONS   (append, SAS PROC APPEND ... FORCE)
           CURATED.TXN_ANOMALIES        (append, only when >0 anomalies)
           CURATED.RUNNING_BALANCES     (full replace, SAS DATA step)

  Why Snowflake SQL and not Snowpark:
    Every step of the SAS program is set-based once the two procedural
    constructs are translated: the multi-output DATA step is a CASE that
    derives REJECT_REASON (first failing rule wins, exactly like the
    if/return chain), and RETAIN RUNNING_BALANCE with BY ACCOUNT_ID is a
    window SUM() OVER (PARTITION BY ACCOUNT_ID ORDER BY ... ROWS UNBOUNDED
    PRECEDING). No hash objects, no row-dependent branching that a window
    cannot express. This also matches the existing task wiring, which
    EXECUTEs this file from @SQL_STAGE.

  Construct mapping (SAS line -> Snowflake):
    %macro / %parmv (19-21)          -> session variable $run_date, validated by the caller
    %let txn_ds = TXN_FEED_yyyymmdd (25) -> SET txn_feed_table = ... ; IDENTIFIER($txn_feed_table)
    %sysfunc(exist()) guard (36)     -> IDENTIFIER() raises "object does not exist"; task fails = %ABORT
    DATA step, 2 outputs (45-97)     -> TEMP TXN_SCREENED with CASE REJECT_REASON; two filtered temps
    put(x, dollar18.2) (74)          -> TRIM(TO_CHAR(x, '$999,999,999,999.99'))
    put(d, date9.) (89)              -> UPPER(TO_CHAR(d, 'DDMONYYYY'))
    PROC SQL left join (105-130)     -> TEMP TXN_ENRICHED (same CASE for POST_TXN_BALANCE)
    RETAIN + BY + first. (137-154)   -> SUM(delta) OVER (PARTITION BY ACCOUNT_ID ORDER BY ... ROWS UNBOUNDED PRECEDING)
    mean()/std()/intnx() (159-169)   -> AVG()/STDDEV_SAMP()/DATEADD('day', -90, $run_date)
    calculated Z_SCORE (184)         -> CTE z then outer CASE
    having ANOMALY_TYPE ne '' (195)  -> outer SELECT ... WHERE ANOMALY_TYPE <> ''
    %lock / unlock (205, 211)        -> BEGIN ... COMMIT transaction (Snowflake is ACID; no dataset lock)
    PROC APPEND ... FORCE (207-209)  -> INSERT INTO <explicit base-table column list>
    %if &nobs_anom > 0 (213)         -> Snowflake Scripting IF inside EXECUTE IMMEDIATE $$ ... $$
    DATA CURATED.RUNNING_BALANCES keep= (222-225) -> CREATE OR REPLACE TABLE ... AS SELECT 4 columns
    proc datasets delete WORK.* (240-242) -> DROP TABLE IF EXISTS <temps>
    format dollar18.2 / $TXNCAT. etc.  -> none (presentation only; raw codes stored)

  Legacy quirks reproduced on purpose (do NOT "fix" without a business decision):
    Q1 PROC APPEND FORCE drops every column the base table lacks. The history
       table has the 10 feed columns, so RUNNING_BALANCE, PRE/POST_TXN_BALANCE,
       CUSTOMER_ID, ACCOUNT_TYPE, ... never land in CURATED.DAILY_TRANSACTIONS.
       REJECT_REASON (always blank for valid rows) is dropped the same way.
    Q2 SAS orders missing below every number: `. < 0` is TRUE and
       `abs(x) > . * 0.9` is TRUE. An account absent from the snapshot
       (PRE_TXN_BALANCE missing) is therefore tagged OVERDRAFT, never
       ORPHAN_ACCOUNT -- the ORPHAN_ACCOUNT branch is effectively unreachable.
       Reproduced with explicit IS NULL OR ... predicates.
    Q3 Anomaly precedence is HIGH_AMOUNT > OVERDRAFT > LARGE_WITHDRAWAL >
       ORPHAN_ACCOUNT; one transaction gets one type even if several apply.
    Q4 Z_SCORE uses the 90-day history BEFORE today's feed is appended, is
       missing when STD_TXN_AMT is missing/zero (fewer than 2 history rows
       or identical amounts), and compares |amount| against mean(|amount|).
    Q5 RUNNING_BALANCE resets per account to PRE_TXN_BALANCE and carries
       across TRANSACTION_DATEs within a feed; POST_TXN_BALANCE (per-row,
       from the snapshot balance) is a different number and both are kept.
    Q6 TRF and ADJ are added with their sign; WDR/PMT/FEE/CHG are subtracted
       as abs(); a positive-signed WDR and a negative-signed WDR both reduce
       the balance.
    Q7 CURATED.RUNNING_BALANCES is replaced every run (DATA step, not APPEND):
       only the latest business date survives.
    Q8 Transactions with a missing TRANSACTION_DATE pass the "Future dated"
       test (missing > date is FALSE in SAS; NULL > date is not TRUE here).
    Q9 TXN_REJECTED is a WORK dataset in SAS -- it is written to the log and
       discarded. Kept here as a TEMPORARY table only (visible in the task
       session for debugging, not persisted).
    Q10 PROC APPEND creates CURATED.TXN_ANOMALIES on first use with the full
       28-column anomaly shape, but only when at least one anomaly exists.
=====================================================================*/

-- Session variables (set by the orchestrator task or caller)
-- SET run_date = CURRENT_DATE();
SET txn_feed_table = 'RAW.TXN_FEED_' || TO_CHAR($run_date, 'YYYYMMDD');

/* ----------------------------------------------------------
   Step 1: Validate and Clean  (SAS lines 45-97)
   First failing rule wins, as in the if/output/return chain.
   ---------------------------------------------------------- */
CREATE OR REPLACE TEMPORARY TABLE TXN_SCREENED AS
SELECT
    f.*,
    CASE
        WHEN f.TRANSACTION_ID IS NULL                       THEN 'Missing TRANSACTION_ID'
        WHEN f.ACCOUNT_ID IS NULL                           THEN 'Missing ACCOUNT_ID'
        WHEN f.TRANSACTION_AMOUNT IS NULL                   THEN 'Missing TRANSACTION_AMOUNT'
        WHEN ABS(f.TRANSACTION_AMOUNT) > 10000000           THEN 'Amount exceeds threshold: '
                                                              || TRIM(TO_CHAR(f.TRANSACTION_AMOUNT, '$999,999,999,999.99'))
        WHEN f.TRANSACTION_TYPE IS NULL
          OR f.TRANSACTION_TYPE NOT IN ('DEP','WDR','TRF','PMT','FEE','INT','ADJ','REV','CHG','REF')
                                                            THEN TRIM('Invalid transaction type: ' || COALESCE(f.TRANSACTION_TYPE, ''))
        WHEN f.TRANSACTION_DATE > $run_date                 THEN 'Future dated: '
                                                              || UPPER(TO_CHAR(f.TRANSACTION_DATE, 'DDMONYYYY'))
        ELSE NULL
    END AS REJECT_REASON
FROM IDENTIFIER($txn_feed_table) f;

CREATE OR REPLACE TEMPORARY TABLE TXN_REJECTED AS
SELECT * FROM TXN_SCREENED WHERE REJECT_REASON IS NOT NULL;

CREATE OR REPLACE TEMPORARY TABLE TXN_VALIDATED AS
SELECT * EXCLUDE REJECT_REASON FROM TXN_SCREENED WHERE REJECT_REASON IS NULL;

/* ----------------------------------------------------------
   Step 2: Enrich with Account and Customer Data  (SAS lines 105-130)
   ---------------------------------------------------------- */
CREATE OR REPLACE TEMPORARY TABLE TXN_ENRICHED AS
SELECT
    t.*,
    a.ACCOUNT_TYPE,
    a.CUSTOMER_ID,
    a.CUSTOMER_SEGMENT,
    a.REGION_CODE,
    a.BRANCH_ID,
    a.CURRENT_BALANCE AS PRE_TXN_BALANCE,
    CASE
        WHEN t.TRANSACTION_TYPE IN ('DEP','INT','REF','REV')
            THEN a.CURRENT_BALANCE + t.TRANSACTION_AMOUNT
        WHEN t.TRANSACTION_TYPE IN ('WDR','PMT','FEE','CHG')
            THEN a.CURRENT_BALANCE - ABS(t.TRANSACTION_AMOUNT)
        WHEN t.TRANSACTION_TYPE IN ('TRF','ADJ')
            THEN a.CURRENT_BALANCE + t.TRANSACTION_AMOUNT
        ELSE a.CURRENT_BALANCE
    END AS POST_TXN_BALANCE,
    a.RISK_RATING
FROM TXN_VALIDATED t
LEFT JOIN STAGING.CUST_ACCOUNTS_DAILY a
       ON t.ACCOUNT_ID = a.ACCOUNT_ID;

/* ----------------------------------------------------------
   Step 3: Running balance  (SAS lines 137-154, RETAIN + BY)
   first.ACCOUNT_ID resets to PRE_TXN_BALANCE; the window sum replays
   every prior row of the same account in BY order.
   NULL + x = NULL mirrors SAS missing arithmetic (Q2, Q5).
   ---------------------------------------------------------- */
CREATE OR REPLACE TEMPORARY TABLE TXN_WITH_BALANCE AS
SELECT
    e.*,
    e.PRE_TXN_BALANCE
      + SUM(
            CASE
                WHEN e.TRANSACTION_TYPE IN ('DEP','INT','REF','REV') THEN e.TRANSACTION_AMOUNT
                WHEN e.TRANSACTION_TYPE IN ('WDR','PMT','FEE','CHG') THEN -ABS(e.TRANSACTION_AMOUNT)
                WHEN e.TRANSACTION_TYPE IN ('TRF','ADJ')             THEN e.TRANSACTION_AMOUNT
                ELSE 0
            END
        ) OVER (PARTITION BY e.ACCOUNT_ID
                ORDER BY e.TRANSACTION_DATE, e.TRANSACTION_ID
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS RUNNING_BALANCE
FROM TXN_ENRICHED e;

/* ----------------------------------------------------------
   Step 4: Anomaly detection  (SAS lines 159-199)
   Stats come from the history table BEFORE today's append (Q4).
   ---------------------------------------------------------- */
CREATE OR REPLACE TEMPORARY TABLE TXN_STATS AS
SELECT
    ACCOUNT_ID,
    AVG(ABS(TRANSACTION_AMOUNT))         AS AVG_TXN_AMT,
    STDDEV_SAMP(ABS(TRANSACTION_AMOUNT)) AS STD_TXN_AMT,
    COUNT(*)                             AS TXN_COUNT
FROM CURATED.DAILY_TRANSACTIONS
WHERE TRANSACTION_DATE >= DATEADD('day', -90, $run_date)
GROUP BY ACCOUNT_ID;

CREATE OR REPLACE TEMPORARY TABLE TXN_ANOMALIES AS
WITH z AS (
    SELECT
        e.*,
        s.AVG_TXN_AMT,
        s.STD_TXN_AMT,
        CASE
            WHEN s.STD_TXN_AMT > 0
                THEN (ABS(e.TRANSACTION_AMOUNT) - s.AVG_TXN_AMT) / s.STD_TXN_AMT
            ELSE NULL
        END AS Z_SCORE
    FROM TXN_WITH_BALANCE e
    LEFT JOIN TXN_STATS s
           ON e.ACCOUNT_ID = s.ACCOUNT_ID
)
SELECT * FROM (
SELECT
    z.*,
    CASE
        WHEN z.Z_SCORE > 3                                              THEN 'HIGH_AMOUNT'
        -- SAS: missing < 0 is TRUE  (Q2)
        WHEN z.RUNNING_BALANCE IS NULL OR z.RUNNING_BALANCE < 0         THEN 'OVERDRAFT'
        -- SAS: abs(x) > missing * 0.9 is TRUE  (Q2)
        WHEN z.TRANSACTION_TYPE = 'WDR'
         AND (z.PRE_TXN_BALANCE IS NULL
              OR ABS(z.TRANSACTION_AMOUNT) > z.PRE_TXN_BALANCE * 0.9)   THEN 'LARGE_WITHDRAWAL'
        WHEN z.CUSTOMER_ID IS NULL                                      THEN 'ORPHAN_ACCOUNT'
        ELSE ''
    END::VARCHAR(20) AS ANOMALY_TYPE
FROM z
)
WHERE ANOMALY_TYPE <> '';   -- SAS having on a non-grouped query = row filter on the calculated column

/* ----------------------------------------------------------
   Step 5: Persist  (SAS lines 202-225)
   ---------------------------------------------------------- */
BEGIN TRANSACTION;                                   -- %lock(CURATED.DAILY_TRANSACTIONS)

-- PROC APPEND ... FORCE: only the base table's columns survive (Q1)
INSERT INTO CURATED.DAILY_TRANSACTIONS
    (TRANSACTION_ID, ACCOUNT_ID, TRANSACTION_DATE, TRANSACTION_TYPE, TRANSACTION_AMOUNT,
     CHANNEL, MERCHANT_CATEGORY, DESCRIPTION, POST_DATE, CURRENCY_CODE)
SELECT
     TRANSACTION_ID, ACCOUNT_ID, TRANSACTION_DATE, TRANSACTION_TYPE, TRANSACTION_AMOUNT,
     CHANNEL, MERCHANT_CATEGORY, DESCRIPTION, POST_DATE, CURRENCY_CODE
FROM TXN_WITH_BALANCE;

COMMIT;                                              -- %lock(..., unlock)

-- %if &nobs_anom > 0 %then proc append base=CURATED.TXN_ANOMALIES ... force  (Q10)
EXECUTE IMMEDIATE $$
BEGIN
    IF ((SELECT COUNT(*) FROM TXN_ANOMALIES) > 0) THEN
        CREATE TABLE IF NOT EXISTS CURATED.TXN_ANOMALIES LIKE TXN_ANOMALIES;
        INSERT INTO CURATED.TXN_ANOMALIES SELECT * FROM TXN_ANOMALIES;
    END IF;
END;
$$;

-- DATA CURATED.RUNNING_BALANCES; keep ACCOUNT_ID TRANSACTION_DATE TRANSACTION_ID RUNNING_BALANCE  (Q7)
CREATE OR REPLACE TABLE CURATED.RUNNING_BALANCES AS
SELECT ACCOUNT_ID, TRANSACTION_DATE, TRANSACTION_ID, RUNNING_BALANCE
FROM TXN_WITH_BALANCE;

/* ----------------------------------------------------------
   Cleanup  (SAS lines 240-242, proc datasets delete)
   ---------------------------------------------------------- */
DROP TABLE IF EXISTS TXN_SCREENED;
DROP TABLE IF EXISTS TXN_VALIDATED;
DROP TABLE IF EXISTS TXN_REJECTED;
DROP TABLE IF EXISTS TXN_ENRICHED;
DROP TABLE IF EXISTS TXN_WITH_BALANCE;
DROP TABLE IF EXISTS TXN_STATS;
DROP TABLE IF EXISTS TXN_ANOMALIES;
