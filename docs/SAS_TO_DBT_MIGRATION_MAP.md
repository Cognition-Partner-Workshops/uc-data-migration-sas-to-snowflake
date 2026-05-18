# SAS to dbt on Databricks — Migration Mapping

This document maps every SAS construct in the `ts-sas-legacy-analytics` codebase to its dbt/Databricks equivalent. It is the reference artifact for the migration assessment phase.

## Architecture Overview

```
SAS Environment                    dbt + Databricks
─────────────────────             ──────────────────────────
autoexec.sas (LIBNAMEs)    →     Unity Catalog + dbt sources
PROC FORMAT catalogs        →     dbt macros (CASE expressions)
                                  or dbt seed CSV files
SAS Macros (%macro)         →     dbt Jinja macros
DATA steps                  →     dbt SQL models (SELECT)
PROC SQL                    →     dbt SQL models (SELECT)
PROC MEANS / PROC FREQ      →     SQL GROUP BY aggregations
PROC APPEND                 →     dbt incremental models (MERGE)
PROC EXPORT (Excel)         →     Databricks notebooks / Python
%INCLUDE chains             →     dbt ref() DAG
Control-M scheduling        →     Databricks Workflows
sendmail notifications      →     Databricks Alerts / PagerDuty
Hash objects (lookup)       →     Spark broadcast joins
RETAIN + BY-group           →     Window functions (SUM OVER)
Macro variables (&var)      →     dbt vars / env_var()
```

## Program-Level Migration Map

| SAS Program | dbt Model(s) | Key Patterns |
|---|---|---|
| `load_customer_accounts.sas` | `stg_cust_accounts.sql` → `int_account_metrics.sql` | PROC SQL join → dbt staging; DATA step derivations → SQL CASE |
| `daily_transaction_processing.sas` | `stg_daily_transactions.sql` → `mart_daily_transactions.sql` | DATA step validation → SQL WHERE; RETAIN running balance → window function |
| `credit_risk_scoring.sas` | `mart_risk_scores.sql` | WOE scorecard DATA step → nested SQL CASE; exp() PD calc → SQL exp() |
| `monthly_regulatory_reporting.sas` | `mart_regulatory_rwa.sql` + `mart_delinquency_aging.sql` | PROC SQL aggregation → SQL GROUP BY; PROC EXPORT → Databricks notebook |
| `claims_processing.sas` | `stg_claims.sql` → `int_claims_adjudication.sql` | Hash lookup → broadcast join; IF/THEN routing → SQL CASE |
| `policy_valuation.sas` | `int_policy_valuation.sql` → `mart_loss_ratios.sql` | MERGE BY → SQL JOIN; earned premium calc → SQL date math |
| `customer_profitability.sas` | `mart_customer_pnl.sql` | Multi-source merge → multi-ref JOIN; tier assignment → CASE |

## SAS Construct Migration Details

### 1. LIBNAME → Unity Catalog External Tables

**SAS:**
```sas
libname ORA_DW oracle path="FINPROD" schema="DW_BANKING" user=&ora_uid pw=&ora_pwd;
libname RAW_BANK "/data/sas/raw/banking" access=readonly;
```

**Databricks:**
```sql
CREATE CATALOG banking_analytics;
CREATE SCHEMA banking_analytics.raw;
CREATE TABLE banking_analytics.raw.cust_accounts
  USING DELTA
  LOCATION 's3://data-lake/raw/banking/cust_accounts';
```

**dbt:**
```yaml
sources:
  - name: banking_raw
    database: banking_analytics
    schema: raw
    tables:
      - name: cust_accounts
```

### 2. PROC FORMAT → dbt Macros

**SAS:**
```sas
proc format library=BANKING;
  value $ACCTTYPE
    'CHK' = 'Checking'
    'SAV' = 'Savings'
    ...
  ;
run;
```

**dbt Macro (`macros/format_account_type.sql`):**
```sql
{% macro format_account_type(column) %}
case {{ column }}
    when 'CHK' then 'Checking'
    when 'SAV' then 'Savings'
    ...
end
{% endmacro %}
```

**Usage in model:**
```sql
select
    account_type,
    {{ format_account_type('account_type') }} as account_type_desc
from {{ ref('stg_cust_accounts') }}
```

### 3. DATA Step Business Logic → SQL CASE

**SAS:**
```sas
data OUTPUT;
  set INPUT;
  if ACCOUNT_TYPE in ('CC','LOC','HELC') and CREDIT_LIMIT > 0 then
    UTILIZATION_PCT = (CURRENT_BALANCE / CREDIT_LIMIT) * 100;
  else
    UTILIZATION_PCT = .;
run;
```

