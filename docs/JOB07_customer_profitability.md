# JOB07 — customer_profitability.sas → Snowflake SQL

Source: `ts-sas-legacy-analytics/Programs/Reports/customer_profitability.sas` (Control-M `BANK_MONTHLY_03`,
monthly 10th business day; not part of any `BatchJobs/` orchestrator; `report_month` defaults to `&PREV_YM`,
which `Config/autoexec_local.sas` sets to `202401`).
Target: `snowflake_sql/JOB07_CUSTOMER_PROFITABILITY.sql`. Scenario: `sample_data/Scenario_customer_profitability/`.

## BLOCKING legacy defect (reproduced, not fixed)

Step 2 (`customer_profitability.sas:65-80`) selects `t.CUSTOMER_ID` from `CURATED.DAILY_TRANSACTIONS`.
That table has no `CUSTOMER_ID`: `daily_transaction_processing.sas:207-208` does
`proc append base=CURATED.DAILY_TRANSACTIONS data=WORK.TXN_WITH_BALANCE force`, and `FORCE` drops the
enrichment columns (`CUSTOMER_ID`, `CUSTOMER_SEGMENT`, …, `RUNNING_BALANCE`) that are not in the base
table created by `Data/load_seed_data.sas` (10 columns, see `curated__daily_transactions.csv` in the
golden tarball). SAS stops with
`ERROR: PROC SQL: the following columns were not found in the contributing tables: CUSTOMER_ID`
and all three REPORTS tables are left unwritten (`export_extra_failed.log`).

The Snowflake SQL references the same column, so on the estate data it fails the same way
(`run_local.py --inputs <golden dir>` → `Table "t" does not have a column named "CUSTOMER_ID"`, exit 2).
**Proposal only (not applied):** join `CURATED.DAILY_TRANSACTIONS t` to `STAGING.CUST_ACCOUNTS_DAILY a`
on `ACCOUNT_ID` (month-end snapshot) and use `a.CUSTOMER_ID`, or add `CUSTOMER_ID` to the curated base
table. Either changes the program's meaning (transactions of accounts without a month-end snapshot would
be dropped or kept), so it needs a business decision.

## Why Snowflake SQL, not Snowpark

Every step is set-based: three `PROC SQL` aggregations, one `MERGE ... BY` with unique keys per input
(a one-to-one merge = LEFT JOIN), straight-line DATA step arithmetic with no `RETAIN`, no `first./last.`,
no hash objects, no multi-output `OUTPUT`, and two `PROC MEANS NWAY` summaries (GROUP BY). The inventory
proposal (SQL) is confirmed by the source.

## Construct mapping

