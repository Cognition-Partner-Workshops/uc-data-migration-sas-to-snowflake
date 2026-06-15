#!/usr/bin/env python3
"""Generate deterministic synthetic data for claims_processing validation.

Produces:
  - sample_data/CLAIMS_REGISTER.csv (SAS baseline — source of truth)
  - sample_data/CLAIMS_REVIEW_QUEUE.csv (SAS baseline)
  - sample_data/FRAUD_ALERTS.csv (SAS baseline)
  - sample_data/{scenario}/CLAIMS_REGISTER.csv (Snowflake target)
  - sample_data/{scenario}/CLAIMS_REVIEW_QUEUE.csv (Snowflake target)
  - sample_data/{scenario}/FRAUD_ALERTS.csv (Snowflake target)

The logic here mirrors the SAS claims_processing.sas DATA step faithfully:
  Step 1: Validate claims against active policies (hash lookup)
  Step 2: Fraud screening (left join FRAUD_INDICATORS, score thresholds)
  Step 3: Auto-adjudication (sequential if/return rules)
  Step 4: Combine outputs

When the Snowpark code is correct, both outputs are identical and
validation passes. If the Snowpark diverges, the harness catches it.
"""
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_DIR = os.path.join(REPO_ROOT, "sample_data")

SEED = 42
PROC_DATE = "2024-01-15"


def generate_input_data(rng: np.random.Generator) -> tuple:
    """Generate synthetic CLAIMS_FEED, POLICIES, FRAUD_INDICATORS."""

    # --- POLICIES (100 policies, mix of active/inactive) ---
    policy_types = ["AUTO", "HOME", "RENT", "LIFE", "HEALTH"]
    n_policies = 100
    policies = pd.DataFrame({
        "POLICY_ID": [f"POL{i:04d}" for i in range(1, n_policies + 1)],
        "STATUS": rng.choice(["ACTIVE", "INACTIVE", "LAPSED"], n_policies,
                             p=[0.80, 0.15, 0.05]),
        "POLICY_TYPE": rng.choice(policy_types, n_policies),
        "EFFECTIVE_DATE": pd.to_datetime("2022-01-01") + pd.to_timedelta(
            rng.integers(0, 365, n_policies), unit="D"),
        "EXPIRATION_DATE": pd.to_datetime("2025-01-01") + pd.to_timedelta(
            rng.integers(0, 730, n_policies), unit="D"),
        "SUM_INSURED": rng.choice([50000, 100000, 200000, 500000], n_policies),
        "DEDUCTIBLE": rng.choice([500, 1000, 2000, 5000], n_policies),
    })

    # --- CLAIMS FEED (200 claims) ---
    n_claims = 200
    # Pick policies — some will reference non-existent ones to trigger invalids
    valid_policy_ids = policies["POLICY_ID"].tolist()
    invalid_policy_ids = [f"POL{i:04d}" for i in range(900, 910)]
    all_claim_policies = (
        rng.choice(valid_policy_ids, n_claims - 10).tolist()
        + invalid_policy_ids
    )
    rng.shuffle(all_claim_policies)

    claims_feed = pd.DataFrame({
        "CLAIM_ID": [f"CLM{i:06d}" for i in range(1, n_claims + 1)],
        "POLICY_ID": all_claim_policies[:n_claims],
        "CLAIMANT_ID": [f"CLNT{rng.integers(1, 80):04d}" for _ in range(n_claims)],
        "LOSS_DATE": pd.to_datetime("2024-01-01") + pd.to_timedelta(
            rng.integers(0, 14, n_claims), unit="D"),
        "CLAIMED_AMOUNT": np.round(
            rng.choice([1000, 2500, 4500, 8000, 15000, 35000, 60000, 120000],
                       n_claims, p=[0.15, 0.15, 0.15, 0.15, 0.15, 0.10, 0.10, 0.05]),
            2),
    })

    # --- FRAUD INDICATORS ---
    # Use (POLICY_ID, CLAIMANT_ID) pairs directly from the claims feed to
    # guarantee matches. Assign scores across all bands for test coverage.
    claim_pairs = claims_feed[["POLICY_ID", "CLAIMANT_ID"]].drop_duplicates()
    n_pairs = len(claim_pairs)
    # Select ~40% of claim pairs to have fraud indicators
    n_fraud = min(80, n_pairs)
    fraud_sample = claim_pairs.sample(n=n_fraud, random_state=int(rng.integers(0, 9999)))

    # Distribute scores: ~55 LOW, ~15 MEDIUM, ~10 HIGH
    n_high = 10
    n_medium = 15
    n_low = n_fraud - n_high - n_medium
    scores = np.concatenate([
        rng.integers(10, 49, n_low),
        rng.integers(50, 79, n_medium),
        rng.integers(80, 99, n_high),
    ])
    rng.shuffle(scores)

    fraud_indicators = fraud_sample.reset_index(drop=True).copy()
    fraud_indicators["FRAUD_SCORE"] = scores[:n_fraud].astype(int)
    fraud_indicators["INDICATOR_FLAGS"] = rng.choice(
        ["FREQ_CLAIMS", "NEW_POLICY", "HIGH_AMOUNT", "PRIOR_FRAUD",
         "SUSPICIOUS_DOCS", "MULTI_CLAIMS"],
        n_fraud)
    # Deduplicate on key (should already be unique)
    fraud_indicators = fraud_indicators.drop_duplicates(
        subset=["POLICY_ID", "CLAIMANT_ID"], keep="first"
    ).reset_index(drop=True)

    return claims_feed, policies, fraud_indicators


