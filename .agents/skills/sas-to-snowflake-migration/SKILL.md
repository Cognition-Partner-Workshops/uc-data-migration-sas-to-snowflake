---
name: sas-to-snowflake-migration
description: Repo mechanics for converting a SAS program to a verified Snowflake model in this repo — conversion output layout, validation commands, scenario namespaces, orchestration. Supplements the general !convert-sas-to-snowflake playbook.
---

## When to use this

Use this skill whenever you are converting or validating a SAS-to-Snowflake
migration **in this repository**. It is the repo-specific companion to the
general procedure in the `!convert-sas-to-snowflake` playbook
(`.workshop/playbooks/sas-to-snowflake-migration.devin.md`): the playbook says
*what* to do and *why* (source-parity principle, procedure, forbidden actions);
this skill says *how* to do it here (exact commands, paths, output layout).

## Layout

### SAS source (read-only reference)

The legacy SAS estate lives in the companion `ts-sas-legacy-analytics` repo:

- `Programs/Banking/` — `load_customer_accounts.sas`,
  `daily_transaction_processing.sas`, `credit_risk_scoring.sas`,
  `monthly_regulatory_reporting.sas`
- `Programs/Insurance/` — `claims_processing.sas`, `policy_valuation.sas`
- `Macro/` — shared SAS macros (`parmv.sas`, `nobs.sas`, `lock.sas`)
- `Formats/` — SAS PROC FORMAT catalogs
- `BatchJobs/` — Control-M orchestrators (`run_daily_banking.sas`,
  `run_daily_insurance.sas`)

### Snowflake SQL conversions

`snowflake_sql/` — SQL conversions for set-based SAS programs:

| File | Source SAS program | Key constructs |
|---|---|---|
| `JOB01_LOAD_CUST_ACCOUNTS.sql` | `load_customer_accounts.sas` | CTAS with CASE-derived columns, DQ exceptions |
| `JOB03_CALC_AMB.sql` | inline batch step | AMB aggregation — **no `is_active` filter** |
| `JOB04_MONTHLY_REGULATORY.sql` | `monthly_regulatory_reporting.sas` | Risk weights (LOC=1.00), delinquency aging, LLP coverage, capital adequacy |

### Snowpark Python conversions

`snowpark/` — Snowpark stored procedures for procedural SAS programs:

| File | Source SAS program | Why Snowpark (not SQL) |
|---|---|---|
| `claims_processing.py` | `claims_processing.sas` | Hash-table lookups, multi-output datasets, conditional routing |

### Task orchestration

`snowflake_sql/orchestration/tasks.sql` — Snowflake Task DAG replacing
Control-M:

| Snowflake Task | Replaces | Schedule/Dependency |
|---|---|---|
| `TASK_DAILY_BANKING_ROOT` | Control-M `BANK_MASTER` | `CRON 0 6 * * *` |
| `TASK_JOB01_LOAD_CUST_ACCOUNTS` | `BANK_DAILY_01` | `AFTER ROOT` |
| `TASK_JOB02_DAILY_TRANSACTIONS` | `BANK_DAILY_02` | `AFTER JOB01` |
| `TASK_JOB03_CALC_AMB` | batch inline | `AFTER JOB02` |
| `TASK_JOB04_MONTHLY_REGULATORY` | `BANK_MONTHLY_01` | `AFTER JOB03` (conditional) |
| `TASK_INS_CLAIMS_PROCESSING` | `INS_DAILY_01` | `CRON 0 8 * * *` (Snowpark SP) |
| `TASK_WEEKLY_CREDIT_RISK` | `BANK_WEEKLY_01` | `CRON 0 2 * * SUN` |

### Validation harness

- SAS source datasets (`.sas7bdat`): `sample_data/MONTHLY_AMB.sas7bdat`,
  `sample_data/CUST_ACCOUNTS.sas7bdat`, `sample_data/DAILY_BALANCE.sas7bdat`.
- Snowflake target datasets (CSV exports, per scenario):
  `sample_data/Scenario1/` (baseline), `sample_data/Scenario2/` (delta).
- Validation rules: `config/validation_rule_config.json` (LLM column
  recommendations), `config/validations_list.csv` (rule checklist).
- Lineage metadata: `lineage/SAS_lineage.json`, `lineage/SF_lineage.json`
  (Collibra-style JSON); `lineage/SAS_lineage_graph.pkl`,
  `lineage/SF_lineage_graph.pkl` (NetworkX graphs).