| SAS construct (customer_profitability.sas) | Snowflake equivalent (JOB07_CUSTOMER_PROFITABILITY.sql) |
|---|---|
| `%macro customer_profitability(report_month=&PREV_YM)` :19, `%parmv` :21 | session variable `$report_month` (comment :62) |
| `%let month_start = %sysfunc(inputn(&report_month.01, yymmdd8.))` :24 | `TO_DATE($report_month \|\| '01', 'YYYYMMDD')` :81, :105 |
| `%let month_end = %sysfunc(intnx(month, "&month_start"d, 0, E))` :25 | `LAST_DAY(TO_DATE(...))` :81, :106, :117 |
| Step 1 `PROC SQL ... from STG_BANK.CUST_ACCOUNTS_DAILY where SNAPSHOT_DATE = "&month_end"d group by CUSTOMER_ID` :35-60 | CTE `interest_income` :66-82 |
| `max(a.CUSTOMER_SEGMENT/REGION_CODE/BRANCH_ID)` :40-42 | `MAX(...)` :69-71 (string maximum, see quirk Q4) |
| `sum(case when ACCOUNT_TYPE in (lending) then CURRENT_BALANCE*INTEREST_RATE/12 else 0 end)` :44-46, deposits :48-50 | `SUM(CASE ... END)` :72-77 |
| `calculated LENDING_INCOME - calculated DEPOSIT_COST` :52 | CTE `interest_income_calc` :84-93 |
| `count(distinct ACCOUNT_ID)` :54, `sum(CURRENT_BALANCE)` :55 | `COUNT(DISTINCT ...)`, `SUM(...)` :78-79 |
| Step 2 `from CURATED.DAILY_TRANSACTIONS t where TRANSACTION_DATE between ... group by t.CUSTOMER_ID` :65-80 | CTE `fee_income` :96-108 — **same missing column, fails identically** |
| `sum(case when TRANSACTION_TYPE='FEE' then abs(TRANSACTION_AMOUNT) else 0 end)` :69-70, `'INT'` :72-73, `count(*)` :75 | :99-103 |
| Step 3 `where SCORE_DATE = (select max(SCORE_DATE) ... where SCORE_DATE <= "&month_end"d)` :91-92 | `WHERE EQUAL_NULL(r.SCORE_DATE, (SELECT MAX ...))` :115-117 (SAS `=` matches missing=missing) |
| `sum(r.EXPECTED_LOSS) as TOTAL_ECL group by CUSTOMER_ID` :89-93 | :110-119 |
| Step 4 `merge INTEREST_INCOME(in=a) FEE_INCOME(in=b) ECL(in=c); by CUSTOMER_ID; if a;` :101-106 | `FROM interest_income_calc i LEFT JOIN fee_income f ... LEFT JOIN ecl e ...` :140-142 |
| `OPERATING_COST = NUM_ACCOUNTS * 15` :109 | :137 |
| `TOTAL_REVENUE = sum(NET_INTEREST_INCOME, FEE_INCOME, 0)` :113 | `COALESCE(NII,0) + COALESCE(FEE_INCOME,0)` :139 |
| `NET_PROFIT = TOTAL_REVENUE - OPERATING_COST - coalesce(TOTAL_ECL, 0)` :117 | CTE `pnl_profit` :144-150 |
| `if TOTAL_RELATIONSHIP > 0 then ROA = NET_PROFIT*12/TOTAL_RELATIONSHIP; else ROA = .;` :121-124 | `CASE WHEN TOTAL_RELATIONSHIP > 0 THEN ... ELSE NULL END` :169-171 |
| `length PROFIT_TIER $20; if NET_PROFIT >= 500 ... else 'Unprofitable'` :128-132 | `CAST(CASE ... END AS VARCHAR(20))` :173-176 |
| `REPORT_MONTH = "&report_month"` :134 | `CAST($report_month AS VARCHAR(6))` :178 |
| `format ... dollar18.2 / percent8.4` :46,50,53,55,71,74,89,110,114,118,125 | none — presentation only, raw values stored |
| Step 5 `proc means nway; class CUSTOMER_SEGMENT; var ...; output n=N_CUSTOMERS sum= mean(NET_PROFIT)=AVG_PROFIT_PER_CUSTOMER` :140-148 | `GROUP BY CUSTOMER_SEGMENT` with `WHERE CUSTOMER_SEGMENT IS NOT NULL`, `COUNT(TOTAL_REVENUE)`, `SUM(...)`, `AVG(NET_PROFIT)` :185-198 |
| `proc means nway; class BRANCH_ID REGION_CODE; ... n=N_CUSTOMERS sum=` :150-157 | `GROUP BY BRANCH_ID, REGION_CODE` with both `IS NOT NULL` :201-213 |
| `drop=_TYPE_ _FREQ_` :143, :153 | not produced |
| `%export_xlsx(data=REPORTS.SEGMENT_PROFITABILITY, file=&REPORT_PATH/PROFITABILITY_&report_month..xlsx)` :159-163 | **not migrated** (file-system side effect; flagged) |
| `%nobs(REPORTS.CUSTOMER_PNL)` log note :167, `proc datasets lib=WORK ... delete` :170-172 | no equivalent needed |

## Reproduced legacy quirks

