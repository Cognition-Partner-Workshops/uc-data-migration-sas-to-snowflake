# MONTHLY_AMB — frozen-run parity

**PASS — L1, L2 and L3 match. No mismatches require explanation.**

| Output | Rows in | Legacy rows | New rows | Keyed mismatches | Legacy checksum | New checksum |
|---|---:|---:|---:|---:|---|---|
| MONTHLY_AMB | 124,740 | 1,980 | 1,980 | 0 | -6873172765739195680 | -6873172765739195680 |

Rows in means all loaded inputs: 1,980 CUST_ACCOUNTS + 122,760 DAILY_BALANCE.
61,380 balance rows are eligible for the July reporting period; August rows
remain in SRC but are outside this legacy run's window.

- SRC: `SFNFR_20261104.SAS_AMB_SRC_SFNFR_20261104.CUST_ACCOUNTS` and
  `SFNFR_20261104.SAS_AMB_SRC_SFNFR_20261104.DAILY_BALANCE`.
- Legacy: `SFNFR_20261104.SAS_AMB_LEGACY_SFNFR_20261104.MONTHLY_AMB`.
- New: `SFNFR_20261104.SAS_AMB_SF_SFNFR_20261104.MONTHLY_AMB`.
- Key: `customer_id`, `account_id`, `reporting_month_yyyymm`.
- Parameters: reporting month **202507**, run date **2025-08-01**.
- Same inputs, new computation in Snowflake; no SAS runtime was invoked.
- Multiset differences: **0** legacy-only and **0** new-only.
- Duplicate business-key groups: **0** on either side.
- All five column profiles match (see [JSON](parity.json)).
- L3 match rate: **100%**. L4 samples: none, since there are no mismatches.
- Legacy/new total average monthly balance: **5,702,328.99**.
- Distinct customers: **1,000** each; inactive accounts are included.

The unchanged harness [report](reconcile.txt) passes all three configured checks:
Row Count, Distinct Count and Sum Amount. No tolerances or legacy files changed.
The stricter key/duplicate/column checks are additional, not replacements for
that harness.

The legacy output, not the flawed draft, establishes July-only selection,
two-decimal output rounding and the replay run date. These are implementation
corrections, not exceptions or waived mismatches. See the
[source decisions and rerun commands](../../snowflake_sql/dual_run/README.md).

The SQL compiles and executes on Snowflake WH_SAS_AMB. The repository has no
`wp` deploy harness, so the actual Python connector deployment/execution is the
equivalent compilation check. The broader illustrative Task DAG was not run:
its unrelated FINANCE_DB objects are outside the isolated run's scope.

Regression coverage includes SAS byte/date/null/integer typing, unsafe SQL
identifier rejection, inactive accounts, month isolation, both join keys,
rounding, fixed run date, canonical column order, and full frozen-window SQL
parity independently executed with DuckDB. DuckDB is a local regression engine,
not a replacement for the Snowflake dual-run evidence.
