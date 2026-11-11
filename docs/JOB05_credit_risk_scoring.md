# JOB05 — credit_risk_scoring.sas → Snowflake SQL

| | |
|---|---|
| SAS source | `ts-sas-legacy-analytics/Programs/Banking/credit_risk_scoring.sas` (macro `%credit_risk_scoring(score_date=&CURR_DT, model_id=CRM-2023-Q4-v2)`) |
| Batch | `BatchJobs/run_daily_banking.sas` step 3; weekly Sunday 02:00 Control-M `BANK_WEEKLY_01` |
| Target | `snowflake_sql/JOB05_CREDIT_RISK_SCORING.sql` (**Snowflake SQL**) |
| Orchestration | `TASK_WEEKLY_CREDIT_RISK` in `snowflake_sql/orchestration/tasks.sql` already points at `@SQL_STAGE/credit_risk_scoring.sql` (owned by the integration PR — not edited here) |
| Inputs | `STG_BANK.CUST_ACCOUNTS_DAILY`, `ORA_DW.BUREAU_SCORES`, `ORA_DW.PAYMENT_HISTORY`, `ORA_DW.COLLATERAL` |
| Outputs | `CURATED.RISK_SCORES` (append), `CURATED.RISK_MIGRATION` (append), `REPORTS.RISK_SUMMARY` (replace) |
| Business date | `CURR_DT=31JAN2024` → `$score_date = 2024-01-31`; `$model_id = 'CRM-2023-Q4-v2'` |
| Parity | **PASS** — 43/43 rules against the Docker-SAS golden baseline (`sample_data/Scenario_credit_risk_scoring/reconciliation.md`) |

## Why Snowflake SQL, not Snowpark

Steps 1, 3 and 5 of the SAS program are `PROC SQL` / `PROC MEANS` — set-based by
construction. The only DATA step (step 2, scorecard) is strictly row-wise: every
statement is an `if/else if` ladder or arithmetic on the current observation.
There is no `RETAIN`, no `first./last.`, no hash object, no `OUTPUT` to multiple
datasets and no `BY`-group state, so each statement maps 1:1 to a `CASE`
expression. SQL is therefore the faithful *and* simplest target; Snowpark would
add a Python layer with no semantic benefit.

## Construct mapping

SAS line numbers refer to `Programs/Banking/credit_risk_scoring.sas`; target
lines refer to `snowflake_sql/JOB05_CREDIT_RISK_SCORING.sql`.

