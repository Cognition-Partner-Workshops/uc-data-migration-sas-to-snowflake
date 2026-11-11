# Synthetic baseline for JOB06 `policy_valuation.sas`

**There is no SAS golden for this program.** `README_GOLDEN.md` in the golden
tarball states that `RAW_INS.*` has no seed data anywhere in the estate and the
Docker SAS runner covers banking only. The baseline below is therefore
*synthetic*: twelve policies, twelve claim rows and ten premium rows, every
identifier prefixed `SYN-`, chosen so that each branch of the SAS logic and each
flagged quirk is exercised. The expected outputs in `baseline_expected/` were
**hand-computed from the SAS source** (derivation below), *not* produced by
running SQL. The harness then compares the Snowflake export against them.

## Assumptions

| # | Assumption | Basis |
|---|---|---|
| A1 | `RAW_INS.POLICIES` columns are exactly those the program reads (`:36–48`, `:63`): `POLICY_ID, CUSTOMER_ID, POLICY_TYPE, STATUS, EFFECTIVE_DATE, EXPIRATION_DATE, ANNUAL_PREMIUM, SUM_INSURED, DEDUCTIBLE, RISK_CATEGORY, UNDERWRITING_CLASS, AGENT_ID, BRANCH_CODE`. `POLICY_TYPE` is `$10` (claims_processing.sas:54). | SAS source |
| A2 | `RAW_INS.CLAIMS` columns: `CLAIM_ID, POLICY_ID, LOSS_DATE, CLAIM_STATUS, INCURRED_AMOUNT, PAID_AMOUNT, RESERVED_AMOUNT` (`:81–94`). Status codes from `$CLMSTAT.`. | SAS source, `Formats/insurance_formats.sas:32–46` |
| A3 | `RAW_INS.PREMIUMS` columns: `PAYMENT_ID` (assumed key, never read), `POLICY_ID, PAYMENT_DATE, PREMIUM_AMOUNT, PAYMENT_STATUS` (`:105–115`). Status values `PAID / LATE / RETURNED` (`LATE`, `RETURNED` are the ones the program tests). | SAS source |
| A4 | Numerics are SAS doubles → `FLOAT`; dates are SAS dates → `DATE` (ISO text in the CSVs); an empty CSV cell is SAS missing (`.` or blank). | SAS storage model |
| A5 | Formats (`$POLTYPE.`, `$RISKCAT.`, `dollar18.2`, `percent8.2`, `date9.`) are display attributes; the expected files hold the **stored** values. | playbook ("formatting is presentation-layer") |
| A6 | `lob=ALL` (what `run_daily_insurance.sas` passes). | batch :112, program :206 |
| A7 | `CURR_DT = 31JAN2024` → `val_date = 2024-01-31`; `intnx('year',…,0,'B') = 2024-01-01`; `intnx('month',…,3) = 2024-04-01`; `intnx('month',…,-12) = 2023-01-01`. | `Config/autoexec_local.sas:74` |

## Inputs (`baseline_inputs/`)

Policies — why each row is there:

| POLICY_ID | TYPE | STATUS | EFFECTIVE | EXPIRATION | PREMIUM | In force 31JAN2024? | Exercises |
|---|---|---|---|---|---|---|---|
| SYN-P001 | AUTO | ACTIVE | 2023-03-15 | 2024-03-14 | 1200 | yes | renewal due, 3 claims incl. one on val_date, late payment, payment with missing date |
| SYN-P002 | HOME | ACTIVE | 2022-06-01 | 2025-05-31 | 2400 | yes | DENY claim on 2023-01-01 (13-month window), claim on 2022-12-31 excluded, RETURNED premium, prior-year payment excluded |
| SYN-P003 | WL | ACTIVE | 2024-01-15 | 2034-01-14 | 600 | yes | effective this month, no claims, no premiums |
| SYN-P004 | AUTO | CANCELLED | 2023-01-01 | 2024-12-31 | 900 | **no** (STATUS) | its claim/payment must not surface |
| SYN-P005 | TL | ACTIVE | 2024-02-01 | 2034-01-31 | 480 | **no** (future effective) | — |
| SYN-P006 | HLTH | ACTIVE | 2023-01-01 | 2024-01-31 | 3600 | yes (expires on val_date) | duplicate claim row, late payment on val_date |
| SYN-P007 | AUTO | ACTIVE | *missing* | 2024-12-31 | 1000 | yes (Q2) | missing effective date, payment with missing amount |
| SYN-P008 | UL | ACTIVE | 2023-07-01 | 2024-06-30 | *missing* | yes | missing premium → N_POLICIES 0, IBNR 0 |
| SYN-P009 | *blank* | ACTIVE | 2023-05-01 | 2024-04-30 | 800 | yes | dropped by PROC MEANS (Q7); RISK_CATEGORY `XYZ` (`$RISKCAT.` OTHER) |
| SYN-P010 | AUTO | ACTIVE | 2023-11-01 | 2024-10-31 | 1500 | yes | claim after val_date excluded, PEND claim with missing reserve |
| SYN-P011 | HOME | ACTIVE | 2021-02-01 | *missing* | 2000 | **no** (missing expiration fails `>=`) | — |
| SYN-P012 | AUTO | ACTIVE | 2022-05-10 | 2024-01-30 | 1100 | **no** (expired the day before) | — |

