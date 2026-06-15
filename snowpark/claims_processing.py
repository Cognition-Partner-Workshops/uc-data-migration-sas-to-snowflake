"""
claims_processing.py — Snowpark Python conversion of
claims_processing.sas (Insurance daily claims intake and processing)

Source: ts-sas-legacy-analytics/Programs/Insurance/claims_processing.sas
SAS schedule: Daily 08:00 via Control-M INS_DAILY_01
Snowflake: Runs as a Snowpark stored procedure via Snowflake Task

Why Snowpark (not SQL):
  The SAS program uses procedural DATA step logic with hash lookups,
  multi-output datasets (CLAIMS_VALID / CLAIMS_INVALID, AUTO_ADJUDICATED /
  MANUAL_REVIEW), and conditional routing that maps naturally to Python
  but awkwardly to pure SQL. Snowpark preserves the procedural semantics
  while running natively on Snowflake compute.

Inputs:  RAW_INS.CLAIMS_FEED (daily), RAW_INS.POLICIES,
         TERA_DW.FRAUD_INDICATORS
Outputs: STG_INS.CLAIMS_REGISTER, STG_INS.CLAIMS_REVIEW_QUEUE,
         STG_INS.FRAUD_ALERTS

Conversion notes:
  - SAS hash lookup (h_pol) → Snowpark DataFrame join
  - SAS DATA step multi-output → filtered DataFrames written separately
  - SAS %nobs → df.count()
  - SAS ifc() → Python conditional expressions / when()
  - SAS PROC APPEND → Snowpark write_pandas with mode='append'
  - SAS %sendmail → Snowflake SYSTEM$SEND_EMAIL (or external notification)
  - SAS catx() for ADJUDICATION_REASON → concat_ws()
  - SAS catx()/put() for ALERT_REASON → concat_ws()
"""
from datetime import date

from snowflake.snowpark import Session
from snowflake.snowpark.functions import (
    col,
    concat_ws,
    lit,
    when,
)
from snowflake.snowpark.types import StringType


