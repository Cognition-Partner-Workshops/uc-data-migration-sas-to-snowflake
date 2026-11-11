# Integration — Snowflake Task DAG and lineage for JOB02 / JOB05 / JOB06 / JOB07

Wires the per-program conversions into `snowflake_sql/orchestration/tasks.sql` and
`lineage/SF_lineage.json`. The SAS batch order (`BatchJobs/run_daily_banking.sas`,
`BatchJobs/run_daily_insurance.sas`) and the Control-M schedules in the program headers are
the source of truth; where Snowflake cannot express one of them exactly, the gap is listed
below rather than papered over.

## Task DAG

| Task | Replaces | Runs | Schedule / dependency | Business-date variables |
|---|---|---|---|---|
| `TASK_DAILY_BANKING_ROOT` | Control-M `BANK_MASTER` | — | `CRON 0 6 * * *` NY | — |
| `TASK_JOB01_LOAD_CUST_ACCOUNTS` | `BANK_DAILY_01` / batch step 1 | `@SQL_STAGE/JOB01_LOAD_CUST_ACCOUNTS.sql` | `AFTER ROOT` | (unchanged) |
| `TASK_JOB02_DAILY_TRANSACTIONS` **(re-pointed)** | `BANK_DAILY_02` / batch step 2 | `CALL SP_DAILY_TRANSACTION_PROCESSING(CURRENT_DATE())` — Snowpark `snowpark/JOB02_daily_transaction_processing.py` `handler` | `AFTER JOB01` | `txn_date = &CURR_DT` |
| `TASK_JOB03_CALC_AMB` | batch inline | `@SQL_STAGE/JOB03_CALC_AMB.sql` | `AFTER JOB02` | (unchanged) |
| `TASK_JOB04_MONTHLY_REGULATORY` | `BANK_MONTHLY_01` / batch step 4 | `@SQL_STAGE/JOB04_MONTHLY_REGULATORY.sql` | `AFTER JOB03`, gate `stream has data OR day-of-month <= 5` | (unchanged — see gaps) |
| `TASK_JOB07_CUSTOMER_PROFITABILITY` **(new)** | `BANK_MONTHLY_03` (10th business day; no batch orchestrator) | `@SQL_STAGE/JOB07_CUSTOMER_PROFITABILITY.sql` | `AFTER JOB02`, gate `day-of-month = 14` | `report_month = &PREV_YM` = previous calendar month |
| `TASK_INS_CLAIMS_PROCESSING` | `INS_DAILY_01` / insurance batch step 1 | `CALL SP_CLAIMS_PROCESSING(CURRENT_DATE())` | `CRON 0 8 * * *` NY | (unchanged) |
| `TASK_JOB06_POLICY_VALUATION` **(new)** | `INS_MONTHLY_01` header / insurance batch step 2 | `@SQL_STAGE/JOB06_POLICY_VALUATION.sql` | `AFTER TASK_INS_CLAIMS_PROCESSING` | `val_date = &CURR_DT`, `lob = ALL` |
| `TASK_WEEKLY_CREDIT_RISK` **(re-pointed)** | `BANK_WEEKLY_01` (also batch step 3) | `@SQL_STAGE/JOB05_CREDIT_RISK_SCORING.sql` | `CRON 0 2 * * SUN` NY (unchanged) | `score_date = &CURR_DT`, `model_id = CRM-2023-Q4-v2` |

`ALTER TASK ... RESUME` order: JOB07, JOB04, JOB03, JOB02, JOB01, ROOT; JOB06, CLAIMS; WEEKLY
(children before their root).

### How a `$variable`-driven script gets its variables

Session variables do not cross task boundaries (each task runs in its own session), so the
root task's `SELECT CURRENT_DATE() AS run_date` reaches nobody. Every task that runs one of
the new scripts therefore sets its own variables in a Snowflake Scripting block:

```sql
BEGIN
    EXECUTE IMMEDIATE 'SET score_date = CURRENT_DATE()';
    EXECUTE IMMEDIATE 'SET model_id = ''CRM-2023-Q4-v2''';
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB05_CREDIT_RISK_SCORING.sql;
END;
```

The scripts' own `SET` statements (e.g. JOB06's `ytd_start` / `renewal_cutoff` / `claims_from`)
run inside the same session, so they see the task's variables. The date defaults mirror the
production `Config/autoexec.sas` (`CURR_DT = today()`, `PREV_YM = intnx(month, today(), -1)`);
`Config/autoexec_local.sas` pins `CURR_DT=31JAN2024` and `PREV_YM=202401` (= `CURR_YM`), which
is a local-config quirk, not a program rule.

### Deployment artefacts