- **Q1 (blocking)** `t.CUSTOMER_ID` does not exist on `CURATED.DAILY_TRANSACTIONS` — program fails in SAS; the SQL fails identically. Not fixed.
- **Q2** `ORA_DW.COST_OF_FUNDS` is listed in the header (:8) but never used in the body; "cost of funds" is in fact the deposit interest (`DEPOSIT_COST`). Not referenced in the SQL either.
- **Q3** `OPERATING_COST = NUM_ACCOUNTS * 15` is a hard-coded allocation (comment says "simplified").
- **Q4** "Primary segment/region/branch (from largest account)" (:39) is implemented as `max()` — an alphabetical string maximum, not the largest account (e.g. `PREM` beats `PB`, `W` beats `NW`). Reproduced as `MAX()`.
- **Q5** `count(distinct ACCOUNT_ID)` vs `sum(CURRENT_BALANCE)`: duplicate account rows at the snapshot would be counted once but summed twice. Reproduced.
- **Q6** `TOTAL_REVENUE = sum(NII, FEE_INCOME, 0)` treats a missing `NET_INTEREST_INCOME` (customer whose only accounts are lending with missing rate/balance) as 0, while `NET_INTEREST_INCOME` itself stays missing on the row. Reproduced with `COALESCE`.
- **Q7** `FEE_INCOME`, `INT_CREDITED`, `TXN_VOLUME` are missing (not 0) for customers with no in-month transactions; `TOTAL_ECL` is missing for customers without a score. Reproduced (LEFT JOIN, no COALESCE on the stored columns).
- **Q8** ROA is missing when `TOTAL_RELATIONSHIP <= 0` or missing; PROFIT_TIER falls to `Unprofitable` for a missing `NET_PROFIT`. Reproduced via CASE/NULL semantics.
- **Q9** Customers present in transactions or risk scores but without a month-end account row are dropped (`if a`). Reproduced (LEFT JOIN from INTEREST_INCOME).
- **Q10** Step 3 compares `SCORE_DATE` with SAS `=`: a missing subquery result (no scores on/before month-end) would select rows with a missing `SCORE_DATE`. Reproduced with `EQUAL_NULL`.
- **Q11** PROC MEANS drops observations with a missing CLASS value: customers with blank segment vanish from `SEGMENT_PROFITABILITY` (but not from `BRANCH_PROFITABILITY`), and vice-versa for blank branch/region. `n=N_CUSTOMERS` is the N of `TOTAL_REVENUE` (first VAR variable), the sum of an all-missing group is missing. Reproduced.
- **Q12** `CURR_YM` and `PREV_YM` are both `202401` in `Config/autoexec_local.sas`, so the "previous month" report runs on the current month. Reproduced by running with `report_month = 202401`.
- **Q13** `%export_xlsx` writes `PROFITABILITY_202401.xlsx` (sheet `By_Segment`) to `&REPORT_PATH` — a file side effect outside the table migration. Flagged, not reproduced.
- **Q14 (runtime observation)** the OpenSAS container log shows `Invalid numeric data, 'MTG'` / `Character values have been converted to numeric values` notes for Step 1's `ACCOUNT_TYPE in (...)` — an artefact of the container runtime, not SAS 9.4 semantics; nothing to reproduce.

## How the Snowflake-side CSVs were produced

Snowflake credentials are present (`SNOWFLAKE_DEMO_ACCOUNT` / `_USER` / `_PRIVATE_KEY`, key-pair auth
works, role `DEMO_ENGINEER`), but **the demo account is suspended**: every DDL returns
`000666 (57014): Your account is suspended due to lack of payment method` (and the role lacks
`CREATE DATABASE`). The conversion was therefore executed **locally in DuckDB** by
`sample_data/Scenario_customer_profitability/run_local.py`, which runs the `.sql` file verbatim except for
three documented shims: `$report_month` → `getvariable('report_month')`, a `TO_DATE(x,'YYYYMMDD')` macro
over `strptime`, and an `EQUAL_NULL(a,b)` macro over `IS NOT DISTINCT FROM`. Inputs are typed as SAS types
them (char → VARCHAR with blank = NULL, numeric → DOUBLE, SAS date integers → DATE).

## Reconciliation

No SAS golden exists for the three outputs (the program fails), so the baseline is synthetic:
`baseline_inputs/` + hand-computed `baseline_expected/` (derivation in `BASELINE_ASSUMPTIONS.md`).

```
python3 sample_data/Scenario_customer_profitability/run_local.py
python3 verify/reconcile.py --scenario Scenario_customer_profitability \
    --sas-golden sample_data/Scenario_customer_profitability/baseline_expected \
    --report sample_data/Scenario_customer_profitability/reconciliation.md
```

Result: 58 rules, all PASS (`reconciliation.md`). Status: **PASS (synthetic baseline)**.

## Open questions

1. Q1: which fix is wanted — join through `STAGING.CUST_ACCOUNTS_DAILY` on `ACCOUNT_ID`, or add `CUSTOMER_ID` to `CURATED.DAILY_TRANSACTIONS`? Until decided the job cannot produce output on estate data.
2. Formats: if `CUSTOMER_SEGMENT`/`REGION_CODE` keep `$CUSTSEG.`/`$REGION.` through PROC SQL `max()`, PROC MEANS would group by the *formatted* value and the `OTHER = 'Unclassified'/'Unknown'` buckets would merge every unmapped code into one class level (output column showing the first internal value). The golden exports show raw codes everywhere, so the SQL groups by raw code; confirm with a SAS 9.4 run.
3. Should `%export_xlsx` become a `COPY INTO @stage` / Snowsight export, or is the table enough?
4. Should `REPORTS.*` be `CREATE OR REPLACE` (as here, like JOB04) or appended per `REPORT_MONTH`? SAS overwrites the dataset each run.
