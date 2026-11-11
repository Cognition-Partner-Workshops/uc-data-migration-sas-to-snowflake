# Synthetic baseline for JOB07 customer_profitability (report_month = 202401)

## Why a synthetic baseline

`Programs/Reports/customer_profitability.sas` cannot run on the estate data: Step 2 selects
`t.CUSTOMER_ID` from `CURATED.DAILY_TRANSACTIONS`, which has no such column, and SAS stops with
`ERROR: PROC SQL: the following columns were not found in the contributing tables: CUSTOMER_ID`
(`export_extra_failed.log` in the golden tarball). There is therefore **no SAS golden** for
`REPORTS.CUSTOMER_PNL`, `REPORTS.SEGMENT_PROFITABILITY`, `REPORTS.BRANCH_PROFITABILITY`.

The expected outputs in `baseline_expected/` were **hand-computed from the SAS logic** on the
small, clearly synthetic inputs in `baseline_inputs/` (ids `C000000n`, `A00000nnn`, `S0000000nn`,
`MODEL_ID = SYNTH`). The reconciliation (`reconciliation.md`) compares the DuckDB execution of
`snowflake_sql/JOB07_CUSTOMER_PROFITABILITY.sql` against these hand-computed files. Nothing in
`baseline_expected/` was produced by the migration code.

## Assumptions (every one of them)

1. **A1 — synthetic `CUSTOMER_ID` on transactions.** `baseline_inputs/curated__daily_transactions.csv`
   carries a `CUSTOMER_ID` column, because the SAS program requires one. The estate table does not have
   it; running `run_local.py --inputs <golden dir>` reproduces the SAS failure instead of producing output.
2. **A2 — dates** are stored as SAS date integers (days since 1960-01-01) exactly like the golden
   exports: 23375 = 31DEC2023, 23376 = 01JAN2024, 23405 = 30JAN2024, 23406 = 31JAN2024, 23407 = 01FEB2024.
   `&month_start` = 01JAN2024, `&month_end` = 31JAN2024 (`inputn('20240101', yymmdd8.)`, `intnx(month, ..., 0, E)`).
3. **A3 — only the columns the program reads** are present in the synthetic inputs (the loader types every
   golden column by name, so the full estate files load too). All SAS numerics are doubles.
4. **A4 — blank character = SAS missing** (`CUSTOMER_SEGMENT` of C0000005 is blank). PROC MEANS with no
   `MISSING` option drops observations whose CLASS value is missing. The `$CUSTSEG.`/`$REGION.` formats'
   `OTHER` buckets are *not* assumed to re-label the blank into a class level (see open questions in
   `docs/JOB07_customer_profitability.md`); all other segment/region codes used are mapped one-to-one by
   the formats, so formatted vs. unformatted grouping gives the same result.
5. **A5 — `max()` on character columns** is a plain string maximum (`'PREM' > 'PB'`, `'W' > 'NW'`,
   `'B002' > 'B001'`), which is what SAS PROC SQL does regardless of the "largest account" comment.
6. **A6 — `n=N_CUSTOMERS`** in `OUTPUT ... n=N_CUSTOMERS sum=` names the N statistic of the *first*
   VAR variable (`TOTAL_REVENUE`), i.e. count of non-missing `TOTAL_REVENUE`; `sum=` keeps the analysis
   variable names; `mean(NET_PROFIT)=AVG_PROFIT_PER_CUSTOMER`.
7. **A7 — PROC MEANS `sum` of an all-missing group is missing** (PB segment / B003-SE branch `TOTAL_ECL`).
8. **A8 — output precision**: values are written with as many digits as the arithmetic gives; the harness
   compares sums at 0.01 tolerance, and ROA for C0000001 is written rounded to 10 decimals
   (12357 / 252000 = 0.04903571428…).

## Inputs

### STG_BANK.CUST_ACCOUNTS_DAILY (12 rows)

