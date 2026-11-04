# JOB03_CALC_AMB replay

## Source contract and implementation

The immutable `sample_data/*.sas7bdat` files are the legacy run, not a new
Python implementation of SAS. There is no SAS runtime here. The two
`JOB03_CALC_AMB` input edges in `lineage/SAS_lineage.json` identify
`WORK.CUST_ACCOUNTS` and `WORK.DAILY_BALANCE`. The companion estate's
`BatchJobs/run_daily_banking.sas` propagates an explicit run date to child
programs, but does not contain the fixture's inline JOB03 program text.
Do not substitute its credit-risk step for this fixture job.

This is a set-based join and aggregation, so the business logic is Snowflake
SQL, not Snowpark or dbt. A caller-rights SQL procedure provides explicit
namespaces and batch arguments to both the runner and the existing Task DAG.

The draft did not match the actual source schema or frozen run:

- Read `CUST_ACCOUNTS`, not the draft's `CUST_ACCOUNTS_DAILY`.
- `DAILY_BALANCE.MONTH` is a SAS date (month start), not a YYYYMM column.
- Inputs contain July **and August** 2025. The legacy output contains only
  `202507`: filter `MONTH` using the explicit reporting-month parameter.
- The legacy output's balances equal July's average daily balances rounded
  to **two decimals** for every key. Preserve that rounding in the job;
  do not apply it only in the comparison.
- All 1,980 legacy rows have `DATE_COMPUTED = 2025-08-01`. Replay this as
  `RUN_DATE`, not today's date. Neither period nor run date is inferred from
  the clock or copied from legacy rows while computing the new output.
- Include all account statuses: 271 inactive accounts remain in the output.
  Do not add an `IS_ACTIVE`, start-date or end-date filter.

The parameter defaults are the frozen run, not a production scheduling policy.
Changing month/run date intentionally changes the result and will not match
this frozen legacy output.

## Isolated dual-run

Database: `SFNFR_20261104`; warehouse: **WH_SAS_AMB** only.

| Role | Schema | Tables |
|---|---|---|
| SRC | `SAS_AMB_SRC_SFNFR_20261104` | `CUST_ACCOUNTS`, `DAILY_BALANCE` |
| LEGACY | `SAS_AMB_LEGACY_SFNFR_20261104` | `MONTHLY_AMB` loaded from SAS output |
| SF | `SAS_AMB_SF_SFNFR_20261104` | SQL procedure and newly computed `MONTHLY_AMB` |

`load` replaces only these three tables. It reads `.sas7bdat` via
`pandas.read_sas(encoding=None)`, decodes UTF-8 bytes without stripping them,
preserves NULLs, validates that numeric IDs can be represented losslessly as
integers, and converts datetime64 dates directly to Python dates (never Unix
or SAS epoch arithmetic). SAS balances remain IEEE double precision (`FLOAT`)
on input and in the legacy output. Source SHA-256 values and loaded row counts
are recorded in `evidence/MONTHLY_AMB/load.json`. No inputs are committed again.

`run` deploys `snowflake_sql/JOB03_CALC_AMB.sql` to SF, then calls it with SRC
and SF namespaces. It never reads LEGACY. `compare` reads both outputs, creates
session-temporary canonical views only in SF, and exports the **new Snowflake
table**, not the legacy table, in business-key order.

The existing `snowflake_sql/orchestration/tasks.sql` JOB03 node now calls the
same procedure on WH_SAS_AMB with explicit frozen-run arguments, retaining
`AFTER TASK_JOB02_DAILY_TRANSACTIONS` and the JOB04 successor. This file remains
the existing FINANCE_DB Task-DAG deployment template. **Do not execute the
whole file for this isolated run**: it also provisions a warehouse and manages
unrelated jobs outside these schemas. No Tasks were deployed or resumed here;
the procedure was executed directly. Production Task activation and batch
parameter policy are separate from this frozen replay.

## Exact rerun commands

Run from the repository root. All schemas and the warehouse must already exist.

```sh
uv venv ~/sfenv
uv pip install --python ~/sfenv/bin/python -r snowflake_sql/dual_run/requirements.txt

~/sfenv/bin/python snowflake_sql/dual_run/monthly_amb.py load
~/sfenv/bin/python snowflake_sql/dual_run/monthly_amb.py run \
  --database SFNFR_20261104 \
  --source-schema SAS_AMB_SRC_SFNFR_20261104 \
  --target-schema SAS_AMB_SF_SFNFR_20261104 \
  --reporting-month 202507 --run-date 2025-08-01
~/sfenv/bin/python snowflake_sql/dual_run/monthly_amb.py compare
make validate TABLE=MONTHLY_AMB SCENARIO=Scenario_SFNFR_20261104 PYTHON="$HOME/sfenv/bin/python"

~/sfenv/bin/python -m pytest -q snowflake_sql/dual_run/test_monthly_amb.py
~/sfenv/bin/python -m ruff check snowflake_sql/dual_run
~/sfenv/bin/python -m pyright --pythonpath "$HOME/sfenv/bin/python" snowflake_sql/dual_run
```

`all` runs load, job and comparison in sequence. For a new namespace, supply
`--database`, `--source-schema`, `--legacy-schema`, `--target-schema`,
`--scenario-dir` and `--evidence-dir` consistently to each invocation.

Each Snowflake command needs these environment variables bound securely from
the **same-named org secrets**, in each Devin exec call:

```python
env={
    "SFNFR_ACCOUNT": "secret:org:SFNFR_ACCOUNT",
    "SFNFR_USER": "secret:org:SFNFR_USER",
    "SFNFR_ROLE": "secret:org:SFNFR_ROLE",
    "SFNFR_PRIVATE_KEY": "secret:org:SFNFR_PRIVATE_KEY",
}
```

Only these four credentials are used. The PKCS8 PEM is decoded in memory,
converted to DER and passed to the Python connector with `SNOWFLAKE_JWT`.
Do not write the key to a profile or file, or use other runs' credentials.

## Evidence and comparison

- `evidence/MONTHLY_AMB/parity.md` and `parity.json`: L1 counts, L2 profiles,
  L3 keyed MD5 comparison and table checksums; up to 20 L4 mismatch samples.
- `canonical_diff.sql`: exact replayable Snowflake comparison statements.
- `reconcile.txt`: unchanged repository gate against the SAS dataset.
- `sample_data/Scenario_SFNFR_20261104/MONTHLY_AMB.csv`: the new output.

Canonical rows use **legacy column order**. NULL is `\N`; numbers are cast
to NUMBER(38,6), rounded to six places then converted to text; dates use
YYYY-MM-DD; strings use TRIM; values are joined with `|`. Both sides use the
same expression. The five output columns contain no timestamps.

Checks include `COUNT(*)`, `HASH_AGG(canonical_row)`, null-safe keyed full outer
join on MD5 hashes, and **multiset** subtraction in both directions. Grouped
row multiplicities implement EXCEPT ALL semantics because ordinary EXCEPT
would hide duplicates. Duplicate business-key groups are also checked explicitly.
L2 profiles cover count, nulls, distinct, min/max, numeric sum and value lengths;
FLOAT sums are compared at the same six-place precision as canonical numbers.
Any mismatch, profile difference or duplicate key makes `compare` exit nonzero.
Neither `verify/reconcile.py` nor `config/validations_list.csv` is modified.