- CLI harness: `verify/reconcile.py` (programmatic gate, exits non-zero on FAIL).
- Streamlit dashboard: `app4.py` (interactive visual exploration).

## Scenarios (isolated, concurrent-safe)

Each run targets a scenario directory under `sample_data/`. Scenarios are the
namespace mechanism: `Scenario1` is the baseline, `Scenario2` is the delta.
For concurrent runs, create additional directories (`Scenario3`,
`Scenario_<session_id>`). The SAS `.sas7bdat` files in `sample_data/` are the
immutable baseline — never modified.

## Tables available

| Table | SAS source | Snowflake columns |
|---|---|---|
| `CUST_ACCOUNTS` | `customer_id`, `account_id`, `account_type`, `is_active`, `start_date`, `end_date` | same |
| `DAILY_BALANCE` | `customer_id`, `account_id`, `date`, `end_of_day_balance`, `month` | same |
| `MONTHLY_AMB` | `customer_id`, `account_id`, `reporting_month_yyyymm`, `average_monthly_balance`, `date_computed` | same |
| `MONTHLY_RWA` | `REPORT_MONTH`, `ACCOUNT_TYPE`, `CUSTOMER_SEGMENT`, `RISK_WEIGHT`, `N_ACCOUNTS`, `TOTAL_EXPOSURE`, `RWA` | same |
| `DELINQUENCY_AGING` | `REPORT_MONTH`, `ACCOUNT_TYPE`, `REGION_CODE`, `DELINQ_BUCKET`, `N_ACCOUNTS`, `TOTAL_BALANCE`, `TOTAL_PAST_DUE` | same |
| `LLP_COVERAGE` | `REPORT_MONTH`, `ACCOUNT_TYPE`, `N_LOANS`, `GROSS_LOANS`, `TOTAL_ALLOWANCE`, `COVERAGE_PCT`, `NPL_BALANCE`, `NPL_COVERAGE_PCT` | same |
| `CAPITAL_ADEQUACY` | `REPORT_MONTH`, `TOTAL_RWA`, `CET1_CAPITAL`, `TIER1_CAPITAL`, `TOTAL_CAPITAL`, `CET1_RATIO`, `TIER1_RATIO`, `TOTAL_CAPITAL_RATIO`, `CET1_STATUS`, `TIER1_STATUS`, `TOTAL_CAPITAL_STATUS` | same |

## Commands

### Validate (CLI)

```bash
# Single table against a scenario:
python verify/reconcile.py --table MONTHLY_AMB --scenario Scenario1

# All tables in a scenario:
python verify/reconcile.py --scenario Scenario1

# Quick smoke test (row counts only):
python verify/reconcile.py --scenario Scenario1 --quick
```

### Make targets

```bash
make validate SCENARIO=Scenario1         # full validation suite
make validate TABLE=MONTHLY_AMB SCENARIO=Scenario1  # single table
make validate-all                        # all scenarios (exits non-zero on any FAIL)
make dashboard                           # launch Streamlit validation UI
make lint                                # ruff check on Python files
```

## Adding a new conversion

1. Read the SAS source in `ts-sas-legacy-analytics`.
2. Choose the target: `snowflake_sql/` for set-based, `snowpark/` for procedural.
3. Write the converted code, reproducing SAS logic faithfully.
4. Add or update a Snowflake Task in `snowflake_sql/orchestration/tasks.sql`.
5. Run `make validate` to confirm parity.
6. Include the green reconciliation report in the PR.

## Close the loop

If a validation fails, investigate against the SAS source — **do not** relax,
delete, or hard-code the validation to make it pass. Fix the Snowflake code
and re-run until the report is green. Include the green report in the PR.

## Lineage tracing

When a validation fails, use the lineage metadata to trace upstream:

```python
from lineage.lineage_functions import load_lineage_graph, collect_upstream_tables

sas_graph = load_lineage_graph("lineage/SAS_lineage_graph.pkl")
sf_graph = load_lineage_graph("lineage/SF_lineage_graph.pkl")
upstream = collect_upstream_tables(sf_graph, "MONTHLY_AMB")
```

Compare the SAS and Snowflake lineage to identify where a transformation
diverged (e.g., an extra `WHERE` clause added during migration).
