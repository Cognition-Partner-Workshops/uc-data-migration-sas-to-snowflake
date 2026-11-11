# JOB02 — `daily_transaction_processing.sas` → Snowpark Python

| | |
|---|---|
| SAS source | `ts-sas-legacy-analytics/Programs/Banking/daily_transaction_processing.sas` (`%daily_transaction_processing(txn_date=&CURR_DT)`) |
| Batch | `BatchJobs/run_daily_banking.sas` step 2 (after JOB01 `load_customer_accounts`, before `credit_risk_scoring`) |
| Target | `snowpark/JOB02_daily_transaction_processing.py` — `daily_transaction_processing(session, txnDate, rawBank, stgBank, curated)`; `handler()` is the stored-procedure entry point |
| Inputs | `RAW_BANK.TXN_FEED_<YYYYMMDD>`, `STG_BANK.CUST_ACCOUNTS_DAILY` (JOB01), `CURATED.DAILY_TRANSACTIONS` (90-day history) |
| Outputs | `CURATED.DAILY_TRANSACTIONS` (append), `CURATED.TXN_ANOMALIES` (append, only when ≥1 anomaly), `CURATED.RUNNING_BALANCES` (replace) |
| Business date | `CURR_DT=31JAN2024` → `txnDate='2024-01-31'`, feed `TXN_FEED_20240131` |
| Parity | **PASS** — 55/55 golden-mode checks (`sample_data/Scenario_daily_transaction_processing/reconciliation.md`) |
| Depends on | harness PR #21 (`verify/reconcile.py --sas-golden`) |

## 1. Why Snowpark Python and not Snowflake SQL

The program is a procedural DATA-step pipeline, which is exactly the case the playbook routes to Snowpark:

* **Multi-output DATA step with reject routing** (SAS 45-97): six `if … then do; REJECT_REASON=…; output WORK.TXN_REJECTED; return; end;` blocks. The *first* failing rule wins and the row never reaches `TXN_VALIDATED`. This is a prioritised CASE plus a two-way split, not a set of independent WHERE filters.
* **`RETAIN` + BY-group `first.ACCOUNT_ID`** (SAS 137-154): a running balance seeded per group and carried across rows.
* **`PROC APPEND … FORCE` whose behaviour depends on the base table's column list** (SAS 207-217): the enrichment columns are *dropped* because the curated history has 10 columns. This needs introspection of the existing table (`proc_append_force()`), which is awkward in pure SQL.
* **`%nobs`-guarded conditional step** (SAS 213): the anomalies append only runs when `nobs > 0`.
* **Step ordering matters**: `TXN_STATS` must read the history *before* today's rows are appended. Snowpark keeps the SAS steps as named DataFrames in the same order and materialises the WORK tables with `cache_result()`.

The set-based pieces (PROC SQL joins, the window-function running balance) still execute as Snowflake SQL under the DataFrame API, so nothing is pulled into Python memory.

## 2. Construct mapping

SAS references are `Programs/Banking/daily_transaction_processing.sas:line`; target references are `snowpark/JOB02_daily_transaction_processing.py:line` unless stated.