| SAS construct | SAS lines | Snowflake equivalent | Target lines |
|---|---|---|---|
| `%macro credit_risk_scoring(score_date=&CURR_DT, model_id=CRM-2023-Q4-v2)` + `%credit_risk_scoring(score_date=&CURR_DT)` | 18, 270 | Session variables `$score_date` (DATE) and `$model_id` set by the caller/task | 48-53, 259-260 |
| `%lock(CURATED.RISK_SCORES)` / `%lock(..., unlock)` | 229, 234, 236, 241 | No-op: Snowflake DML is transactional, no member-level lock exists | 291-295 |
| `proc sql; create table WORK.SCORE_INPUT as select ... left join ...` | 32-86 | `CREATE OR REPLACE TEMPORARY TABLE WORK_SCORE_INPUT AS SELECT ... LEFT JOIN ...` | 57-116 |
| Correlated sub-select `b.SCORE_DATE = (select max(SCORE_DATE) ... where CUSTOMER_ID = b.CUSTOMER_ID and SCORE_DATE <= "&score_date"d)` | 73-78 | CTE `bureau_latest` with `MAX(CASE WHEN SCORE_DATE <= $score_date THEN SCORE_DATE END) OVER (PARTITION BY CUSTOMER_ID)` joined on `b.SCORE_DATE = b.MAX_SCORE_DATE` | 58-66, 108-110 |
| `case when c.COLLATERAL_VALUE > 0 then a.CURRENT_BALANCE / c.COLLATERAL_VALUE else . end as LTV format=8.4` | 67-71 | `CASE WHEN c.COLLATERAL_VALUE > 0 THEN ... ELSE NULL END AS LTV` (format not applied — raw value stored, as SAS stores it) | 100-105 |
| `where a.SNAPSHOT_DATE = "&score_date"d and a.ACCOUNT_TYPE in (...)` | 83-85 | `WHERE a.SNAPSHOT_DATE = $score_date AND a.ACCOUNT_TYPE IN (...)` | 115-116 |
| `data WORK.SCORED; set WORK.SCORE_INPUT;` | 92-94 | `CREATE OR REPLACE TEMPORARY TABLE WORK_SCORED AS WITH woe AS (...), odds AS (...), scored AS (...) SELECT ...` | 122-262 |
| `INTERCEPT = -3.2145;` | 96 | `-3.2145::FLOAT AS INTERCEPT` | 126 |
| FICO `if not missing(FICO_SCORE) then do; if FICO_SCORE >= 760 then WOE_FICO = -1.204; ... end; else WOE_FICO = 0.198;` | 98-107 | `CASE WHEN FICO_SCORE IS NULL THEN 0.198 WHEN FICO_SCORE >= 760 THEN -1.204 ... END` | 129-137 |
| Utilization `if not missing(UTILIZATION_PCT) ... else WOE_UTIL = 0;` | 109-118 | `CASE WHEN UTILIZATION_PCT IS NULL THEN 0 WHEN <= 10 ... END` | 140-148 |
| Payment `if not missing(PMT_LATE_90_12MO) ... = 0 / = 1 / else 1.567; else 0` | 120-126 | `CASE WHEN PMT_LATE_90_12MO IS NULL THEN 0 WHEN = 0 THEN -0.678 WHEN = 1 THEN 0.445 ELSE 1.567 END` | 151-156 |
| Account age `if not missing(ACCT_AGE_MONTHS) ... else 0` | 128-135 | `CASE WHEN ACCT_AGE_MONTHS IS NULL THEN 0 WHEN >= 120 ... END` | 159-165 |
| LTV `if ACCOUNT_TYPE in ('MTG','AUTO','HELC') and not missing(LTV) then ... else WOE_LTV = 0;` | 137-147 | `CASE WHEN ACCOUNT_TYPE NOT IN ('MTG','AUTO','HELC') THEN 0 WHEN LTV IS NULL THEN 0 WHEN LTV <= 0.60 ... END` | 168-175 |
| `LOG_ODDS = INTERCEPT + 0.412*WOE_FICO + ...;` | 149-155 | Same expression in CTE `odds` | 180-187 |
| `PD = 1 / (1 + exp(-LOG_ODDS));` | 157 | `1 / (1 + EXP(-LOG_ODDS)) AS PD` | 193 |
| LGD `if secured then do; if not missing(LTV) then LGD = max(0, min(1, (LTV-0.5)*0.8)); else LGD = 0.40; end; else if 'CC' then 0.75; else 0.50;` | 160-169 | Nested `CASE` with `GREATEST(0, LEAST(1, (LTV - 0.5) * 0.8))` | 196-205 |
| EAD `if ACCOUNT_TYPE in ('CC','LOC','HELC') then EAD = CURRENT_BALANCE + 0.50*(CREDIT_LIMIT - CURRENT_BALANCE); else EAD = CURRENT_BALANCE;` | 171-176 | `CASE WHEN ACCOUNT_TYPE IN ('CC','LOC','HELC') THEN ... ELSE CURRENT_BALANCE END AS EAD` (NULL propagates like SAS missing) | 208-212 |
| `EXPECTED_LOSS = PD * LGD * EAD;` | 178-180 | `PD * LGD * EAD AS EXPECTED_LOSS` | 246 |
| Risk rating `if PD < 0.005 then NEW_RISK_RATING = 1; else if PD < 0.01 ... else 7;` | 182-189 | `CASE WHEN PD < 0.005 THEN 1 ... ELSE 7 END AS NEW_RISK_RATING` | 249-257 |
| `SCORE_DATE = "&score_date"d; MODEL_ID = "&model_id"; SCORE_TIMESTAMP = datetime();` | 191-194 | `$score_date::DATE`, `$model_id::VARCHAR`, `CURRENT_TIMESTAMP()` | 259-261 |
| `drop INTERCEPT WOE_FICO WOE_UTIL WOE_DPD WOE_AGE WOE_LTV LOG_ODDS;` | 196 | Intermediate columns are not projected in the final `SELECT` | 216-262 |
| `proc sql; create table WORK.RISK_MIGRATION as select ... inner join ... where ...` | 202-224 | `CREATE OR REPLACE TEMPORARY TABLE WORK_RISK_MIGRATION AS SELECT ... INNER JOIN ... WHERE ...` | 268-287 |
| `case when a.RISK_RATING is null then 'NEW' when s.NEW_RISK_RATING < a.RISK_RATING then 'UPGRADE' when > then 'DOWNGRADE' else 'STABLE' end as MIGRATION_DIRECTION length=10` | 205-215 | Same `CASE ... END::VARCHAR(10)` | 274-279 |
| `where a.SNAPSHOT_DATE = "&score_date"d and (a.RISK_RATING ne s.NEW_RISK_RATING or a.RISK_RATING is null)` | 217-223 | `WHERE a.SNAPSHOT_DATE = $score_date AND (a.RISK_RATING <> s.NEW_RISK_RATING OR a.RISK_RATING IS NULL)` | 285-287 |
| `proc append base=CURATED.RISK_SCORES data=WORK.SCORED force;` | 231 | `CREATE TABLE IF NOT EXISTS ... AS SELECT * ... WHERE 1=0; INSERT INTO ... SELECT *` (append, never replace) | 297-305 |
| `proc append base=CURATED.RISK_MIGRATION data=WORK.RISK_MIGRATION force;` | 238 | same pattern | 302-305 |
| `proc means noprint nway; class ACCOUNT_TYPE NEW_RISK_RATING; var PD LGD EAD EXPECTED_LOSS; output out=REPORTS.RISK_SUMMARY(drop=_TYPE_ _FREQ_) n=N_ACCOUNTS mean(PD)=AVG_PD mean(LGD)=AVG_LGD sum(EAD)=TOTAL_EAD sum(EXPECTED_LOSS)=TOTAL_EL;` | 246-256 | `CREATE OR REPLACE TABLE REPORTS.RISK_SUMMARY AS SELECT ACCOUNT_TYPE, NEW_RISK_RATING, COUNT(PD) AS N_ACCOUNTS, COUNT(LGD) AS LGD, COUNT(EAD) AS EAD, COUNT(EXPECTED_LOSS) AS EXPECTED_LOSS, AVG(PD), AVG(LGD), SUM(EAD), SUM(EXPECTED_LOSS) ... WHERE class cols NOT NULL GROUP BY ... ORDER BY ...` | 319-334 |
| `proc datasets lib=WORK nolist; delete SCORE_INPUT SCORED RISK_MIGRATION;` | 264-266 | `DROP TABLE IF EXISTS WORK_*` | 338-340 |
| `%put NOTE: ... %nobs(WORK.SCORED)` logging | 258-262 | Not reproduced (log-only side effects) | — |