Claims: see `RAW_INS__CLAIMS.csv` (SYN-CL005 appears twice on purpose; SYN-CL009
has a missing `RESERVED_AMOUNT`; SYN-CL010 is dated 2024-02-05; SYN-CL004 is dated
2022-12-31; SYN-CL011 belongs to the cancelled policy).
Premiums: see `RAW_INS__PREMIUMS.csv` (SYN-PM003 is 2023-12-28; SYN-PM007 is
2024-02-01; SYN-PM008 has a missing amount; SYN-PM010 has a missing date).

## Derivation at `val_date = 2024-01-31` (→ `baseline_expected/`)

Step 1 (`:49–60`): `intck('month', a, b)` counts month boundaries.

| POLICY | AGE = intck(eff, 31JAN24) | MTE = intck(31JAN24, exp) | RENEWAL (exp ≤ 01APR24) | YTD = PREM/12 × min(12, intck(max(eff, 01JAN24), min(31JAN24, exp))) |
|---|---|---|---|---|
| P001 | 10 | 2 | Y | 100 × intck(01JAN24, 31JAN24)=0 → **0** |
| P002 | 19 | 16 | N | 200 × 0 → 0 |
| P003 | 0 | 120 | N | 50 × intck(15JAN24, 31JAN24)=0 → 0 |
| P006 | 12 | 0 | Y | 300 × 0 → 0 |
| P007 | . | 11 | N | max(., 01JAN24)=01JAN24 → 1000/12 × 0 → 0 |
| P008 | 6 | 5 | N | ./12 × 0 → **.** |
| P009 | 8 | 3 | N (30APR24 > 01APR24) | 0 |
| P010 | 2 | 9 | N | 0 |

Step 2 (`:78–97`, window 2023-01-01 … 2024-01-31):

| POLICY | rows in window | NUM (distinct) | INCURRED | PAID | RESERVED | LAST_CLAIM | OPEN_RESERVES (OPEN/INV/ADJ/PEND) | DENIED |
|---|---|---|---|---|---|---|---|---|
| P001 | CL001 PAID 2500/2500/0; CL002 OPEN 4000/1000/3000; CL012 INV 900/0/900 | 3 | 7400 | 3500 | 3900 | 2024-01-31 | 3000+900 = 3900 | 0 |
| P002 | CL003 DENY 0/0/0 (CL004 2022-12-31 excluded) | 1 | 0 | 0 | 0 | 2023-01-01 | 0 | 1 |
| P006 | CL005 ADJ 15000/5000/10000 **×2** | 1 | 30000 | 10000 | 20000 | 2023-09-05 | 20000 | 0 |
| P008 | CL007 OPEN 500/0/500 | 1 | 500 | 0 | 500 | 2024-01-20 | 500 | 0 |
| P010 | CL008 ADJ 1200/200/1000; CL009 PEND 800/0/. (CL010 2024-02-05 excluded) | 2 | 2000 | 200 | 1000 | 2024-01-15 | 1000 + (.) = 1000 | 0 |
| P003, P007, P009 | none | . | . | . | . | . | . | . |

Step 3 (`:102–118`, window 2024-01-01 … 2024-01-31):

| POLICY | rows in window | COLLECTED | RETURNED | LAST_PAYMENT | LATE |
|---|---|---|---|---|---|
| P001 | PM001 100 PAID; PM002 100 LATE (PM010 missing date excluded) | 200 | 0 | 2024-01-28 | 1 |
| P002 | PM004 200 RETURNED (PM003 2023-12-28 excluded) | 200 | 200 | 2024-01-10 | 0 |
| P006 | PM005 300 PAID; PM006 300 LATE | 600 | 0 | 2024-01-31 | 1 |
| P007 | PM008 . PAID | sum(.) = **.** | 0 | 2024-01-15 | 0 |
| others | none | . | . | . | . |

Step 4 (`:123–164`): `YTD_EARNED_PREMIUM` is 0 or missing for every row, so
`LOSS_RATIO = .`, `COMBINED_RATIO = .`, `PREMIUM_ADEQUATE = 'N'` for all eight;
`IBNR = max(0, YTD×0.15 − coalesce(PAID,0)) = max(0, −PAID) = 0` (P008: `max(0, .) = 0`);
`TOTAL_RESERVE = coalesce(OPEN_RESERVES,0) + 0` → P001 3900, P002 0, P003 0,
P006 20000, P007 0, P008 500, P009 0, P010 1000 (Σ = 25400).