| SAS construct | SAS lines | Snowflake equivalent | Target lines |
|---|---|---|---|
| `%macro daily_transaction_processing(txn_date=)` + `%parmv` validation | 19-33 | Python function with `txnDate: str` ('YYYY-MM-DD'); `date.fromisoformat` is the type check | 113-134 |
| `%let txn_ds = TXN_FEED_%sysfunc(putn("&txn_date"d, yymmddn8.))` | 25 | `f"{rawBank}.TXN_FEED_{txnDt.strftime('%Y%m%d')}"` | 133 |
| `%if not %sysfunc(exist(RAW_BANK.&txn_ds)) … %goto ABORT` | 36-43 | `_table_exists()` → early `return "ERROR: …"` | 142-143 |
| `%nobs(RAW_BANK.&txn_ds)` / `%nobs(WORK.…)` | 15, 99-101, 199 | `DataFrame.count()` | 146, 181-182, 298 |
| `length REJECT_REASON $200` + ordered `if … output TXN_REJECTED; return;` chain | 45-93 | one `when().when()…otherwise(NULL::VARCHAR(200))` expression evaluated in SAS order, then `filter(is_not_null)` / `filter(is_null).drop()` | 151-179 |
| `missing(TRANSACTION_ID)` / `missing(ACCOUNT_ID)` on `$` vars | 52, 58 | `_is_missing_char()` = `COALESCE(TRIM(col),'') = ''` (blank **or** NULL) | 77-79 |
| `missing(TRANSACTION_AMOUNT)` on numeric | 64 | `col.is_null()` | 154 |
| `put(TRANSACTION_AMOUNT, dollar18.2)` in `catx` | 71-72 | `TRIM(TO_CHAR(amt, '$999,999,999,999,999.00'))` inside `CONCAT_WS(' ', …)` | 157-162 |
| `put(TRANSACTION_DATE, date9.)` | 86-87 | `UPPER(TO_CHAR(date, 'DDMONYYYY'))` | 168-174 |
| `TRANSACTION_TYPE not in (…)` (missing value is NOT IN → rejected) | 75 | `col.is_null() \| ~col.isin(VALID_TXN_TYPES)` | 164-168 |
| `TRANSACTION_DATE > "&txn_date"d` | 84 | `col("TRANSACTION_DATE") > lit(txnDt)` | 169 |
| `drop REJECT_REASON` (DATA-step DROP applies to both TXN_VALIDATED and TXN_REJECTED) | 96 | `.drop("REJECT_REASON")` on the validated branch; kept on `txnRejected` only for the `%nobs` count, never persisted | 180 |
| PROC SQL `select t.*, a.… from TXN_VALIDATED t left join CUST_ACCOUNTS_DAILY a on t.ACCOUNT_ID = a.ACCOUNT_ID` | 105-128 | account key renamed to `A_ACCOUNT_ID`, `join(how="left")`, projection = `txnValidated.columns + [...]` (one `ACCOUNT_ID`, same column order as `t.*`) | 189-222 |
| `a.CURRENT_BALANCE as PRE_TXN_BALANCE` | 115 | `col("CURRENT_BALANCE").alias("PRE_TXN_BALANCE")` | 196 |
| `case when … end as POST_TXN_BALANCE format=dollar18.2` | 116-124 | `when/when/when/otherwise` (format is display-only; no equivalent) | 200-205 |
| `order by t.ACCOUNT_ID, t.TRANSACTION_DATE, t.TRANSACTION_ID` + `by …` in DATA step | 129, 139 | `Window.partition_by("ACCOUNT_ID").order_by("TRANSACTION_DATE","TRANSACTION_ID")` | 231 |
| `retain RUNNING_BALANCE; if first.ACCOUNT_ID then RUNNING_BALANCE = PRE_TXN_BALANCE;` + per-row `+ amt` / `- abs(amt)` | 141-152 | `FIRST_VALUE(PRE_TXN_BALANCE) OVER (… ROWS UNBOUNDED PRECEDING) + SUM(delta) OVER (same)` where `delta` is the signed CASE; non-matching types add 0 | 232-243 |
| `WORK.TXN_WITH_BALANCE` (intermediate SAS dataset) | 137 | `.cache_result()` → Snowflake temp table | 243 |
| PROC SQL `mean(abs(x))`, `std(abs(x))`, `count(*)` … `where TRANSACTION_DATE >= intnx('day',"&txn_date"d,-90) group by ACCOUNT_ID` | 159-170 | `filter(date >= txnDt - 90 days).group_by("ACCOUNT_ID").agg(AVG(ABS), STDDEV(ABS) (sample), COUNT(1))` | 251-261 |
| `case when s.STD_TXN_AMT > 0 then (abs(amt)-avg)/std else . end as Z_SCORE` | 176-180 | `when(STD > 0, …).otherwise(NULL)` | 265-267 |
| `case when calculated Z_SCORE > 3 … when e.RUNNING_BALANCE < 0 … when WDR and abs(amt) > PRE*0.9 … when missing(CUSTOMER_ID) … else '' end as ANOMALY_TYPE length=20` | 181-189 | `when` chain in the same order; each SAS comparison that can see a missing value gets an explicit `is_null()` branch (SAS missing < any number); `.cast(StringType(20))` | 273-287 |
| `left join WORK.TXN_STATS s on e.ACCOUNT_ID = s.ACCOUNT_ID` | 191-192 | stats key renamed to `S_ACCOUNT_ID`, `join(how="left")`, projection `txnWithBalance.columns + […]` | 264, 288-290 |
| `having ANOMALY_TYPE ne ''` | 193 | `.filter(col("ANOMALY_TYPE") != "")` | 292 |
| `proc append base=CURATED.DAILY_TRANSACTIONS data=WORK.TXN_WITH_BALANCE force;` | 207-209 | `proc_append_force()` — creates base from the data if absent, otherwise projects to the base table's columns (extra dropped, missing filled NULL) and `write.mode("append")` | 94-110, 304 |
| `%if &nobs_anom > 0 %then proc append base=CURATED.TXN_ANOMALIES … force` | 213-217 | `if nobsAnom > 0: proc_append_force(...)` | 306-307 |
| `data CURATED.RUNNING_BALANCES; set TXN_WITH_BALANCE; keep ACCOUNT_ID TRANSACTION_DATE TRANSACTION_ID RUNNING_BALANCE;` | 222-225 | `select(RUNNING_BALANCE_KEEP).write.mode("overwrite").save_as_table(...)` with columns in PDV order `TRANSACTION_ID, ACCOUNT_ID, TRANSACTION_DATE, RUNNING_BALANCE` | 73, 313-315 |
| `%lock(CURATED.DAILY_TRANSACTIONS)` / `%lock(..., action=unlock)` | 204-227 | none — Snowflake DML is transactional | (comment at 302) |
| `%put NOTE: … Feed: &nobs_feed | Valid: … | Rejected: … | Anomalies: …` | 230-237 | return string | 317-321 |
| `proc datasets lib=WORK … delete TXN_VALIDATED TXN_REJECTED …` | 240-244 | nothing — DataFrames / temp tables die with the session | — |
| `format … dollar18.2`, `$TXNCAT.` labels | 124, 153, loader | display-only; no data effect, not reproduced | — |