def run_claims_processing(
    claims_feed: pd.DataFrame,
    policies: pd.DataFrame,
    fraud_indicators: pd.DataFrame,
    proc_date: str,
) -> tuple:
    """Execute claims_processing logic in pandas (mirrors SAS DATA step).

    Returns (claims_register, claims_review_queue, fraud_alerts_df).
    """
    # Step 1: Validate — join to active policies only
    active_policies = policies[policies["STATUS"] == "ACTIVE"][
        ["POLICY_ID", "POLICY_TYPE", "EFFECTIVE_DATE", "EXPIRATION_DATE",
         "SUM_INSURED", "DEDUCTIBLE"]
    ]

    joined = claims_feed.merge(active_policies, on="POLICY_ID", how="left")

    # Valid claims: policy found, loss date in period, amount <= sum insured
    valid_mask = (
        joined["POLICY_TYPE"].notna()
        & (joined["LOSS_DATE"] >= joined["EFFECTIVE_DATE"])
        & (joined["LOSS_DATE"] <= joined["EXPIRATION_DATE"])
        & (joined["CLAIMED_AMOUNT"] <= joined["SUM_INSURED"])
    )
    claims_valid = joined[valid_mask].copy().reset_index(drop=True)

    # Step 2: Fraud screening — left join fraud indicators
    fraud_check = claims_valid.merge(
        fraud_indicators, on=["POLICY_ID", "CLAIMANT_ID"], how="left"
    )
    fraud_check["FRAUD_SCORE"] = fraud_check["FRAUD_SCORE"].fillna(0)
    fraud_check["INDICATOR_FLAGS"] = fraud_check["INDICATOR_FLAGS"].fillna("")

    fraud_check["FRAUD_RISK"] = "LOW"
    fraud_check.loc[fraud_check["FRAUD_SCORE"] >= 50, "FRAUD_RISK"] = "MEDIUM"
    fraud_check.loc[fraud_check["FRAUD_SCORE"] >= 80, "FRAUD_RISK"] = "HIGH"

    # Fraud alerts (HIGH risk)
    fraud_alerts_df = fraud_check[fraud_check["FRAUD_RISK"] == "HIGH"].copy()
    fraud_alerts_df["ALERT_REASON"] = (
        "Fraud score: " + fraud_alerts_df["FRAUD_SCORE"].astype(int).astype(str)
        + "; " + fraud_alerts_df["INDICATOR_FLAGS"]
    )
    fraud_alerts_df["ALERT_DATE"] = proc_date

    # Step 3: Auto-adjudication (sequential rules with early exit)
    fraud_check["ADJUDICATION_RESULT"] = ""
    fraud_check["ADJUDICATION_REASON"] = ""
    fraud_check["APPROVED_AMOUNT"] = np.nan

    assigned = pd.Series(False, index=fraud_check.index)

    # Rule 1: HIGH fraud → DENY, goes to MANUAL_REVIEW
    mask_deny = fraud_check["FRAUD_RISK"] == "HIGH"
    fraud_check.loc[mask_deny, "ADJUDICATION_RESULT"] = "DENY"
    fraud_check.loc[mask_deny, "ADJUDICATION_REASON"] = "High fraud risk - SIU referral"
    fraud_check.loc[mask_deny, "APPROVED_AMOUNT"] = 0.0
    assigned |= mask_deny

    # Rule 2: LOW risk, <=5000, policy type in (AUTO, HOME, RENT)
    mask_small = (
        ~assigned
        & (fraud_check["FRAUD_RISK"] == "LOW")
        & (fraud_check["CLAIMED_AMOUNT"] <= 5000)
        & fraud_check["POLICY_TYPE"].isin(["AUTO", "HOME", "RENT"])
    )
    fraud_check.loc[mask_small, "ADJUDICATION_RESULT"] = "APPR"
    fraud_check.loc[mask_small, "ADJUDICATION_REASON"] = "Auto-approved: low risk, small claim"
    fraud_check.loc[mask_small, "APPROVED_AMOUNT"] = np.maximum(
        0, fraud_check.loc[mask_small, "CLAIMED_AMOUNT"]
        - fraud_check.loc[mask_small, "DEDUCTIBLE"]
    )
    assigned |= mask_small

    # Rule 3: LOW risk, <=25% of sum insured AND <=50K
    mask_standard = (
        ~assigned
        & (fraud_check["FRAUD_RISK"] == "LOW")
        & (fraud_check["CLAIMED_AMOUNT"] <= fraud_check["SUM_INSURED"] * 0.25)
        & (fraud_check["CLAIMED_AMOUNT"] <= 50000)
    )
    fraud_check.loc[mask_standard, "ADJUDICATION_RESULT"] = "APPR"
    fraud_check.loc[mask_standard, "ADJUDICATION_REASON"] = (
        "Auto-approved: within 25% of sum insured"
    )
    fraud_check.loc[mask_standard, "APPROVED_AMOUNT"] = np.maximum(
        0, fraud_check.loc[mask_standard, "CLAIMED_AMOUNT"]
        - fraud_check.loc[mask_standard, "DEDUCTIBLE"]
    )
    assigned |= mask_standard

    # Rule 4: Everything else → PEND (manual review)
    mask_pend = ~assigned
    fraud_check.loc[mask_pend, "ADJUDICATION_RESULT"] = "PEND"

    # Build dynamic reason (SAS ifc() equivalent)
    for idx in fraud_check.index[mask_pend]:
        reasons = []
        if fraud_check.loc[idx, "FRAUD_RISK"] == "MEDIUM":
            reasons.append("Medium fraud risk")
        if fraud_check.loc[idx, "CLAIMED_AMOUNT"] > 50000:
            reasons.append("Large claim")
        if fraud_check.loc[idx, "CLAIMED_AMOUNT"] > fraud_check.loc[idx, "SUM_INSURED"] * 0.25:
            reasons.append("Exceeds 25% threshold")
        fraud_check.loc[idx, "ADJUDICATION_REASON"] = "; ".join(reasons)

    fraud_check.loc[mask_pend, "APPROVED_AMOUNT"] = np.nan

    # Step 4: Build outputs
    fraud_check["PROCESSING_DATE"] = proc_date
    fraud_check["CLAIM_STATUS"] = fraud_check["ADJUDICATION_RESULT"]

    # MANUAL_REVIEW = denied (Rule 1) + pending (Rule 4)
    manual_mask = mask_deny | mask_pend

    # CLAIMS_REGISTER = all (auto + denied + manual)
    register_cols = [
        "CLAIM_ID", "POLICY_ID", "CLAIMANT_ID", "LOSS_DATE", "CLAIMED_AMOUNT",
        "POLICY_TYPE", "SUM_INSURED", "DEDUCTIBLE", "FRAUD_SCORE", "FRAUD_RISK",
        "ADJUDICATION_RESULT", "ADJUDICATION_REASON", "APPROVED_AMOUNT",
        "PROCESSING_DATE", "CLAIM_STATUS",
    ]
    claims_register = fraud_check[register_cols].copy()

    review_cols = [
        "CLAIM_ID", "POLICY_ID", "CLAIMANT_ID", "LOSS_DATE", "CLAIMED_AMOUNT",
        "POLICY_TYPE", "SUM_INSURED", "DEDUCTIBLE", "FRAUD_SCORE", "FRAUD_RISK",
        "ADJUDICATION_RESULT", "ADJUDICATION_REASON", "APPROVED_AMOUNT",
    ]
    claims_review_queue = fraud_check.loc[manual_mask, review_cols].copy()

    alert_cols = [
        "CLAIM_ID", "POLICY_ID", "CLAIMANT_ID", "LOSS_DATE", "CLAIMED_AMOUNT",
        "FRAUD_SCORE", "FRAUD_RISK", "INDICATOR_FLAGS", "ALERT_REASON", "ALERT_DATE",
    ]
    fraud_alerts_out = fraud_alerts_df[alert_cols].copy()

    return claims_register, claims_review_queue, fraud_alerts_out


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "Scenario1"
    scenario_dir = os.path.join(SAMPLE_DIR, scenario)
    os.makedirs(scenario_dir, exist_ok=True)

    rng = np.random.default_rng(SEED)
    claims_feed, policies, fraud_indicators = generate_input_data(rng)

    claims_register, claims_review_queue, fraud_alerts_out = run_claims_processing(
        claims_feed, policies, fraud_indicators, PROC_DATE
    )

    # Write SAS baselines (source of truth)
    claims_register.to_csv(os.path.join(SAMPLE_DIR, "CLAIMS_REGISTER.csv"), index=False)
    claims_review_queue.to_csv(os.path.join(SAMPLE_DIR, "CLAIMS_REVIEW_QUEUE.csv"), index=False)
    fraud_alerts_out.to_csv(os.path.join(SAMPLE_DIR, "FRAUD_ALERTS.csv"), index=False)

    # Write Snowflake targets (should be identical when conversion is correct)
    claims_register.to_csv(os.path.join(scenario_dir, "CLAIMS_REGISTER.csv"), index=False)
    claims_review_queue.to_csv(os.path.join(scenario_dir, "CLAIMS_REVIEW_QUEUE.csv"), index=False)
    fraud_alerts_out.to_csv(os.path.join(scenario_dir, "FRAUD_ALERTS.csv"), index=False)

    print(f"Generated claims validation data for {scenario}:")
    print(f"  CLAIMS_REGISTER:     {len(claims_register)} rows")
    print(f"  CLAIMS_REVIEW_QUEUE: {len(claims_review_queue)} rows")
    print(f"  FRAUD_ALERTS:        {len(fraud_alerts_out)} rows")


if __name__ == "__main__":
    main()