Upload to `@FINANCE_DB.PUBLIC.SQL_STAGE` before running `tasks.sql`:
`snowflake_sql/JOB01_LOAD_CUST_ACCOUNTS.sql`, `JOB03_CALC_AMB.sql`, `JOB04_MONTHLY_REGULATORY.sql`,
`JOB05_CREDIT_RISK_SCORING.sql`, `JOB06_POLICY_VALUATION.sql`, `JOB07_CUSTOMER_PROFITABILITY.sql`
and `snowpark/JOB02_daily_transaction_processing.py` (imported by
`SP_DAILY_TRANSACTION_PROCESSING`, which `tasks.sql` now creates). `SP_CLAIMS_PROCESSING`
(claims_processing.py) is still not registered anywhere in the repo — pre-existing gap.

## Dependencies that could not be mirrored exactly

1. **JOB07 ↔ JOB05.** `customer_profitability.sas` reads `CURATED.RISK_SCORES` (JOB05) as well
   as JOB01/JOB02 outputs. `AFTER` only works inside one DAG and `TASK_WEEKLY_CREDIT_RISK` is
   its own root, so only the JOB01 → JOB02 dependency is expressed. The script takes the
   latest `SCORE_DATE <= month_end` exactly as SAS does, so a Sunday run before the 14th is
   what it consumes.
2. **Business-day schedules.** Control-M business-day calendars have no Snowflake equivalent.
   JOB07's "10th business day" is gated on `DAYOFMONTH(CURRENT_DATE()) = 14` (the 10th business
   day falls on the 14th in 5 of the 7 possible month-start weekdays, on the 12th/13th
   otherwise, later in months with holidays). JOB04 keeps its pre-existing `<= 5`
   approximation of "3rd business day". Both gates run inside the task body, because a Task
   `WHEN` clause accepts only `SYSTEM$STREAM_HAS_DATA` / `SYSTEM$GET_PREDECESSOR_RETURN_VALUE` /
   `SYSTEM$PREDECESSORS_STATE` / `SYSTEM$TASK_COMPLETION_STATE` (Snowflake error 091464 for
   `DAYOFMONTH`). JOB07 therefore runs after JOB02 on the 14th rather than at a Control-M time
   of day of its own.