## 3. Legacy quirks reproduced (not fixed)

| # | Quirk | Where | Effect in golden run |
|---|---|---|---|
| Q1 | `PROC APPEND … FORCE` onto the existing 10-column `CURATED.DAILY_TRANSACTIONS` silently **drops** every enrichment column (`ACCOUNT_TYPE`, `CUSTOMER_ID`, `CUSTOMER_SEGMENT`, `REGION_CODE`, `BRANCH_ID`, `PRE_TXN_BALANCE`, `POST_TXN_BALANCE`, `RISK_RATING`, `RUNNING_BALANCE`). On a *first* run against an empty library the table would be created with all 19 columns instead. | SAS 207-209 → target 94-110 | history grows 18293 → 18903 rows, still 10 columns |
| Q2 | SAS treats missing as lower than any number, so an account missing from `CUST_ACCOUNTS_DAILY` gets a missing `RUNNING_BALANCE`, which satisfies `RUNNING_BALANCE < 0` → classified **OVERDRAFT**, never `ORPHAN_ACCOUNT` (that branch is unreachable for orphans; it can only fire for an account row that exists with a blank `CUSTOMER_ID`). Reproduced with `RUNNING_BALANCE IS NULL OR < 0`. | SAS 183-186 → target 275 | no orphan rows in this feed (`ORPHAN_ACCOUNT=0`) |
| Q3 | Z-score baseline (`TXN_STATS`) is the history **before** today's rows are appended; the same account's today-rows are not in their own baseline. Lazy Snowpark evaluation would have re-read the table after the append — `cache_result()` pins the SAS order. | SAS 159-170 → target 243, 293 | 16 `HIGH_AMOUNT` (an un-pinned run gives 14) |
| Q4 | `POST_DATE` is never validated; only `TRANSACTION_DATE > &txn_date` is rejected. | SAS 84 → target 169 | 2 future-dated rows rejected, post dates untouched |
| Q5 | Sign convention: `DEP/INT/REF/REV/TRF/ADJ` add the **signed** amount, `WDR/PMT/FEE/CHG` subtract `abs(amount)`. A negative `DEP` lowers the balance; a negative `WDR` still lowers it. 28 feed rows / 1101 history rows have negative amounts. | SAS 116-123, 144-149 → target 200-205, 232-237 | reproduced; `Filtered Sum (TRANSACTION_AMOUNT < 0)` matches |
| Q6 | `LARGE_WITHDRAWAL` compares `abs(amount)` with the **pre-day** balance × 0.9 (not the running balance) and is evaluated *after* `OVERDRAFT`, so a withdrawal that empties the account is reported as OVERDRAFT, never LARGE_WITHDRAWAL. 14 rows meet the LARGE_WITHDRAWAL predicate but are all shadowed. | SAS 183-188 → target 275-283 | `LARGE_WITHDRAWAL=0` |
| Q7 | Rejected rows (`WORK.TXN_REJECTED`, 12 rows: 2 each of missing id / missing account / missing amount / amount > 10,000,000 / invalid type `XFR`,`MISC` / future-dated) are counted in the log and then **deleted** — no persisted reject table. Reproduced: counted, not written. | SAS 45-97, 240-244 → target 178-182 | `Rejected: 12` in the return string only |
| Q8 | `PRE_TXN_BALANCE` is the single snapshot balance from `CUST_ACCOUNTS_DAILY`; `POST_TXN_BALANCE` therefore ignores the account's earlier same-day transactions (only `RUNNING_BALANCE` accumulates). Both are stored in `TXN_ANOMALIES`. | SAS 115-124 | reproduced; `Sum Amount [POST_TXN_BALANCE]` matches |
| Q9 | Within an account the BY order is `TRANSACTION_DATE, TRANSACTION_ID` — a character sort on the ID, not posting time. All feed rows share one date, so the ID order decides the running balance path. | SAS 129, 139 → target 231 | reproduced |
| Q10 | `std()` is the sample standard deviation, so an account with exactly one history transaction has a missing `STD_TXN_AMT` → `Z_SCORE` missing → cannot be `HIGH_AMOUNT`. `STDDEV` (sample) behaves identically. | SAS 165, 176-180 → target 257, 265 | reproduced |
| Q11 | The `having ANOMALY_TYPE ne ''` filter means a feed with zero anomalies leaves `CURATED.TXN_ANOMALIES` untouched (and non-existent on a fresh library). | SAS 193, 213 → target 292, 306 | 46 rows appended |
| Q12 | Character lengths are fixed by the loader (`TRANSACTION_ID $12`, `TRANSACTION_TYPE $4`, `DESCRIPTION $60`, …); longer CSV values are truncated by SAS `truncover`. The runner truncates identically before loading (`_sas_char`). | `Data/load_seed_data.sas` 110-121 → `run_local.py` 107-112 | no values exceed the lengths in this feed |
| Q13 | Reject reasons built at SAS 53-93 are never observable: `REJECT_REASON` is dropped from every output dataset at line 96 and `WORK.TXN_REJECTED` is deleted at 240-242; only the `%nobs` count survives. The conversion keeps the column on `txnRejected` for the count and persists nothing. | SAS 53-96, 240-242 → target 178-183 | 61 rejected rows counted, none persisted |
| Q14 | `%lock(CURATED.DAILY_TRANSACTIONS)` / `%lock(..., unlock)` at SAS 205/211 are positional calls to the keyword-only `Macro/lock.sas` (MEMBER=, ACTION=; `unlock` is not a valid ACTION), so they are dead code in production SAS as well as under the Docker shim. Snowflake DML is transactional; no equivalent emitted. | SAS 205, 211; `Macro/lock.sas` 86-124 | no data impact |

