"""
JOB02_daily_transaction_processing.py — Snowpark Python conversion of
daily_transaction_processing.sas (Banking daily transaction ETL)

Source: ts-sas-legacy-analytics/Programs/Banking/daily_transaction_processing.sas
SAS schedule: BatchJobs/run_daily_banking.sas step 2 (Control-M BANK_MASTER,
              program header says BANK_DAILY_02 07:30)
Snowflake: Runs as a Snowpark stored procedure via Snowflake Task
           TASK_JOB02_DAILY_TRANSACTIONS (orchestration/tasks.sql)

Why Snowpark (not SQL):
  The program is a procedural DATA-step pipeline: a multi-output DATA step
  with `output ...; return;` reject routing (lines 45-97), a BY-group DATA
  step with RETAIN and first.ACCOUNT_ID (lines 137-154), two PROC APPEND
  steps whose FORCE semantics depend on the *existing* base table's column
  list (lines 207-217), and a %nobs-guarded conditional append (line 213).
  Snowpark keeps each SAS step as one named DataFrame so the step order
  (stats BEFORE the append) and the reject routing stay visible, while the
  set-based pieces (PROC SQL joins, window running balance) still run as
  Snowflake SQL.

Inputs:  RAW_BANK.TXN_FEED_<YYYYMMDD>, STG_BANK.CUST_ACCOUNTS_DAILY,
         CURATED.DAILY_TRANSACTIONS (90-day history, read BEFORE the append)
Outputs: CURATED.DAILY_TRANSACTIONS (append), CURATED.TXN_ANOMALIES (append,
         only when anomalies > 0), CURATED.RUNNING_BALANCES (replace)

Legacy quirks reproduced on purpose (see docs/JOB02_daily_transaction_processing.md):
  Q1  PROC APPEND ... FORCE onto the 10-column CURATED.DAILY_TRANSACTIONS
      drops every enrichment column (CUSTOMER_ID, balances, RUNNING_BALANCE).
  Q2  SAS missing < any number: a missing RUNNING_BALANCE (orphan account)
      is classified OVERDRAFT, so ORPHAN_ACCOUNT is unreachable for orphans.
  Q3  Z-score baseline is the history BEFORE today's rows are appended.
  Q4  POST_DATE is never validated (future post dates pass).
  Q5  Negative amounts: DEP/INT/REF/REV/TRF/ADJ add the signed amount,
      WDR/PMT/FEE/CHG subtract abs(amount).
  Q6  LARGE_WITHDRAWAL compares against the pre-day balance, after OVERDRAFT.
  Q7  Rejected rows (WORK.TXN_REJECTED) are counted and then deleted.
"""
from datetime import date, timedelta

from snowflake.snowpark import DataFrame, Session, Window
from snowflake.snowpark.exceptions import SnowparkSQLException
from snowflake.snowpark.functions import (
    abs as sf_abs,
)
from snowflake.snowpark.functions import (
    avg,
    coalesce,
    col,
    concat_ws,
    count,
    first_value,
    lit,
    stddev,
    to_char,
    trim,
    upper,
    when,
)
from snowflake.snowpark.functions import (
    sum as sf_sum,
)
from snowflake.snowpark.types import StringType

VALID_TXN_TYPES = ["DEP", "WDR", "TRF", "PMT", "FEE", "INT", "ADJ", "REV", "CHG", "REF"]
CREDIT_TYPES = ["DEP", "INT", "REF", "REV"]      # balance + amount
DEBIT_TYPES = ["WDR", "PMT", "FEE", "CHG"]       # balance - abs(amount)
TRANSFER_TYPES = ["TRF", "ADJ"]                  # balance + amount
AMOUNT_THRESHOLD = 10000000
ZSCORE_THRESHOLD = 3
LARGE_WITHDRAWAL_PCT = 0.9
HISTORY_DAYS = 90

RUNNING_BALANCE_KEEP = ["TRANSACTION_ID", "ACCOUNT_ID", "TRANSACTION_DATE", "RUNNING_BALANCE"]


def _is_missing_char(column):
    """SAS missing() on a character variable: blank (or NULL in Snowflake)."""
    return coalesce(trim(col(column)), lit("")) == lit("")


def _table_exists(session: Session, table: str) -> bool:
    try:
        _ = session.table(table).schema
    except SnowparkSQLException:
        return False
    return True