**dbt SQL:**
```sql
select
    *,
    case
        when account_type in ('CC','LOC','HELC') and credit_limit > 0
        then (current_balance / credit_limit) * 100
        else null
    end as utilization_pct
from {{ ref('stg_cust_accounts') }}
```

### 4. RETAIN + BY-Group → Window Functions

**SAS:**
```sas
data RUNNING;
  set TRANSACTIONS;
  by ACCOUNT_ID;
  retain RUNNING_BALANCE;
  if first.ACCOUNT_ID then RUNNING_BALANCE = PRE_TXN_BALANCE;
  RUNNING_BALANCE = RUNNING_BALANCE + TRANSACTION_AMOUNT;
run;
```

**Databricks SQL:**
```sql
select
    *,
    pre_txn_balance + sum(transaction_amount) over (
        partition by account_id
        order by transaction_date, transaction_id
        rows unbounded preceding
    ) as running_balance
from transactions
```

### 5. Hash Object Lookup → Broadcast Join

**SAS:**
```sas
if _N_ = 1 then do;
  declare hash h_pol(dataset: "RAW_INS.POLICIES");
  h_pol.definekey('POLICY_ID');
  h_pol.definedata('POLICY_TYPE','SUM_INSURED');
  h_pol.definedone();
end;
rc = h_pol.find();
```

**Databricks SQL:**
```sql
select /*+ BROADCAST(p) */
    c.*,
    p.policy_type,
    p.sum_insured
from claims c
left join policies p on c.policy_id = p.policy_id
```

### 6. PROC APPEND → dbt Incremental

**SAS:**
```sas
%lock(CURATED.DAILY_TRANSACTIONS);
proc append base=CURATED.DAILY_TRANSACTIONS data=WORK.TXN_ENRICHED force;
run;
%lock(CURATED.DAILY_TRANSACTIONS, unlock);
```

**dbt:**
```sql
{{ config(materialized='incremental', incremental_strategy='merge', unique_key='transaction_id') }}
select * from {{ ref('stg_daily_transactions') }}
{% if is_incremental() %}
where transaction_date > (select max(transaction_date) from {{ this }})
{% endif %}
```

### 7. Batch Orchestration → Databricks Workflows

**SAS (run_daily_banking.sas):**
```sas
%run_step(1, Load Customer Accounts, load_customer_accounts.sas)
%run_step(2, Daily Transactions, daily_transaction_processing.sas)
%run_step(3, Credit Risk Scoring, credit_risk_scoring.sas)
```

**Databricks Workflow (JSON):**
```json
{
  "name": "daily_banking_pipeline",
  "tasks": [
    {"task_key": "dbt_staging", "dbt_task": {"commands": ["dbt run --select tag:staging"]}},
    {"task_key": "dbt_intermediate", "depends_on": [{"task_key": "dbt_staging"}],
     "dbt_task": {"commands": ["dbt run --select tag:intermediate"]}},
    {"task_key": "dbt_marts", "depends_on": [{"task_key": "dbt_intermediate"}],
     "dbt_task": {"commands": ["dbt run --select tag:marts"]}}
  ]
}
```

### 8. Macro Variables → dbt vars / env_var

**SAS:**
```sas
%let CURR_DT = %sysfunc(today(), date9.);
%let PREV_YM = %sysfunc(intnx(month, %sysfunc(today()), -1), yymmn6.);
```

**dbt (dbt_project.yml):**
```yaml
vars:
  curr_dt: "{{ run_started_at.strftime('%Y-%m-%d') }}"
  prev_ym: "{{ (run_started_at - modules.datetime.timedelta(days=30)).strftime('%Y%m') }}"
```

## dbt DAG (Dependency Graph)

```
stg_cust_accounts ──→ int_account_metrics ──→ mart_daily_transactions
                                           ├─→ mart_risk_scores
                                           └─→ mart_regulatory_reporting

stg_daily_transactions ──→ mart_daily_transactions
                        └─→ int_transaction_anomalies

stg_claims ──→ int_claims_adjudication ──→ mart_claims_register
stg_policies ──→ int_policy_valuation ──→ mart_loss_ratios
```

## Validation Strategy

For each migrated model, validate equivalence against the SAS output:

1. **Row count parity**: `SELECT COUNT(*)` in dbt must match SAS `%nobs()` output from logs
2. **Column-level checksums**: `SUM(amount)`, `COUNT(DISTINCT id)` must match
3. **Sample record comparison**: Pick 100 random records and compare field-by-field
4. **Business rule validation**: Exception counts (anomalies, rejections) should match SAS exception dataset volumes from logs

The existing `config/validation_rule_config.json` in this repo defines the validation rules. The Streamlit app (`app.py`) provides a UI for executing and reviewing validation results.