## 4. How the Snowflake-side CSVs were produced

**Credentials exist but the Snowflake account's compute is suspended.** `SNOWFLAKE_DEMO_*` key-pair authentication succeeds (user `DEVIN_SVC`, role `DEMO_ENGINEER`, warehouse `DEMO_WH`), but every statement that needs compute — `CREATE SCHEMA`, any non-metadata `SELECT` — fails with `000666 (57014): Your account is suspended due to lack of payment method.` Nothing was written to Snowflake.

The reconciliation outputs were therefore produced with the **Snowpark local testing emulator** (`snowflake-snowpark-python[localtest]` 1.56.0, `Session.builder.config("local_testing", True)`) by `sample_data/Scenario_daily_transaction_processing/run_local.py --engine local`. The conversion module is executed unchanged; the runner:

1. loads the inputs typed exactly as SAS does (`run_local.py` `TXN_SCHEMA`, `ACCT_SCHEMA`): `VARCHAR(n)` per the SAS `length` statement with `truncover` truncation and blank→NULL, `DOUBLE` for SAS numerics, `DATE` from `date9.` strings (seeds) or SAS day integers (golden `stg_bank__cust_accounts_daily.csv`), and columns in the SAS PDV order (`TXN_PDV_ORDER`, char variables first — the order the golden exports show);
   * `RAW_BANK.TXN_FEED_20240131` ← SOURCE `Data/csv/raw_bank/TXN_FEED_20240131.csv` (622 rows)
   * `CURATED.DAILY_TRANSACTIONS` ← SOURCE `Data/csv/curated/DAILY_TRANSACTIONS.csv` (18293 rows)
   * `STG_BANK.CUST_ACCOUNTS_DAILY` ← golden `stg_bank__cust_accounts_daily.csv` (466 rows, JOB01's output)
2. calls `daily_transaction_processing(session, "2024-01-31", rawBank=…, stgBank=…, curated=…)`;
3. dumps the three output tables as `CURATED__<TABLE>.csv` the way `export_golden.sas` / PROC EXPORT does — upper-case headers, DATE columns as SAS day integers (23406 = 2024-01-31) so `Filtered Count (TRANSACTION_DATE == 23406)` compares like with like.

Emulator shims (`install_local_shims()`, active **only** with `--engine local`; real Snowflake needs none):

| Patched function | Why |
|---|---|
| `trim` | not implemented by the emulator |
| `abs` | emulator raises on NULL; Snowflake/SAS return NULL/missing |
| `to_char(number, format)` | emulator rejects numeric format strings; only `'$999,…,999.00'` (SAS `dollar18.2`, used in a REJECT_REASON that is never persisted) is emulated, other calls are delegated |

Two emulator limitations also shaped the (Snowflake-valid) code: the mock mangles duplicate column names in `df.alias()` joins, so the join keys of the right-hand tables are renamed (`A_ACCOUNT_ID`, `S_ACCOUNT_ID`) instead — semantically identical to `t.ACCOUNT_ID = a.ACCOUNT_ID` with `t.*`.

`--engine snowflake` is implemented (private namespace `INSURANCE_DEMO.DEV_JOB02_{RAW_BANK,STG_BANK,CURATED}`) and is the intended path once the account is unsuspended; it has not been exercised end-to-end.

Reproduce:

```bash
pip install "snowflake-snowpark-python[localtest]" pandas
tar xzf sas_golden_31JAN2024.tar.gz -C ~/golden
python3 sample_data/Scenario_daily_transaction_processing/run_local.py --engine local \
    --source-repo ~/repos/ts-sas-legacy-analytics --golden ~/golden
python3 verify/reconcile.py --scenario Scenario_daily_transaction_processing --sas-golden ~/golden \
    --report sample_data/Scenario_daily_transaction_processing/reconciliation.md
```

## 5. Reconciliation

55 checks, all PASS (`sample_data/Scenario_daily_transaction_processing/reconciliation.md`, rules in `validations.csv`): row counts 18903 / 46 / 610; control totals `DAILY_TRANSACTIONS.SUM_AMOUNT=8231436.81`, `RUNNING_BALANCES.SUM_RUNNING_BALANCE=49551238.14`; `HIGH_AMOUNT=16`, `OVERDRAFT=30`, `LARGE_WITHDRAWAL=0`, `ORPHAN_ACCOUNT=0`; key distinct/not-null/uniqueness; sums of every numeric anomaly column (`Z_SCORE`, `AVG_TXN_AMT`, `STD_TXN_AMT`, `PRE/POST_TXN_BALANCE`, `RUNNING_BALANCE`); today-vs-history splits; negative-amount and shadowed-LARGE_WITHDRAWAL counts. A row-level diff (outside the harness) of all 46 anomalies and 610 running balances against the golden files shows max abs difference 4.6e-8 (PROC EXPORT prints 10 significant digits).

## 6. Open questions

1. **Snowflake execution** — the demo account is suspended for billing; `--engine snowflake` is untested. Re-run it (and register `handler` as the stored procedure behind `TASK_JOB02_DAILY_TRANSACTIONS`) once compute is restored.
2. **Orchestration** — `snowflake_sql/orchestration/tasks.sql` still points `TASK_JOB02_DAILY_TRANSACTIONS` at a non-existent `JOB02_DAILY_TRANSACTIONS.sql`; it must call the Snowpark procedure instead (owned by the integration child, not changed here).
3. **Q1 on first run** — if `CURATED.DAILY_TRANSACTIONS` is ever (re)created empty, SAS and the conversion will both create a 19-column table and the enrichment columns will start being persisted. Is the 10-column shape a requirement or an accident?
4. **Q2** — should orphan accounts be `ORPHAN_ACCOUNT` rather than `OVERDRAFT` (and should running balance be missing for them)? Behaviour preserved; a fix would need a business decision.
5. **Q6** — is `LARGE_WITHDRAWAL` meant to be unreachable whenever the withdrawal overdraws the account (14 shadowed rows in this feed)?
6. **Rejects** — 12 rejected feed rows per day are discarded with no persisted audit trail (Q7); the Snowflake side could persist `WORK.TXN_REJECTED` cheaply if wanted (not done, SAS does not).
7. **Numeric type** — SAS numerics are IEEE doubles, so the runner loads amounts as `DOUBLE`. If the production Snowflake tables use `NUMBER(18,2)`, sums will differ from SAS at the 1e-10 level only, but `Z_SCORE` and `STD_TXN_AMT` will be computed with different precision.