def _table_columns(session: Session, table: str) -> list:
    return [f.name.strip('"') for f in session.table(table).schema.fields]


def proc_append_force(session: Session, df: DataFrame, baseTable: str) -> None:
    """SAS `proc append base=BASE data=DF force;`.

    If BASE does not exist SAS creates it from DF (all columns).  If it does,
    FORCE appends only the columns BASE already has: extra DF columns are
    dropped (with a WARNING in the SAS log) and BASE columns missing from DF
    are filled with missing values.
    """
    if not _table_exists(session, baseTable):
        df.write.mode("append").save_as_table(baseTable)
        return
    baseColumns = _table_columns(session, baseTable)
    dfColumns = {c.strip('"').upper() for c in df.columns}
    projected = df.select(
        [col(c) if c.upper() in dfColumns else lit(None).alias(c) for c in baseColumns]
    )
    projected.write.mode("append").save_as_table(baseTable, column_order="name")


def daily_transaction_processing(
    session: Session,
    txnDate: str,
    rawBank: str = "RAW_BANK",
    stgBank: str = "STG_BANK",
    curated: str = "CURATED",
) -> str:
    """Validate, enrich, running-balance, anomaly-detect and load one day's feed.

    Args:
        session: Active Snowpark session.
        txnDate: Transaction date 'YYYY-MM-DD' (SAS &CURR_DT, e.g. 31JAN2024).
        rawBank / stgBank / curated: schema names for the SAS librefs
            RAW_BANK / STG_BANK / CURATED (overridable so a dev run can use
            a private namespace).

    Returns:
        Summary string mirroring the SAS completion NOTE.
    """
    txnDt = date.fromisoformat(txnDate)
    feedTable = f"{rawBank}.TXN_FEED_{txnDt.strftime('%Y%m%d')}"       # SAS line 25
    accountsTable = f"{stgBank}.CUST_ACCOUNTS_DAILY"
    dailyTxnTable = f"{curated}.DAILY_TRANSACTIONS"
    anomaliesTable = f"{curated}.TXN_ANOMALIES"
    runningBalTable = f"{curated}.RUNNING_BALANCES"

    # ------------------------------------------------------------------
    # Step 1: Validate Incoming Feed  (SAS lines 36-97)
    # ------------------------------------------------------------------
    if not _table_exists(session, feedTable):                               # %goto ABORT
        return f"ERROR: Feed dataset {feedTable} not found"

    feed = session.table(feedTable)
    nobsFeed = feed.count()                                                 # %nobs

    # The DATA step tests the rules in this order and `return`s on the first
    # hit, so REJECT_REASON is the FIRST failing rule.  REJECT_REASON is
    # length $200; it is dropped from TXN_VALIDATED.
    rejectReason = (
        when(_is_missing_char("TRANSACTION_ID"), lit("Missing TRANSACTION_ID"))
        .when(_is_missing_char("ACCOUNT_ID"), lit("Missing ACCOUNT_ID"))
        .when(col("TRANSACTION_AMOUNT").is_null(), lit("Missing TRANSACTION_AMOUNT"))
        .when(
            sf_abs(col("TRANSACTION_AMOUNT")) > AMOUNT_THRESHOLD,
            concat_ws(
                lit(" "),
                lit("Amount exceeds threshold:"),
                trim(to_char(col("TRANSACTION_AMOUNT"), "$999,999,999,999,999.00")),  # dollar18.2
            ),
        )
        .when(
            # SAS: missing NOT IN (...) is TRUE, so a NULL type is rejected too
            col("TRANSACTION_TYPE").is_null() | ~col("TRANSACTION_TYPE").isin(VALID_TXN_TYPES),
            concat_ws(lit(" "), lit("Invalid transaction type:"), trim(col("TRANSACTION_TYPE"))),
        )
        .when(
            col("TRANSACTION_DATE") > lit(txnDt),                          # Q4: POST_DATE not checked
            concat_ws(
                lit(" "),
                lit("Future dated:"),
                upper(to_char(col("TRANSACTION_DATE"), "DDMONYYYY")),        # date9.
            ),
        )
        .otherwise(lit(None).cast(StringType(200)))
    )
    feedChecked = feed.with_column("REJECT_REASON", rejectReason)
    txnRejected = feedChecked.filter(col("REJECT_REASON").is_not_null())    # WORK.TXN_REJECTED
    txnValidated = feedChecked.filter(col("REJECT_REASON").is_null()).drop("REJECT_REASON")

    nobsValid = txnValidated.count()
    nobsRejected = txnRejected.count()                                      # Q7: counted, never persisted

    # ------------------------------------------------------------------
    # Step 2: Enrich with Account and Customer Data  (SAS lines 105-130)
    # ------------------------------------------------------------------
    # `t.ACCOUNT_ID = a.ACCOUNT_ID`: the account-side key is renamed so the
    # joined frame keeps exactly one ACCOUNT_ID (t.*), as the PROC SQL does.
    accounts = session.table(accountsTable).select(
        col("ACCOUNT_ID").alias("A_ACCOUNT_ID"),
        "ACCOUNT_TYPE",
        "CUSTOMER_ID",
        "CUSTOMER_SEGMENT",
        "REGION_CODE",
        "BRANCH_ID",
        col("CURRENT_BALANCE").alias("PRE_TXN_BALANCE"),
        "RISK_RATING",
    )
    amount = col("TRANSACTION_AMOUNT")
    postTxnBalance = (
        when(col("TRANSACTION_TYPE").isin(CREDIT_TYPES), col("PRE_TXN_BALANCE") + amount)
        .when(col("TRANSACTION_TYPE").isin(DEBIT_TYPES), col("PRE_TXN_BALANCE") - sf_abs(amount))
        .when(col("TRANSACTION_TYPE").isin(TRANSFER_TYPES), col("PRE_TXN_BALANCE") + amount)
        .otherwise(col("PRE_TXN_BALANCE"))
    )
    txnEnriched = (
        txnValidated.join(accounts, col("ACCOUNT_ID") == col("A_ACCOUNT_ID"), how="left")
        .select(
            txnValidated.columns                                            # t.*
            + [
                "ACCOUNT_TYPE",
                "CUSTOMER_ID",
                "CUSTOMER_SEGMENT",
                "REGION_CODE",
                "BRANCH_ID",
                "PRE_TXN_BALANCE",
                postTxnBalance.alias("POST_TXN_BALANCE"),
                "RISK_RATING",
            ]
        )
    )

    # ------------------------------------------------------------------
    # Step 3: Running Balance Calculation  (SAS lines 137-154)
    # BY ACCOUNT_ID TRANSACTION_DATE TRANSACTION_ID; RETAIN RUNNING_BALANCE;
    # first.ACCOUNT_ID seeds with PRE_TXN_BALANCE, each row then adds its
    # signed delta.  A missing seed (orphan account) stays missing for the
    # whole group — SAS missing arithmetic — hence first_value(...) + sum(),
    # never a NULL-skipping sum on its own.
    # ------------------------------------------------------------------
    byGroup = Window.partition_by("ACCOUNT_ID").order_by("TRANSACTION_DATE", "TRANSACTION_ID")
    runningWindow = byGroup.rows_between(Window.UNBOUNDED_PRECEDING, Window.CURRENT_ROW)
    balanceDelta = (
        when(col("TRANSACTION_TYPE").isin(CREDIT_TYPES), col("TRANSACTION_AMOUNT"))
        .when(col("TRANSACTION_TYPE").isin(DEBIT_TYPES), -sf_abs(col("TRANSACTION_AMOUNT")))  # Q5
        .when(col("TRANSACTION_TYPE").isin(TRANSFER_TYPES), col("TRANSACTION_AMOUNT"))
        .otherwise(lit(0))                                                  # RUNNING_BALANCE unchanged
    )
    txnWithBalance = txnEnriched.with_column(
        "RUNNING_BALANCE",
        first_value(col("PRE_TXN_BALANCE")).over(runningWindow)
        + sf_sum(balanceDelta).over(runningWindow),
    ).cache_result()                                                        # WORK.TXN_WITH_BALANCE

    # ------------------------------------------------------------------
    # Step 4: Anomaly Detection  (SAS lines 159-197)
    # TXN_STATS reads CURATED.DAILY_TRANSACTIONS BEFORE today's append (Q3).
    # SAS mean() ignores missing, std() is the sample std dev (missing for a
    # single observation) — AVG / STDDEV (sample) behave the same.
    # ------------------------------------------------------------------
    historyFrom = txnDt - timedelta(days=HISTORY_DAYS)                      # intnx('day', d, -90)
    txnStats = (
        session.table(dailyTxnTable)
        .filter(col("TRANSACTION_DATE") >= lit(historyFrom))
        .group_by("ACCOUNT_ID")
        .agg(
            avg(sf_abs(col("TRANSACTION_AMOUNT"))).alias("AVG_TXN_AMT"),
            stddev(sf_abs(col("TRANSACTION_AMOUNT"))).alias("STD_TXN_AMT"),
            count(lit(1)).alias("TXN_COUNT"),
        )
    )

    txnStats = txnStats.with_column_renamed("ACCOUNT_ID", "S_ACCOUNT_ID")   # e.ACCOUNT_ID = s.ACCOUNT_ID
    absAmount = sf_abs(col("TRANSACTION_AMOUNT"))
    zScore = when(
        col("STD_TXN_AMT") > 0, (absAmount - col("AVG_TXN_AMT")) / col("STD_TXN_AMT")
    ).otherwise(lit(None))
    # SAS compares missing as lower than every number, so `. < 0` and
    # `x > .` are TRUE.  Snowflake NULL comparisons are NULL (false in a
    # CASE), so each SAS comparison that can see a missing value is spelled
    # out with its IS NULL branch.  `calculated Z_SCORE > 3` with a missing
    # Z_SCORE is FALSE in SAS, which NULL > 3 already reproduces.
    anomalyType = (
        when(col("Z_SCORE") > ZSCORE_THRESHOLD, lit("HIGH_AMOUNT"))
        .when(col("RUNNING_BALANCE").is_null() | (col("RUNNING_BALANCE") < 0), lit("OVERDRAFT"))  # Q2
        .when(
            (col("TRANSACTION_TYPE") == "WDR")
            & (
                col("PRE_TXN_BALANCE").is_null()
                | (absAmount > col("PRE_TXN_BALANCE") * LARGE_WITHDRAWAL_PCT)
            ),
            lit("LARGE_WITHDRAWAL"),                                        # Q6
        )
        .when(_is_missing_char("CUSTOMER_ID"), lit("ORPHAN_ACCOUNT"))
        .otherwise(lit(""))
        .cast(StringType(20))                                               # length=20
    )
    txnAnomalies = (
        txnWithBalance.join(txnStats, col("ACCOUNT_ID") == col("S_ACCOUNT_ID"), how="left")
        .select(txnWithBalance.columns + ["AVG_TXN_AMT", "STD_TXN_AMT", zScore.alias("Z_SCORE")])  # e.*
        .with_column("ANOMALY_TYPE", anomalyType)
        .filter(col("ANOMALY_TYPE") != "")                                  # having ANOMALY_TYPE ne ''
        .cache_result()                                                     # WORK.TXN_ANOMALIES
    )
    # cache_result() materialises the WORK tables as Snowflake temp tables.
    # Without it Snowpark would re-evaluate TXN_STATS lazily AFTER today's
    # rows are appended to DAILY_TRANSACTIONS, breaking Q3.
    nobsAnom = txnAnomalies.count()

    # ------------------------------------------------------------------
    # Step 5: Load to Curated Layer  (SAS lines 205-217)
    # %lock has no equivalent: Snowflake DML is transactional.
    # ------------------------------------------------------------------
    proc_append_force(session, txnWithBalance, dailyTxnTable)               # Q1

    if nobsAnom > 0:
        proc_append_force(session, txnAnomalies, anomaliesTable)

    # ------------------------------------------------------------------
    # Step 6: Persist Running Balances  (SAS lines 222-225) — DATA step
    # replaces the table every run; KEEP preserves PDV order.
    # ------------------------------------------------------------------
    txnWithBalance.select(RUNNING_BALANCE_KEEP).write.mode("overwrite").save_as_table(
        runningBalTable
    )

    return (
        f"daily_transaction_processing completed for {txnDate}: "
        f"Feed: {nobsFeed} | Valid: {nobsValid} | Rejected: {nobsRejected} "
        f"| Anomalies: {nobsAnom}"
    )


def handler(session: Session, txnDate: str) -> str:
    """Stored-procedure entry point (CREATE PROCEDURE ... HANDLER='handler')."""
    return daily_transaction_processing(session, txnDate)