Step 5 (`:169–193`): P009 (blank class) dropped; `N_POLICIES` = count of
non-missing `YTD_EARNED_PREMIUM`.

| POLICY_TYPE | members | N_POLICIES | TOTAL_EARNED | TOTAL_INCURRED | TOTAL_PAID | TOTAL_RESERVES | TOTAL_IBNR | AGG ratios |
|---|---|---|---|---|---|---|---|---|
| AUTO | P001, P007, P010 | 3 | 0 | 7400+2000 = 9400 | 3700 | 4900 | 0 | . (TOTAL_EARNED not > 0) |
| HLTH | P006 | 1 | 0 | 30000 | 10000 | 20000 | 0 | . |
| HOME | P002 | 1 | 0 | 0 | 0 | 0 | 0 | . |
| UL | P008 | 0 | sum(.) = . | 500 | 0 | 500 | 0 | . |
| WL | P003 | 1 | 0 | . | . | 0 | 0 | . |

## Logic check at `val_date = 2024-06-30` (→ `logic_check_30JUN2024/baseline_expected/`)

Same inputs; anchors: year start 2024-01-01, renewal cutoff 2024-09-01, claims
from 2023-06-01. Added only because 31JAN2024 cannot exercise the non-zero
branches (quirk Q1).

In force: P002, P003, P005, P007, P008 (expires on val_date), P010.
Out: P001 (exp 14MAR24), P006 (exp 31JAN24), P009 (exp 30APR24), P004, P011, P012.

| POLICY | AGE | MTE | RENEWAL | YTD | claims in window → NUM/INC/PAID/RES/OPEN | premiums → COLL/RET/LATE | LOSS_RATIO | COMBINED | ADEQ | IBNR = max(0, YTD×0.15 − PAID) | TOTAL_RESERVE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P002 | 24 | 11 | N | 200 × intck(01JAN24, 30JUN24)=5 = 1000 | none (CL003 is Jan-2023) | PM004: 200/200/0 | 0/1000 = 0 | 0.30 | Y | 150 | 0 + 150 = 150 |
| P003 | 5 | 115 | N | 50 × intck(15JAN24, 30JUN24)=5 = 250 | none | none | 0 | 0.30 | Y | 37.5 | 37.5 |
| P005 | 4 | 115 | N | 40 × intck(01FEB24, 30JUN24)=4 = 160 | none | none | 0 | 0.30 | Y | 24 | 24 |
| P007 | . | 6 | N | 1000/12 × 5 = 416.6̅ | none | PM008: ./0/0 | 0 | 0.30 | Y | 62.5 | 62.5 |
| P008 | 11 | 0 | Y | . | CL007: 1/500/0/500/500 | none | . | . | N | max(0, .) = 0 | 500 |
| P010 | 7 | 4 | N (31OCT24 > 01SEP24) | 125 × 5 = 625 | CL008, CL009, CL010: 3/5000/200/4000/(1000+.+3000)=4000 | PM007: 125/0/0 | 5000/625 = 8.0 | 8.30 | N (> 1.0) | max(0, 93.75 − 200) = 0 | 4000 |

Summary: AUTO (P007, P010) N=2, EARNED 1041.6̅, INCURRED 5000, PAID 200,
RESERVES 4062.5, IBNR 62.5, AGG_LOSS 5000/1041.6̅ = 4.8, AGG_COMBINED 5.1;
HOME N=1, EARNED 1000, INCURRED . → AGG ratios **.** (missing/1000 is missing
even though TOTAL_EARNED > 0); TL N=1, 160, ., ., 24, 24, .; UL N=0, EARNED .,
500, 0, 500, 0, .; WL N=1, 250, ., ., 37.5, 37.5, .

## Reproduce

```bash
# Snowflake (needs SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PAT):
python3 sample_data/Scenario_policy_valuation/run_local.py --val-date 2024-01-31 --out sample_data/Scenario_policy_valuation
python3 sample_data/Scenario_policy_valuation/run_local.py --val-date 2024-06-30 --out sample_data/Scenario_policy_valuation/logic_check_30JUN2024
# or locally: add --engine duckdb to either command (pip install duckdb)

python3 verify/reconcile.py --scenario Scenario_policy_valuation \
    --sas-golden sample_data/Scenario_policy_valuation/baseline_expected \
    --report sample_data/Scenario_policy_valuation/reconciliation.md
python3 verify/reconcile.py --scenario Scenario_policy_valuation/logic_check_30JUN2024 \
    --sas-golden sample_data/Scenario_policy_valuation/logic_check_30JUN2024/baseline_expected \
    --report sample_data/Scenario_policy_valuation/logic_check_30JUN2024/reconciliation.md
```