## Reproduced legacy quirks (flagged, not fixed)

1. **`PROC MEANS n=N_ACCOUNTS` only renames the first VAR.** SAS emits the N
   statistic of `LGD`, `EAD` and `EXPECTED_LOSS` under the analysis-variable
   names themselves, so `REPORTS.RISK_SUMMARY` has columns `LGD`, `EAD`,
   `EXPECTED_LOSS` that are *counts* (always equal to `N_ACCOUNTS`), not
   averages/sums. The golden export confirms the 10-column shape
   `ACCOUNT_TYPE, NEW_RISK_RATING, N_ACCOUNTS, LGD, EAD, EXPECTED_LOSS, AVG_PD, AVG_LGD, TOTAL_EAD, TOTAL_EL`;
   the conversion reproduces it exactly (target lines 319-334). Any downstream
   consumer reading `RISK_SUMMARY.EAD` as an exposure amount is getting a count.
2. **`PROC APPEND ... FORCE` with no de-duplication by `SCORE_DATE`.**
   Re-running the job for the same date doubles the rows in
   `CURATED.RISK_SCORES` / `CURATED.RISK_MIGRATION`. Reproduced as
   `INSERT INTO` (no `MERGE`, no delete-by-date). The scenario runner drops the
   curated tables first because the golden run was a first run on an empty
   library.
