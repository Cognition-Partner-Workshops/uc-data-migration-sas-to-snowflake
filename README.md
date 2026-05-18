# SAS to Databricks Migration — Validation & Lineage Toolkit

A comprehensive toolkit for SAS-to-Databricks/Snowflake data migration validation, including lineage metadata, sample banking datasets, a dbt target project, and a Streamlit validation UI.

## Repository Structure

```
├── lineage/                      # Data lineage metadata
│   ├── SAS_lineage.json          # Source SAS lineage (Collibra-style JSON)
│   ├── SF_lineage.json           # Target Snowflake/Databricks lineage
│   └── SAS_DIS_Lineage_Generator.sas  # PROC METADATA lineage extraction
├── data/                         # Sample banking datasets
│   ├── CUST_ACCOUNTS.sas7bdat    # Customer accounts (SAS native)
│   ├── DAILY_BALANCE.sas7bdat    # Daily balance snapshots
│   ├── MONTHLY_AMB.sas7bdat      # Monthly average balances
│   └── *.csv                     # CSV equivalents for validation
├── dbt_project/                  # dbt target architecture (Databricks)
│   ├── models/
│   │   ├── staging/              # Raw source → staging (replaces LIBNAME extracts)
│   │   ├── intermediate/         # Business logic (replaces DATA steps)
│   │   └── marts/                # Final outputs (replaces CURATED/REPORTS)
│   ├── macros/                   # PROC FORMAT → dbt Jinja macros
│   ├── dbt_project.yml           # Project config with Databricks profile
│   └── profiles.yml              # Databricks connection config
├── docs/
│   └── SAS_TO_DBT_MIGRATION_MAP.md  # Complete SAS→dbt construct mapping
├── config/
│   ├── validation_rule_config.json   # Validation rules for migration QA
│   └── validations_list.csv          # Validation checklist
├── app.py                        # Streamlit validation dashboard
└── llm_agents/                   # LLM-powered migration recommendations
```

## Quick Start

### Prerequisites

- Python 3.9+
- Streamlit (`pip install streamlit`)

### Run the Validation Dashboard

```bash
pip install streamlit pandas
streamlit run app.py
```

### SAS OnDemand for Academics (Optional)

For testing SAS-side operations, register for a free account at [SAS OnDemand for Academics](https://welcome.oda.sas.com/).

### SAS Code to Convert CSV to .sas7bdat

```sas
libname mydata '/home/<your_user_id>/my_sas_data';

PROC IMPORT DATAFILE='/home/<your_user_id>/creditscores.csv'
    OUT=mydata.creditscores
    DBMS=CSV
    REPLACE;
RUN;
```

## dbt Target Architecture

The `dbt_project/` directory contains the target state for the migration — every SAS program in `ts-sas-legacy-analytics` has a corresponding dbt model:

| SAS Source Program | dbt Model | Migration Pattern |
|---|---|---|
| `load_customer_accounts.sas` | `stg_cust_accounts` → `int_account_metrics` | PROC SQL + DATA step → SQL + CASE |
| `daily_transaction_processing.sas` | `stg_daily_transactions` → `mart_daily_transactions` | RETAIN → window function |
| `credit_risk_scoring.sas` | `mart_risk_scores` | WOE scorecard → nested CASE + exp() |
| `claims_processing.sas` | (planned) `stg_claims` → `int_claims_adjudication` | Hash lookup → broadcast join |
| `policy_valuation.sas` | (planned) `int_policy_valuation` → `mart_loss_ratios` | MERGE → SQL JOIN |

See `docs/SAS_TO_DBT_MIGRATION_MAP.md` for the complete construct-level mapping.

## Lineage Metadata

The `lineage/` directory contains Collibra-style lineage JSON mapping data flows from SAS sources to target tables:

- **SAS_lineage.json**: Nodes and edges representing the SAS-side data flow
- **SF_lineage.json**: Target-side lineage in Snowflake/Databricks
- **SAS_DIS_Lineage_Generator.sas**: Macro template for extracting lineage from SAS Metadata Server via PROC METADATA

## Validation Approach

1. **Row count parity**: Compare `%nobs()` from SAS logs against `COUNT(*)` in Databricks
2. **Column checksums**: `SUM()`, `COUNT(DISTINCT)` comparisons
3. **Sample record validation**: 100-record spot checks field-by-field
4. **Business rule verification**: Exception counts match between SAS and dbt outputs

The Streamlit app provides a visual interface for executing and reviewing validation results against the rules defined in `config/validation_rule_config.json`.