| ACCOUNT_ID | CUSTOMER_ID | TYPE | BALANCE | RATE | BRANCH | SEG | REGION | SNAPSHOT | in Step 1? |
|---|---|---|---|---|---|---|---|---|---|
| A00000101 | C0000001 | MTG | 240000 | 0.06 | B001 | RET | NE | 23406 | yes (lending) |
| A00000102 | C0000001 | CHK | 12000 | 0.01 | B001 | RET | NE | 23406 | yes (deposit) |
| A00000101 | C0000001 | MTG | 241000 | 0.06 | B001 | RET | NE | 23405 | **no** — prior snapshot (`where SNAPSHOT_DATE = 31JAN2024`) |
| A00000201 | C0000002 | SAV | 50000 | 0.03 | B002 | PREM | W | 23406 | yes (deposit) |
| A00000202 | C0000002 | CD | 100000 | 0.048 | B002 | PREM | W | 23406 | yes (deposit) |
| A00000301 | C0000003 | LOC | 20000 | **missing** | B001 | SMB | MW | 23406 | yes (lending, missing product) |
| A00000401 | C0000004 | AUTO | 10000 | 0.06 | B001 | PB | NE | 23406 | yes (lending) |
| A00000402 | C0000004 | SAV | 2000 | 0.03 | B001 | PB | NE | 23406 | yes (deposit) |
| A00000501 | C0000005 | CC | 0 | 0.2 | B003 | *blank* | SE | 23406 | yes (lending, zero) |
| A00000601 | C0000006 | PERS | 30000 | 0.08 | B002 | PREM | W | 23406 | yes (lending) |
| A00000602 | C0000006 | MMA | 20000 | 0.03 | B001 | PB | NW | 23406 | yes (deposit) — exercises `max()` on SEG/REGION/BRANCH |
| A00000701 | C0000007 | AUTO | 15000 | 0.05 | B003 | RET | SE | 23405 | **no** — customer has no month-end row → dropped by `if a` |

### CURATED.DAILY_TRANSACTIONS (15 rows; `TRANSACTION_DATE between 23376 and 23406`)

| TXN | ACCOUNT | CUSTOMER | TYPE | DATE | AMOUNT | in Step 2? |
|---|---|---|---|---|---|---|
| S000000001 | A00000102 | C0000001 | FEE | 23380 | -25 | yes → abs 25 |
| S000000002 | A00000102 | C0000001 | FEE | 23390 | 15 | yes → 15 |
| S000000003 | A00000102 | C0000001 | INT | 23406 | 3.5 | yes (month_end inclusive) → INT 3.5 |
| S000000004 | A00000102 | C0000001 | DEP | 23385 | 500 | yes (volume only) |
| S000000005 | A00000102 | C0000001 | FEE | 23375 | 99 | **no** — 31DEC2023 |
| S000000006 | A00000102 | C0000001 | FEE | 23407 | 77 | **no** — 01FEB2024 |
| S000000015 | A00000102 | C0000001 | DEP | 23376 | 10 | yes (month_start inclusive) |
| S000000007 | A00000301 | C0000003 | FEE | 23380 | -12 | yes → 12 |
| S000000008 | A00000402 | C0000004 | FEE | 23380 | 30 | yes → 30 |
| S000000009 | A00000402 | C0000004 | WDR | 23381 | -100 | yes (volume only) |
| S000000010 | A00000501 | C0000005 | FEE | 23382 | 150 | yes → 150 |
| S000000011 | A00000602 | C0000006 | INT | 23406 | -7.25 | yes → abs 7.25 |
| S000000012 | A00000602 | C0000006 | FEE | 23383 | 5 | yes → 5 |
| S000000013 | A00000701 | C0000007 | FEE | 23384 | 40 | yes in Step 2, but C0000007 not in INTEREST_INCOME → dropped by `if a` |
| S000000014 | A00000801 | C0000008 | FEE | 23384 | 60 | yes in Step 2, but C0000008 has no accounts → dropped by `if a` |

### CURATED.RISK_SCORES (7 rows; `max(SCORE_DATE) where SCORE_DATE <= 23406` = 23406)

| ACCOUNT | CUSTOMER | EXPECTED_LOSS | SCORE_DATE | in Step 3? |
|---|---|---|---|---|
| A00000101 | C0000001 | 150.25 | 23406 | yes |
| A00000102 | C0000001 | 20 | 23406 | yes |
| A00000101 | C0000001 | 999 | 23375 | **no** — older score date |
| A00000101 | C0000001 | 5000 | 23407 | **no** — after month_end, excluded from the max() too |
| A00000301 | C0000003 | 300 | 23406 | yes |
| A00000601 | C0000006 | 10.5 | 23406 | yes |
| A00000701 | C0000007 | 50 | 23406 | yes, then dropped by `if a` |

## Derivation of REPORTS.CUSTOMER_PNL (6 rows, one per customer in INTEREST_INCOME)