3. **Bureau tie duplication.** If a customer had two `BUREAU_SCORES` rows on
   the same (latest) `SCORE_DATE`, SAS's correlated sub-select would duplicate
   the account row; the window-MAX join does the same. The seed has no ties
   (verified: 236 unique `ACCOUNT_ID`s), so no effect on the golden run.
4. **Missing `COLLATERAL_VALUE` ⇒ `LTV` missing ⇒ secured `LGD = 0.40`, `WOE_LTV = 0`.**
   A missing collateral value fails `> 0` in SAS and `IS NULL` in SQL. Also a
   collateral value of exactly `0` yields missing LTV (not infinity / not 0).
   Reproduced; 108 of 236 scored accounts have no collateral row.
5. **`UTILIZATION_PCT` is missing for 115 of 236 scored accounts** (JOB01 only
   computes it when `CREDIT_LIMIT > 0`). SAS silently assigns `WOE_UTIL = 0`
   (the "neutral" WOE), not the population-average WOE used for missing FICO.
   Reproduced.
6. **Two different missing-value defaults in the same scorecard.** Missing
   FICO ⇒ `0.198` (population average); missing utilization / DPD / age / LTV
   ⇒ `0`. Reproduced verbatim.
7. **`EAD` for revolving products ignores negative headroom.** For
   `CC/LOC/HELC`, `EAD = CURRENT_BALANCE + 0.5 * (CREDIT_LIMIT - CURRENT_BALANCE)`
   is applied even when `CREDIT_LIMIT = 0` or `CURRENT_BALANCE > CREDIT_LIMIT`,
   which *reduces* EAD below the balance. Reproduced (no `GREATEST`).
8. **Migration compares against the *same* snapshot's `RISK_RATING`.** Step 3
   joins `WORK.SCORED` back to `STG_BANK.CUST_ACCOUNTS_DAILY` for the same
   `SNAPSHOT_DATE`, so `PREV_RATING` is the rating carried on the day's
   snapshot, not the previous `CURATED.RISK_SCORES` run. Reproduced.
9. **`STABLE` rows never reach `CURATED.RISK_MIGRATION`.** The `WHERE` keeps
   only changed/new ratings, so the `'STABLE'` arm of the `CASE` is dead code.
   Reproduced (golden has 0 STABLE rows; so does the target).
10. **`SCORE_TIMESTAMP = datetime()`** is a SAS datetime (seconds since
    1960-01-01); Snowflake stores `CURRENT_TIMESTAMP()` as `TIMESTAMP_LTZ`.
    Non-deterministic by nature; excluded from reconciliation. The golden
    export shows the SAS value `2109989623` (= 2026-11-11 SAS datetime).
11. **No rating 1, 2, 5, 6 or 7 is ever produced on the golden data.** With
    these coefficients PD ranges 0.0172–0.0690, so only ratings 3 and 4 appear
    (`Distinct Count [NEW_RISK_RATING] = 2`). Not a conversion defect, but a
    sign the scorecard bands are not exercised by the seed.
12. **Presentation formats are dropped.** `LTV format=8.4`, `PD percent8.4`,
    `EXPECTED_LOSS dollar18.2`, `SCORE_DATE date9.`, `SCORE_TIMESTAMP datetime20.`
    are display-only in SAS; raw values are stored in Snowflake, exactly as the
    SAS datasets store them.
13. **`%lock` is a no-op in the Docker estate** (`docker/shims/lock.sas`) and is
    not needed in Snowflake. No lock-timeout / `onFail=ABORT` behaviour is
    carried over.
14. **Column order of `CURATED.RISK_SCORES` follows SAS PDV order** (input
    columns, then `LTV, PD, LGD, EAD, EXPECTED_LOSS, NEW_RISK_RATING, SCORE_DATE, MODEL_ID, SCORE_TIMESTAMP`),
    matching the golden export column-for-column.

## How the Snowflake-side CSVs were produced

**Locally with DuckDB, not in Snowflake.** The environment has
`SNOWFLAKE_DEMO_*` credentials and the connection authenticates, but every
statement fails with
`000666 (57014): Your account is suspended due to lack of payment method`, so
the Snowflake engine could not be exercised. The runner therefore fell back to
its DuckDB engine:

