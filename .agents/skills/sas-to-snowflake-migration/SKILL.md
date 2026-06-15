---
name: sas-to-snowflake-migration
description: Repo mechanics for validating a SAS-to-Snowflake data migration in this repo — validation commands, scenario layout, lineage tracing, where controls live. Supplements the general !validate-sas-to-snowflake playbook.
---

## When to use this

Use this skill whenever you are validating a SAS-to-Snowflake table migration
**in this repository**. It is the repo-specific companion to the general
procedure in the `!validate-sas-to-snowflake` playbook
(`.workshop/playbooks/sas-to-snowflake-migration.devin.md`): the playbook says
*what* to do and *why* (source-parity principle, procedure, forbidden actions);
this skill says *how* to do it here (exact commands, paths, scenarios).

## Layout

- SAS source estate (read-only reference): the `ts-sas-legacy-analytics` repo.
- SAS source datasets (`.sas7bdat`): `sample_data/MONTHLY_AMB.sas7bdat`,
  `sample_data/CUST_ACCOUNTS.sas7bdat`, `sample_data/DAILY_BALANCE.sas7bdat`.
- Snowflake target datasets (CSV exports, per scenario):
  `sample_data/Scenario1/` (baseline), `sample_data/Scenario2/` (delta).
- Validation rules: `config/validation_rule_config.json` (LLM column
  recommendations), `config/validations_list.csv` (rule checklist).
- Lineage metadata: `lineage/SAS_lineage.json`, `lineage/SF_lineage.json`
  (Collibra-style JSON); `lineage/SAS_lineage_graph.pkl`,
  `lineage/SF_lineage_graph.pkl` (NetworkX graphs for traversal).
- Validation harness: `helper_functions.py` (core reconciliation logic),
  `verify/reconcile.py` (CLI entry point).
- Streamlit dashboard: `app4.py` (interactive UI for exploring results).
- LLM agents: `llm_agents/` (column recommendations, report generation).

## Scenarios (isolated, concurrent-safe)

Each validation run targets a scenario directory under `sample_data/`. Scenarios
are the namespace mechanism: `Scenario1` is the baseline migration,
`Scenario2` is the delta migration. For concurrent runs, create additional
scenario directories (`Scenario3`, `ScenarioN`, or `Scenario_<session_id>`).
The SAS source `.sas7bdat` files in `sample_data/` are the immutable baseline —
never modified.

## Tables available

| Table | SAS source | Snowflake columns |
|---|---|---|
| `CUST_ACCOUNTS` | `customer_id`, `account_id`, `account_type`, `is_active`, `start_date`, `end_date` | same |
| `DAILY_BALANCE` | `customer_id`, `account_id`, `date`, `end_of_day_balance`, `month` | same |
| `MONTHLY_AMB` | `customer_id`, `account_id`, `reporting_month_yyyymm`, `average_monthly_balance`, `date_computed` | same |

## Validate (CLI)

```bash
# Full validation for one table against a scenario:
python verify/reconcile.py --table MONTHLY_AMB --scenario Scenario1

# Full validation for all tables in a scenario:
python verify/reconcile.py --scenario Scenario1

# Quick smoke test (row counts only):
python verify/reconcile.py --scenario Scenario1 --quick
```

The harness loads the SAS `.sas7bdat` as the source of truth, compares against
the scenario CSV, runs all configured validation rules, and exits non-zero on
any `FAIL`. Output is a human-readable report suitable for PR inclusion.

## Make targets

```bash
make validate SCENARIO=Scenario1         # run full validation suite
make validate-all                        # validate all scenarios
make dashboard                           # launch Streamlit validation UI
make lint                                # ruff check on Python files
```

## Adding validation rules for a new table

1. Add entries to `config/validations_list.csv` with the table name and rules.
2. Add LLM column recommendations to `config/validation_rule_config.json` (or
   let the LLM agent suggest them on first run).
3. Run `make validate SCENARIO=<scenario>` to execute.

## Close the loop

If a validation fails, investigate against the SAS source — **do not** relax,
delete, or hard-code the validation to make it pass. Fix the Snowflake
migration script and re-export the data, then re-run validation until the
report is green. Include the green report in the PR.

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
