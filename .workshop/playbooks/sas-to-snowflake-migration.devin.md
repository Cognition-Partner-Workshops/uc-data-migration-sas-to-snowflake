# Playbook: Convert and validate a SAS-to-Snowflake migration

> **Facilitator / presenter:** this file is the source for a **Devin Playbook**.
> Copy its contents into your Devin organization (Settings > Playbooks > *Create
> a new Playbook*) so sessions can invoke it as `!convert-sas-to-snowflake`.
> See [Creating Playbooks](https://docs.devin.ai/product-guides/creating-playbooks).
> The repo-specific commands (paths, scenario layout, harness invocations) are
> kept in the companion Skill at
> `.agents/skills/sas-to-snowflake-migration/SKILL.md`, which Devin auto-loads
> when working in this repo.

## Overview

Convert one SAS program to its Snowflake equivalent — **Snowflake SQL** for
set-based programs, **Snowpark Python** for procedural programs — and validate
the output against the SAS source with a programmatic reconciliation harness.
The outcome is a PR containing the converted Snowflake code, a Snowflake Task
definition for orchestration, and a green reconciliation report that proves the
Snowflake output ties out to the SAS source. The value is end-to-end migration
with verifiable confidence: the same source-of-truth check applies to every
program, every table, and every run.

## The one principle: the SAS source is the source of truth

A migration reproduces the SAS numbers faithfully — it does not improve them. If
the legacy data has a quirk (an extra filter, an inactive-account inclusion, a
date-format convention), reproduce it and **flag it** — never silently "correct"
it. Remediating a legacy behaviour is a separate, deliberate decision made with
the business, not a side effect of migration. This is why "looks reasonable"
review is not enough and why every migration is gated by a parity check against
the source.

## Required from user

- **SAS program** — the source program to convert, e.g.
  `monthly_regulatory_reporting.sas`.
- **Target construct** — `sql` (Snowflake SQL for set-based programs) or
  `snowpark` (Snowpark Python for procedural programs with DATA step logic,
  hash lookups, or multi-output datasets).
- **Scenario** — which scenario dataset to validate against, e.g. `Scenario1`
  (baseline) or `Scenario2` (delta). Scenarios provide namespace isolation so
  concurrent runs do not collide.

## Procedure

1. **Read the SAS source** thoroughly. Identify every input table, output table,
   macro dependency, format catalog, business rule, CASE mapping, and filter
   condition. Map the data flow: `RAW` → `STAGING` → `CURATED` → `REPORTS`.
2. **Choose the target construct.** If the SAS program is purely PROC SQL
   (set-based joins, aggregations, CASE logic), convert to **Snowflake SQL**
   under `snowflake_sql/`. If it uses DATA step procedural logic (hash lookups,
   `retain`, multi-output `output` statements, conditional routing), convert to
   **Snowpark Python** under `snowpark/`. Document the choice in the PR.
3. **Convert faithfully.** Translate every SAS construct to its Snowflake
   equivalent:
   - `PROC SQL` → Snowflake SQL (CTEs replace `calculated` refs)
   - `DATA step` → Snowpark DataFrame operations
   - SAS date functions → Snowflake `DATEDIFF`, `DATEADD`, `TO_DATE`
   - SAS formats (`$ACCTTYPE.`, `RISKRATE.`) → no Snowflake equivalent
     (formatting is presentation-layer; store raw codes)
   - SAS macro vars → Snowflake session variables (`$var`)
   - SAS `%include` macros → inline or Snowflake UDFs
   - Control-M scheduling → Snowflake Tasks with CRON expressions
4. **Write a Snowflake Task definition** in `snowflake_sql/orchestration/tasks.sql`
   that schedules the converted program, wires it into the dependency chain, and
   replaces the Control-M job.
5. **Run the reconciliation harness** (`make validate`) against the SAS source
   and the Snowflake output. Check Row Count, Sum Amount, Distinct Count, Not
   Null, Uniqueness.
6. **If any validation fails**, trace upstream lineage using the Collibra-style
   metadata (`lineage/SAS_lineage.json`, `lineage/SF_lineage.json`) to identify
   the transformation job that introduced the divergence. Fix the Snowflake code,
   not the check.
7. **Deliver a PR** that includes the converted Snowflake SQL or Snowpark Python,
   the Task DDL, any validation rule updates, and the green reconciliation
   report.

## Specifications (postconditions)

- The converted Snowflake code produces output that matches the SAS source on
  every configured validation rule.
- Snowflake SQL lives under `snowflake_sql/`, Snowpark Python under `snowpark/`.
- A Snowflake Task definition in `snowflake_sql/orchestration/tasks.sql` wires
  the new job into the dependency DAG.
- Risk weights, thresholds, filter conditions, and business rules are reproduced
  **exactly** from the SAS source. Document any SAS-specific construct that has
  no Snowflake equivalent.
- The PR contains the parity evidence (reconciliation report), not just the code.

## Advice and pointers

- **Date formats** are a common migration trap: SAS uses many date encodings
  (`date9.`, `yymmdd10.`, `mmddyy10.`); Snowflake expects ISO 8601
  (`YYYY-MM-DD`). Always compare date columns in a canonical format.
- **The `is_active` filter** is a frequent divergence: SAS programs often process
  all accounts; a Snowflake migration may add a `WHERE is_active = 'ACTIVE'`
  filter that the source does not have, causing row-count mismatches.
- **`calculated` column references** (SAS PROC SQL) have no Snowflake equivalent.
  Wrap the query in a CTE or subquery.
- **SAS hash objects** map to Snowpark DataFrame joins, not Snowflake SQL JOINs —
  use Snowpark for programs that rely on hash lookups for performance.
- A control that is hard to make pass is usually telling you the migration
  diverged — read the SAS source again before touching the control.

### Worked example: the `is_active` filter divergence

A real defect this loop catches, and the canonical illustration of "source is
truth":

- The SAS computation of Monthly Average Balance processes **all** accounts in
  `WORK.CUST_ACCOUNTS` and `WORK.DAILY_BALANCE`, regardless of account status.
- The Snowflake migration's `JOB03_CALC_AMB.sql` added a
  `WHERE c.is_active = 'ACTIVE'` clause to the join — a plausible optimization
  that excludes inactive accounts.
- The reconciliation harness catches it:

  ```
  Row Count:      SAS=1980   SF=1709    -> FAIL
  Distinct Count: SAS=1000   SF=942     -> FAIL
  Sum Amount:     SAS=5702329  SF=4928656  -> FAIL ($773K gap)
  ```

  271 rows missing, 58 customers lost, $773,673 control-total gap.
- The fix: remove the extra filter, matching the SAS logic faithfully. If the
  business *wants* to exclude inactive accounts going forward, that is a
  deliberate remediation flagged and tracked separately.

The lineage trace shows the divergence originates at the `JOB03_CALC_AMB`
transformation node. The SAS lineage has no such filter.

### Worked example: LOC risk-weight divergence

In `monthly_regulatory_reporting.sas`, the SAS CASE mapping assigns `LOC`
(Lines of Credit) a risk weight of **1.00**. A conversion attempt used 0.75
(matching the `AUTO`/`PERS`/`CC` weight) — a plausible but incorrect
generalization. The RWA parity check flagged the total Risk-Weighted Assets
divergence, and the lineage trace pinpointed it to the `RISK_WEIGHT` CASE in
`JOB04_MONTHLY_REGULATORY.sql`. The fix: set LOC to 1.00, matching the SAS
source exactly.

## Forbidden actions

- Do **not** "improve", clean up, or modernise legacy logic during conversion —
  reproduce it faithfully and flag anomalies for a separate decision.
- Do **not** relax, delete, or hard-code a validation rule to make a report go
  green. Fix the migration, not the check.
- Do **not** modify the SAS source datasets — they are the immutable baseline.
- Do **not** convert more than the one program in scope for this session (fan out
  via child sessions for multi-program conversion).
- Do **not** write into another run's namespace or the durable raw source tables.

## Parallel fan-out

Each SAS program conversion is independent, so migrations parallelise cleanly:
run one session per program (each with its own scenario directory), or one
orchestrator session that spawns a child per program. Because this playbook
fixes the procedure and the validation contract, every session's output is
consistent and independently verified — the same review bar applied N times in
parallel instead of once in series.