```bash
python3 sample_data/Scenario_credit_risk_scoring/run_local.py \
    --golden ~/golden --estate ~/repos/ts-sas-legacy-analytics --engine duckdb
python3 verify/reconcile.py --scenario Scenario_credit_risk_scoring \
    --sas-golden ~/golden --report sample_data/Scenario_credit_risk_scoring/reconciliation.md
```

Input typing (`run_local.py`): character columns keep their SAS `$n` lengths
(`ACCOUNT_ID $12`, `CUSTOMER_ID $10`, `ACCOUNT_TYPE $4`, ...), every SAS numeric
is a 64-bit double, golden SAS date integers are converted with
`DATE '1960-01-01' + n`, and `date9.` seed strings (e.g. `31OCT2023`) are
parsed as dates — mirroring `Data/load_seed_data.sas` and
`Programs/Banking/load_customer_accounts.sas`.

DuckDB dialect shim (the complete list, applied by `DuckDbEngine.rewrite`):

| Snowflake text | DuckDB text | Why |
|---|---|---|
| `$score_date` / `$model_id` | `DATE '2024-01-31'` / `'CRM-2023-Q4-v2'` | DuckDB has no session variables |
| `CURRENT_TIMESTAMP()` | `now()` | DuckDB's `current_timestamp` takes no parentheses |
| `FLOAT` | `DOUBLE` | DuckDB `FLOAT` is 32-bit REAL; Snowflake `FLOAT` is 64-bit (first run without this produced `417924.83203125` instead of `417924.83`) |

Nothing else in the `.sql` file is rewritten. When the Snowflake account is
re-activated, `--engine snowflake` runs the identical file inside private
schemas `INSURANCE_DEMO.JOB05_CRS_{STG_BANK,ORA_DW,CURATED,REPORTS}` (the
only rewrite being that schema prefix, the equivalent of a `LIBNAME` remap).

## Validation coverage (`validations.csv`, 43 rules)

Every output table has Row Count, Sum Amount of its main amount column(s),
Distinct Count / Not Null / Uniqueness of the key, and the control-total
checks from `controls_extra.csv`: `RISK_SCORES` SUM_PD / SUM_EAD /
SUM_EXPECTED_LOSS, `RISK_MIGRATION` UPGRADE=76 / DOWNGRADE=113 / NEW=6
(+ STABLE=0), `RISK_SUMMARY` SUM_TOTAL_EL, plus quirk checks (the four
count columns are equal in all 12 groups; no STABLE rows; `PREV_RATING`
null count = NEW count). All 43 pass; see `reconciliation.md`.

## Open questions

1. **Schema naming.** SAS librefs are `STG_BANK`, `ORA_DW`, `CURATED`,
   `REPORTS`; existing conversions (JOB01/JOB04) use `STAGING`, `RAW`,
   `CURATED`, `REPORTS` and the orchestration task runs in `FINANCE_DB`. The
   conversion keeps the SAS librefs as schema names (so the golden file names
   line up); the integration owner should confirm the final database/schema
   map and whether `ORA_DW` becomes an external/Oracle-replicated schema.
2. **Append semantics on re-run.** Should Snowflake keep SAS's blind
   `PROC APPEND` (duplicates on re-run) or guard with
   `DELETE ... WHERE SCORE_DATE = $score_date` first? Kept blind to match SAS;
   a guard would be a behaviour change and needs business sign-off.
3. **`RISK_SUMMARY` count columns named `LGD`/`EAD`/`EXPECTED_LOSS`** (quirk 1).
   Keep the misleading SAS column names for compatibility, or rename to
   `N_LGD`/`N_EAD`/`N_EL` once downstream consumers are known?
4. **`SCORE_TIMESTAMP` type.** SAS stores a numeric datetime; the conversion
   uses `TIMESTAMP_LTZ`. Confirm whether any consumer expects the SAS numeric.
5. **Snowflake execution evidence is outstanding** because the demo account is
   suspended; the SQL has only been executed through the DuckDB shim. Re-run
   `run_local.py --engine snowflake` once the account is active and attach the
   Snowflake-side CSVs.
6. **`PREV_YM=202401` equals `CURR_YM`** in `Config/autoexec_local.sas`; not
   used by this program but noted since the same config drives the batch.