3. **JOB05 daily vs weekly.** `run_daily_banking.sas` runs `credit_risk_scoring.sas` daily as
   step 3 (between JOB02 and JOB04); the program header says weekly `BANK_WEEKLY_01`. The
   weekly CRON task is kept as instructed. Because the script appends blindly (PROC APPEND
   semantics, PR #22 quirk), adding it to the daily chain as well would duplicate
   `CURATED.RISK_SCORES` / `RISK_MIGRATION` rows — decide one schedule before enabling.
4. **JOB06 monthly vs daily.** `policy_valuation.sas` header: monthly 5th business day
   (`INS_MONTHLY_01`); `run_daily_insurance.sas:112`: daily step 2 after claims. The batch
   order is mirrored (daily, `AFTER` claims). `STG_INS.POLICY_VALUATION` and
   `REPORTS.LOSS_RATIO_SUMMARY` are `CREATE OR REPLACE`, so a daily run is idempotent.
5. **JOB07 fails on estate data by design.** `CURATED.DAILY_TRANSACTIONS` has no `CUSTOMER_ID`
   (JOB02's PROC APPEND FORCE drops it), so SAS and the conversion both fail at step 2
   (PR #23). The task is wired so the DAG is complete, but it will fail until the business
   decides the fix.

## Pre-existing defects in `tasks.sql` fixed here (the file did not deploy before)

Found by running the file against a Snowflake sandbox (`SAS_MIG_INTEGRATION`, `FINANCE_DB` /
`WH_BATCH_ETL` substituted); nothing else about the existing tasks was changed.

- `COMMENT = ...` after `AFTER ...` is a syntax error (`unexpected 'COMMENT'`): in the
  `CREATE TASK` grammar the task parameters precede the `AFTER` clause. Reordered on every
  child task.
- JOB04's `WHEN ... OR DAYOFMONTH(CURRENT_DATE()) <= 5` is rejected (091464). The same OR
  gate is now evaluated in the body (`IF (...) THEN EXECUTE IMMEDIATE FROM ...`).
- `TASK_WEEKLY_CREDIT_RISK` loaded `@SQL_STAGE/credit_risk_scoring.sql`, which does not exist;
  it now loads `JOB05_CREDIT_RISK_SCORING.sql` and sets `$score_date` / `$model_id`.
- `TASK_JOB02_DAILY_TRANSACTIONS` loaded `@SQL_STAGE/JOB02_DAILY_TRANSACTIONS.sql`, which does
  not exist; it now calls the Snowpark procedure.

Not changed (flagged): `TASK_JOB04_MONTHLY_REGULATORY` does not set `$report_month`, which
`JOB04_MONTHLY_REGULATORY.sql` reads; `FINANCE_DB.PUBLIC.MONTHLY_TRIGGER_STREAM` must exist
before the JOB04 body runs.

## Schema map (open)

The conversions keep SAS librefs as schema names; JOB01/JOB03/JOB04 and JOB07 use the earlier
`STAGING` / `RAW` names. In `FINANCE_DB` both sets would have to exist until the map is
settled; this PR does not rename anything inside the conversions.

| SAS libref | JOB01/03/04 (main) | JOB02 | JOB05 | JOB06 | JOB07 |
|---|---|---|---|---|---|
| `RAW_BANK` / `ORA_DW` | `RAW` | `RAW_BANK` | `ORA_DW` | — | — |
| `STG_BANK` | `STAGING` | `STG_BANK` | `STG_BANK` | — | `STAGING` |
| `RAW_INS` / `STG_INS` | — | — | — | `RAW_INS` / `STG_INS` | — |
| `CURATED` / `REPORTS` | `REPORTS` | `CURATED` | `CURATED` / `REPORTS` | `REPORTS` | `CURATED` / `REPORTS` |

JOB07 reads `STAGING.CUST_ACCOUNTS_DAILY` while JOB05 reads `STG_BANK.CUST_ACCOUNTS_DAILY` —
the same JOB01 output under two names. Resolve with one `ALTER TABLE ... RENAME` / view per
schema, or align the conversions, before the first production run.

## Lineage (`lineage/SF_lineage.json`)

29 records appended (existing 5 untouched), one per source-table → target-table edge, in the
existing record shape (`source` / `target` nodes `snowflake` → `FINANCE_DB` → `<schema>`,
`transformations.path`, `highlights[].text` = the headline quirk). Table nodes are named
`SCHEMA.TABLE` exactly as the golden-harness mode addresses them (`CURATED.TXN_ANOMALIES`), so
the graph joins across jobs: `REPORTS.CUSTOMER_PNL` traces upstream to
`CURATED.DAILY_TRANSACTIONS`, `CURATED.RISK_SCORES`, `RAW_BANK.TXN_FEED_YYYYMMDD`,
`STG_BANK.CUST_ACCOUNTS_DAILY`, `ORA_DW.*`. Intra-job derivations are explicit edges
(`STG_INS.POLICY_VALUATION → REPORTS.LOSS_RATIO_SUMMARY`,
`REPORTS.CUSTOMER_PNL → SEGMENT_PROFITABILITY / BRANCH_PROFITABILITY`;
`CURATED.DAILY_TRANSACTIONS` is both JOB02 input (history) and output). Because a job is one
graph node, `collect_upstream_tables` on such a table also lists the job's other inputs/outputs
(e.g. `REPORTS.CUSTOMER_PNL` appears in its own upstream list); the DFS is cycle-safe.

The existing JOB01 record targets `STG.CUST_ACCOUNTS` (the demo mini-pipeline naming), so the
JOB01 → JOB02/JOB05/JOB07 hop is not joined in the graph until the schema map above is settled;
the record was left as is. `lineage/SF_lineage_graph.pkl` was regenerated from the JSON with
`lineage/lineage_pickel_frm_json.build_DAG` (35 nodes, 35 edges, networkx 3.4.2);
`collect_upstream_tables("MONTHLY_AMB")` still returns the original result.

## Validation evidence

- Snowflake sandbox `SAS_MIG_INTEGRATION` (account `TOJGONB-SF03144`, `ACCOUNTADMIN`): all 20
  statements of `tasks.sql` (`FINANCE_DB` → sandbox, `WH_BATCH_ETL` → `COMPUTE_WH`, CREATE
  WAREHOUSE skipped) succeed — procedure, 9 tasks, 9 RESUMEs. `TASK_DEPENDENTS` shows
  `ROOT → JOB01 → JOB02 → {JOB03 → JOB04, JOB07}` and `CLAIMS → JOB06`. The sandbox DAG was
  suspended again afterwards.
- End-to-end run of the `TASK_JOB06_POLICY_VALUATION` body as a task (`EXECUTE TASK` on a
  standalone copy with `val_date` pinned to `2024-01-31`, `RAW_INS` cloned from PR #25's
  sandbox): `STG_INS.POLICY_VALUATION` (8 rows) and `REPORTS.LOSS_RATIO_SUMMARY` (5 rows)
  match PR #25's committed CSVs cell for cell.
- Session-variable hand-off proven with a probe script (`SET` in the task block → `$var`
  visible to `EXECUTE IMMEDIATE FROM`, and the script's own `SET` works).
- `python3 -c json.load` on `SF_lineage.json`; `ruff` not applicable (no Python changed).
