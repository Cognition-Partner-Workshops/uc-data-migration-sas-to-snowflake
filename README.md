# SAS to Snowflake Migration — Validation & Lineage Toolkit

A validation framework for SAS-to-Snowflake data migrations, including lineage metadata, sample banking datasets (.sas7bdat + CSV), validation configurations, and a Streamlit validation UI with LLM-powered recommendations.

## Repository Structure

```
├── lineage/                      # Data lineage metadata
│   ├── SAS_lineage.json          # Source SAS lineage (Collibra-style JSON)
│   ├── SF_lineage.json           # Target Snowflake lineage
│   └── SAS_DIS_Lineage_Generator.sas  # PROC METADATA lineage extraction
├── sample_data/                  # Sample banking datasets
│   ├── Scenario1/                # Baseline migration scenario
│   │   ├── CUST_ACCOUNTS.*       # Customer accounts (SAS + CSV)
│   │   ├── DAILY_BALANCE.*       # Daily balance snapshots
│   │   └── MONTHLY_AMB.*         # Monthly average balances
│   └── Scenario2/                # Delta migration scenario
├── config/
│   ├── validation_rule_config.json   # Validation rules for migration QA
│   └── validations_list.csv          # Validation checklist
├── app.py                        # Streamlit validation dashboard
├── llm_agents/                   # LLM-powered migration recommendations
└── test_code/                    # Validation test scripts
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

## Lineage Metadata

The `lineage/` directory contains Collibra-style lineage JSON mapping data flows from SAS sources to Snowflake target tables:

- **SAS_lineage.json**: Nodes and edges representing the SAS-side data flow
- **SF_lineage.json**: Target-side lineage in Snowflake
- **SAS_DIS_Lineage_Generator.sas**: Macro template for extracting lineage from SAS Metadata Server via PROC METADATA

## Validation Approach

1. **Row count parity**: Compare `%nobs()` from SAS logs against `COUNT(*)` in Snowflake
2. **Column checksums**: `SUM()`, `COUNT(DISTINCT)` comparisons
3. **Sample record validation**: 100-record spot checks field-by-field
4. **Business rule verification**: Exception counts match between SAS and Snowflake outputs

The Streamlit app provides a visual interface for executing and reviewing validation results against the rules defined in `config/validation_rule_config.json`.

## Related Repositories

| Repo | Purpose |
|---|---|
| [`ts-sas-legacy-analytics`](https://github.com/Cognition-Partner-Workshops/ts-sas-legacy-analytics) | Source SAS estate (banking/insurance programs, macros, formats, batch orchestration) |
| [`uc-data-migration-sas-to-databricks`](https://github.com/Cognition-Partner-Workshops/uc-data-migration-sas-to-databricks) | dbt/Databricks migration target architecture and construct mapping |