def claims_processing(session: Session, proc_date: str) -> str:
    """Process daily claims feed: validate, screen fraud, auto-adjudicate.

    Args:
        session: Active Snowpark session.
        proc_date: Processing date as 'YYYY-MM-DD'.

    Returns:
        Summary string with counts.
    """
    proc_dt = date.fromisoformat(proc_date)
    feed_table = f"RAW_INS.CLAIMS_FEED_{proc_dt.strftime('%Y%m%d')}"

    # ------------------------------------------------------------------
    # Step 1: Ingest and Validate
    # SAS: DATA step with hash lookup (h_pol) against POLICIES
    # Multi-output: CLAIMS_VALID / CLAIMS_INVALID
    # ------------------------------------------------------------------
    try:
        claims_raw = session.table(feed_table)
    except Exception:
        return f"ERROR: Claims feed {feed_table} not found"

    policies = (
        session.table("RAW_INS.POLICIES")
        .filter(col("STATUS") == "ACTIVE")
        .select(
            "POLICY_ID", "POLICY_TYPE", "EFFECTIVE_DATE",
            "EXPIRATION_DATE", "SUM_INSURED", "DEDUCTIBLE",
        )
    )

    # Join claims to policies (SAS hash lookup equivalent)
    joined = claims_raw.join(policies, on="POLICY_ID", how="left")

    # Valid: policy found AND active, loss date within policy period,
    # claimed amount does not exceed sum insured
    claims_valid = joined.filter(
        col("POLICY_TYPE").is_not_null()
        & (col("LOSS_DATE") >= col("EFFECTIVE_DATE"))
        & (col("LOSS_DATE") <= col("EXPIRATION_DATE"))
        & (col("CLAIMED_AMOUNT") <= col("SUM_INSURED"))
    )

    # claims_invalid retained for completeness (mirrors SAS multi-output)
    # but not written to a table in this procedure
    _ = joined.filter(
        col("POLICY_TYPE").is_null()
        | (col("LOSS_DATE") < col("EFFECTIVE_DATE"))
        | (col("LOSS_DATE") > col("EXPIRATION_DATE"))
        | (col("CLAIMED_AMOUNT") > col("SUM_INSURED"))
    )  # noqa: F841

    nobs_new = claims_valid.count()

    # ------------------------------------------------------------------
    # Step 2: Fraud Screening
    # SAS: PROC SQL left join to TERA_DW.FRAUD_INDICATORS
    # FRAUD_RISK derived from FRAUD_SCORE thresholds: >=80 HIGH, >=50 MEDIUM
    # ------------------------------------------------------------------
    fraud_indicators = session.table("TERA_DW.FRAUD_INDICATORS")

    fraud_check = claims_valid.join(
        fraud_indicators,
        on=["POLICY_ID", "CLAIMANT_ID"],
        how="left",
    ).with_column(
        "FRAUD_RISK",
        when(col("FRAUD_SCORE") >= 80, lit("HIGH"))
        .when(col("FRAUD_SCORE") >= 50, lit("MEDIUM"))
        .otherwise(lit("LOW")),
    )

    # SAS: separate high-risk claims for SIU review with ALERT_REASON, ALERT_DATE
    fraud_alerts = (
        fraud_check
        .filter(col("FRAUD_RISK") == "HIGH")
        .with_column(
            "ALERT_REASON",
            concat_ws(
                lit("; "),
                concat_ws(lit(" "), lit("Fraud score:"), col("FRAUD_SCORE").cast(StringType())),
                col("INDICATOR_FLAGS"),
            ),
        )
        .with_column("ALERT_DATE", lit(proc_date))
    )
    nobs_fraud = fraud_alerts.count()

    # ------------------------------------------------------------------
    # Step 3: Auto-Adjudication Rules
    # Reproduces SAS DATA step logic exactly — do NOT modify thresholds.
    # SAS uses sequential if/return: once a row outputs, it skips later rules.
    # ------------------------------------------------------------------

    # Rule 1: Auto-deny — high fraud risk → MANUAL_REVIEW with DENY
    denied = fraud_check.filter(col("FRAUD_RISK") == "HIGH").with_columns(
        ["ADJUDICATION_RESULT", "ADJUDICATION_REASON", "APPROVED_AMOUNT"],
        [
            lit("DENY"),
            lit("High fraud risk - SIU referral"),
            lit(0.0),
        ],
    )

    # Rule 2: Auto-approve — low risk, small claim (<=5000), eligible policy type
    low_risk = fraud_check.filter(col("FRAUD_RISK") == "LOW")

    auto_small = low_risk.filter(
        (col("CLAIMED_AMOUNT") <= 5000)
        & col("POLICY_TYPE").isin("AUTO", "HOME", "RENT")
    ).with_columns(
        ["ADJUDICATION_RESULT", "ADJUDICATION_REASON", "APPROVED_AMOUNT"],
        [
            lit("APPR"),
            lit("Auto-approved: low risk, small claim"),
            when(col("CLAIMED_AMOUNT") - col("DEDUCTIBLE") > 0,
                 col("CLAIMED_AMOUNT") - col("DEDUCTIBLE")).otherwise(lit(0.0)),
        ],
    )

    # Rule 3: Auto-approve — low risk, within 25% of sum insured AND <=50K
    # Excludes rows already captured by Rule 2 (SAS `return` semantics)
    auto_small_ids = auto_small.select("CLAIM_ID")
    auto_standard = (
        low_risk
        .join(auto_small_ids, on="CLAIM_ID", how="left_anti")
        .filter(
            (col("CLAIMED_AMOUNT") <= col("SUM_INSURED") * 0.25)
            & (col("CLAIMED_AMOUNT") <= 50000)
        )
        .with_columns(
            ["ADJUDICATION_RESULT", "ADJUDICATION_REASON", "APPROVED_AMOUNT"],
            [
                lit("APPR"),
                lit("Auto-approved: within 25% of sum insured"),
                when(col("CLAIMED_AMOUNT") - col("DEDUCTIBLE") > 0,
                     col("CLAIMED_AMOUNT") - col("DEDUCTIBLE")).otherwise(lit(0.0)),
            ],
        )
    )

    auto_adjudicated = auto_small.union_all(auto_standard)
    nobs_auto = auto_adjudicated.count()

    # Rule 4: Everything else → manual review (SAS: ADJUDICATION_RESULT='PEND')
    # SAS builds dynamic reason via ifc():
    #   Medium fraud risk; Large claim (>50K); Exceeds 25% threshold
    auto_ids = auto_adjudicated.select("CLAIM_ID")
    denied_ids = denied.select("CLAIM_ID")
    manual_review = (
        fraud_check
        .join(auto_ids, on="CLAIM_ID", how="left_anti")
        .join(denied_ids, on="CLAIM_ID", how="left_anti")
        .with_columns(
            ["ADJUDICATION_RESULT", "ADJUDICATION_REASON", "APPROVED_AMOUNT"],
            [
                lit("PEND"),
                concat_ws(
                    lit("; "),
                    when(col("FRAUD_RISK") == "MEDIUM", lit("Medium fraud risk")).otherwise(lit("")),
                    when(col("CLAIMED_AMOUNT") > 50000, lit("Large claim")).otherwise(lit("")),
                    when(col("CLAIMED_AMOUNT") > col("SUM_INSURED") * 0.25,
                         lit("Exceeds 25% threshold")).otherwise(lit("")),
                ),
                lit(None).cast("DOUBLE"),
            ],
        )
    )
    nobs_review = manual_review.count()

    # ------------------------------------------------------------------
    # Step 4: Update Claims Register (SAS PROC APPEND equivalent)
    # Combines auto-adjudicated + denied + manual review
    # ------------------------------------------------------------------
    combined = (
        auto_adjudicated.union_all(denied).union_all(manual_review)
        .with_column("PROCESSING_DATE", lit(proc_date))
        .with_column("CLAIM_STATUS", col("ADJUDICATION_RESULT"))
    )

    combined.write.mode("append").save_as_table("STG_INS.CLAIMS_REGISTER")
    manual_review.write.mode("append").save_as_table(
        "STG_INS.CLAIMS_REVIEW_QUEUE"
    )

    if nobs_fraud > 0:
        fraud_alerts.write.mode("append").save_as_table("STG_INS.FRAUD_ALERTS")

    summary = (
        f"claims_processing completed for {proc_date}: "
        f"new={nobs_new}, auto={nobs_auto}, "
        f"review={nobs_review}, fraud={nobs_fraud}"
    )
    return summary