Formulas (SAS lines): LENDING = Σ bal×rate/12 over lending types else 0 (44-46); DEPOSIT = same over
deposit types (48-50); NII = LENDING − DEPOSIT (52); NUM = count(distinct ACCOUNT_ID) (54);
TOTAL_REL = Σ bal (55); FEE = Σ abs(amt) where type='FEE' (69-70); INT = Σ abs(amt) where type='INT'
(72-73); VOL = count(*) (75); ECL = Σ EXPECTED_LOSS at latest score date (89-92);
OPERATING = NUM×15 (109); REVENUE = sum(NII, FEE, 0) — SAS `sum()` skips missing (113);
NET = REVENUE − OPERATING − coalesce(ECL,0) (117); ROA = NET×12/TOTAL_REL if TOTAL_REL > 0 else missing
(121-124); tier ≥500 Highly Profitable / ≥100 Profitable / ≥0 Marginal / else Unprofitable (129-132).

| Customer | LENDING | DEPOSIT | NII | NUM | TOTAL_REL | FEE | INT | VOL | ECL | OPER | REVENUE | NET | ROA | TIER |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0000001 (RET, NE, B001) | 240000×0.06/12 = **1200** | 12000×0.01/12 = **10** | **1190** | 2 | **252000** | 25+15 = **40** | **3.5** | **5** | 150.25+20 = **170.25** | 30 | 1190+40 = **1230** | 1230−30−170.25 = **1029.75** | 1029.75×12/252000 = **0.0490357143** | Highly Profitable |
| C0000002 (PREM, W, B002) | **0** | 50000×0.03/12 + 100000×0.048/12 = 125+400 = **525** | **−525** | 2 | **150000** | . | . | . | . | 30 | sum(−525, ., 0) = **−525** | −525−30−0 = **−555** | −555×12/150000 = **−0.0444** | Unprofitable |
| C0000003 (SMB, MW, B001) | 20000×./12 = **.** (only lending row, missing rate) | **0** (the single row contributes `else 0`) | . − 0 = **.** | 1 | **20000** | **12** | **0** | **1** | **300** | 15 | sum(., 12, 0) = **12** | 12−15−300 = **−303** | −303×12/20000 = **−0.1818** | Unprofitable |
| C0000004 (PB, NE, B001) | 10000×0.06/12 = **50** | 2000×0.03/12 = **5** | **45** | 2 | **12000** | **30** | **0** | **2** | . | 30 | **75** | 75−30−0 = **45** | 45×12/12000 = **0.045** | Marginal |
| C0000005 (blank, SE, B003) | 0×0.2/12 = **0** | **0** | **0** | 1 | **0** | **150** | **0** | **1** | . | 15 | **150** | 150−15−0 = **135** | TOTAL_REL not > 0 → **.** | Profitable |
| C0000006 (max→PREM, W, B002) | 30000×0.08/12 = **200** | 20000×0.03/12 = **50** | **150** | 2 | **50000** | **5** | **7.25** | **2** | **10.5** | 30 | **155** | 155−30−10.5 = **114.5** | 114.5×12/50000 = **0.02748** | Profitable |

`REPORT_MONTH = '202401'` on every row. C0000007 and C0000008 are absent (`if a`).

## Derivation of REPORTS.SEGMENT_PROFITABILITY (CLASS CUSTOMER_SEGMENT, NWAY; C0000005 dropped — blank class)

| SEGMENT | N_CUSTOMERS | TOTAL_REVENUE | OPERATING_COST | TOTAL_ECL | NET_PROFIT | TOTAL_RELATIONSHIP | AVG_PROFIT_PER_CUSTOMER |
|---|---|---|---|---|---|---|---|
| PB (C4) | 1 | 75 | 30 | . (all missing) | 45 | 12000 | 45 |
| PREM (C2, C6) | 2 | −525+155 = −370 | 60 | 10.5 | −555+114.5 = −440.5 | 200000 | −220.25 |
| RET (C1) | 1 | 1230 | 30 | 170.25 | 1029.75 | 252000 | 1029.75 |
| SMB (C3) | 1 | 12 | 15 | 300 | −303 | 20000 | −303 |

## Derivation of REPORTS.BRANCH_PROFITABILITY (CLASS BRANCH_ID REGION_CODE, NWAY)

| BRANCH | REGION | N_CUSTOMERS | TOTAL_REVENUE | OPERATING_COST | TOTAL_ECL | NET_PROFIT |
|---|---|---|---|---|---|---|
| B001 | MW (C3) | 1 | 12 | 15 | 300 | −303 |
| B001 | NE (C1, C4) | 2 | 1230+75 = 1305 | 60 | 170.25 | 1029.75+45 = 1074.75 |
| B002 | W (C2, C6) | 2 | −370 | 60 | 10.5 | −440.5 |
| B003 | SE (C5) | 1 | 150 | 15 | . | 135 |
