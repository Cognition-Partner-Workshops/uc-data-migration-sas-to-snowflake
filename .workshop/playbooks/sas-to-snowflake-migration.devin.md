# Playbook: Validate a SAS-to-Snowflake data migration

> **Facilitator / presenter:** this file is the source for a **Devin Playbook**.
> Copy its contents into your Devin organization (Settings > Playbooks > *Create
> a new Playbook*) so sessions can invoke it as `!validate-sas-to-snowflake`.
> See [Creating Playbooks](https://docs.devin.ai/product-guides/creating-playbooks).
> The repo-specific commands (paths, scenario layout, harness invocations) are
> kept in the companion Skill at
> `.agents/skills/sas-to-snowflake-migration/SKILL.md`, which Devin auto-loads
> when working in this repo.

## Overview

Run a **programmatic reconciliation** of one SAS-to-Snowflake table migration.
The outcome is a PR containing any conversion fixes plus a reconciliation report
that proves the Snowflake data ties out to the SAS source. The value is
consistency: every table is validated the same way and every migration is gated
by parity checks against the source.

## The one principle: the SAS source is the source of truth

A migration reproduces the SAS numbers faithfully — it does not improve them. If
the legacy data has a quirk (an extra filter, an inactive-account inclusion, a
date-format convention), reproduce it and **flag it** — never silently "correct"
it. Remediating a legacy behaviour is a separate, deliberate decision made with
the business, not a side effect of migration. This is why "looks reasonable"
review is not enough and why every migration is gated by a parity check against
the source.

## Required from user

- **SAS table** — the source table to validate, e.g. `MONTHLY_AMB`.
- **Snowflake table** — the target table in Snowflake, e.g. `MONTHLY_AMB`.
- **Scenario** — which scenario dataset to validate against, e.g. `Scenario1`
  (baseline) or `Scenario2` (delta). Scenarios provide namespace isolation so
  concurrent runs do not collide.

## Procedure

1. Load the SAS source dataset (`.sas7bdat` or the SAS-exported CSV baseline)
   and the Snowflake target dataset (CSV export or live Snowflake query). Decode
   SAS byte-encoded character columns.
2. Identify the validation rules that apply to this table from the
   `config/validation_rule_config.json` and `config/validations_list.csv`. If
   the table is new, use the LLM-powered column recommender to suggest
   appropriate columns for each rule type.
3. Run the full validation suite against both datasets: **Row Count** (no silent
   row loss or fan-out), **Sum Amount** (control totals tie out), **Distinct
   Count** (cardinality matches), **Not Null** (no unexpected nulls introduced),
   **Uniqueness** (key constraints preserved), and **Row Hash** (deep equality
   where applicable).
4. If any validation fails, trace **upstream lineage** using the Collibra-style
   lineage metadata (`lineage/SAS_lineage.json`, `lineage/SF_lineage.json`) to
   identify the transformation job that introduced the divergence.
5. Investigate against the **SAS source** — do not relax the check to make it
   pass. Correct the Snowflake SQL or migration script and re-run until the
   validation report is green.
6. If upstream tables also show failures, perform **recursive root-cause
   analysis**: walk the lineage graph upstream until you find the first table
   where the divergence originates.
7. Deliver a PR that includes any conversion fixes, updated validation rules,
   and the reconciliation report output, so a reviewer sees the parity evidence,
   not just the code.

## Specifications (postconditions)

- Every validation rule passes for the target table: Row Count, Sum Amount,
  Distinct Count, Not Null, Uniqueness.
- The PR contains any fixes, the validation config, and the reconciliation
  report.
- Any source quirk reproduced is explicitly flagged in code and in the PR —
  never silently changed.
- Upstream lineage is traced for any failure, with root-cause documented.

## Advice and pointers

- **Date formats** are a common migration trap: SAS uses many date encodings
  (`date9.`, `yymmdd10.`, `mmddyy10.`); Snowflake typically expects ISO 8601
  (`YYYY-MM-DD`). Always compare date columns in a canonical format.
- **The `is_active` filter** is a frequent divergence: SAS programs often process
  all accounts; a Snowflake migration may add a `WHERE is_active = 'ACTIVE'`
  filter that the source does not have, causing row-count mismatches.
- A control that is hard to make pass is usually telling you the migration
  diverged — read the SAS source again before touching the control.
- The Streamlit dashboard (`app4.py`) provides an interactive visual interface
  for exploring validation results, but the CLI harness (`verify/reconcile.py`)
  is the programmatic gate.

### Worked example: the `is_active` filter divergence

A real defect this loop catches, and the canonical illustration of "source is
truth":

- The SAS `JOB03_CALC_AMB.sas` computes the Monthly Average Balance for **all**
  accounts in `WORK.CUST_ACCOUNTS` and `WORK.DAILY_BALANCE`, regardless of
  account status.
- The Snowflake migration's `JOB03_CALC_AMB.sql` added a
  `WHERE c.is_active = 'ACTIVE'` clause to the join — a plausible optimization
  that excludes inactive accounts.
- The Sum Amount validation on `average_monthly_balance` catches it: the
  Snowflake total is lower because inactive accounts (roughly 1 in 7, per the
  synthetic data generation) are excluded from the AMB calculation.
- The fix is to remove the extra filter, matching the SAS logic faithfully. If
  the business *wants* to exclude inactive accounts going forward, that is a
  deliberate remediation flagged and tracked separately — not a silent side
  effect of migration.

The lineage trace shows the divergence originates at the `JOB03_CALC_AMB`
transformation node (visible in `SF_lineage.json` where the `highlights` field
contains `WHERE c.is_active = 'ACTIVE'`). The SAS lineage has no such filter.

## Forbidden actions

- Do **not** "improve", clean up, or modernise legacy logic during validation —
  reproduce it faithfully and flag anomalies for a separate decision.
- Do **not** relax, delete, or hard-code a validation rule to make a report go
  green. Fix the migration, not the check.
- Do **not** modify the SAS source datasets — they are the immutable baseline.
- Do **not** validate more than the one table in scope for this session (fan out
  via child sessions for multi-table validation).

## Parallel fan-out

Each table migration is independent, so validations parallelise cleanly: run one
session per table (each with its own scenario directory), or one orchestrator
session that spawns a child per table. Because this playbook fixes the procedure
and the validation contract, every session's output is consistent and
independently verified — the same review bar applied N times in parallel instead
of once in series.
