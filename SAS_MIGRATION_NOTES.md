# SAS to Python/Snowflake Migration Notes

This document records every translation decision made while converting the eight
SAS macros in `ts-sas-legacy-codebase/Macro/` to Python/pandas and while
designing the Snowflake DDL for the three sample datasets.

---

## 1  Macro-by-Macro Translation Decisions

### 1.1  `transpose.sas` → `transpose()`

| SAS construct | Python equivalent | Notes |
|---|---|---|
| `PROC TRANSPOSE` | `DataFrame.melt()` + `pivot_table()` | Two-step melt/pivot reproduces BY-group transpose |
| `BY` statement with `PROC SORT` | `sort_values(by)` before melt | SAS auto-sorts; Python requires an explicit step |
| `NOTSORTED` option | `sort=False` passthrough | Preserves encounter order of BY groups |
| `_NAME_` / `_LABEL_` automatic vars | Explicit column creation/dropping | `_LABEL_` is always empty because pandas has no column labels |
| `COL1 … COLn` rename via `COL=` | Dictionary rename after pivot | Iterates user-supplied names against generated `COLn` columns |
| `ID` statement | `pivot_table(columns=id_col)` | Values of `id_col` become output column names |
| `LET` option (last dup wins) | `drop_duplicates(keep='last')` before pivot | Applied only when both `id_col` and `let=True` |
| `COPY` statement | Side-merge of copy columns | Grouped first-value per BY key merged back |
| `WHERE=` dataset option | `DataFrame.query()` | Applied to input before transposing |
| `PREFIX=` option | Custom column prefix string | Replaces default `COL` prefix |

**Key difference:** SAS `_LABEL_` carries the variable label from the input
dataset's metadata.  pandas DataFrames do not store per-column labels, so
`_LABEL_` is either omitted (`label=None`) or populated with empty strings.

### 1.2  `subset_data.sas` → `subset_data()`

| SAS construct | Python equivalent | Notes |
|---|---|---|
| `DATA … SET … WHERE` | `DataFrame.query()` | Pandas query syntax replaces SAS WHERE |
| Subsetting `IF` | `if_func` callback `f(df) → bool Series` | SAS `if` runs per-row in the DATA step; a vectorized callable is idiomatic in pandas |
| `OBS= "1-5 or 11-15"` | Regex parser → boolean index mask | Parsed identically to SAS macro: ranges and `or` keyword |
| `FIRSTOBS=` / `LASTOBS=` (1-based) | `iloc[start:end]` (0-based) | Converted to 0-based slicing internally |
| `RENAME=(old=new)` | `DataFrame.rename(columns={...})` | Applied *before* WHERE/KEEP, matching SAS precedence |
| `KEEP=` / `DROP=` | Column selection / `drop()` | Applied last |

**SAS missing-value nuance:** In SAS, `call missing(of _all_)` sets all vars
to missing (`.` for numeric, `""` for character).  The Python `if_func`
parameter lets callers replicate arbitrary row-level logic, including
`lambda df: df.notna().any(axis=1)` for the "delete blank rows" pattern.

### 1.3  `compare.sas` → `compare()`

| SAS construct | Python equivalent | Notes |
|---|---|---|
| `PROC COMPARE` | Custom comparison loop | No single pandas call reproduces PROC COMPARE |
| Library-level compare | Not implemented | Python operates on DataFrames, not libraries; callers iterate |
| `BY` statement | `merge(how='outer', indicator=True)` | Identifies rows in base-only, comp-only, and both |
| `CRITERION=` / `METHOD=` | `abs(base - comp) > criterion` | EXACT → strict `!=`; ABSOLUTE → fuzz tolerance |
| `MAXPRINT=` | Not needed | All diffs returned in `CompareResult.value_diffs` |
| `LISTBASEVAR` / `LISTCOMPVAR` | `base_only_columns` / `comp_only_columns` | Reported as lists on the result object |
| SPDE temp library | Not applicable | Python uses in-memory DataFrames |

**Return type:** SAS PROC COMPARE writes to the log and optionally creates an
`OUT=` dataset.  The Python version returns a `CompareResult` dataclass with
structured attributes (`equal`, `value_diffs`, `base_only_rows`, etc.) for
programmatic inspection.

### 1.4  `dedup_string.sas` → `dedup_string()`

| SAS construct | Python equivalent | Notes |
|---|---|---|
| `SCAN()` + `INDEXW()` loop | `str.split()` + `set` lookup | O(n) vs SAS O(n²) INDEXW; same output |
| `UPCASE()` comparison | `.upper()` key in set | Case-insensitive dedup, original casing preserved |
| Hash object in DATA step | Python `set` | SAS 9.2+ hash; functionally identical |
| `DLM=` parameter | `dlm` parameter | Defaults to space in both |

**Scope:** The SAS macro generates inline DATA step code (`%dedup_string`
expands inside a `data … run;` block).  The Python function is standalone and
returns a new string.

### 1.5  `dedup_mstring.sas` → `dedup_mstring()`

| SAS construct | Python equivalent | Notes |
|---|---|---|
| `%SCAN` / `%INDEXW` macro functions | `re.split()` + `set` | Pure macro execution (compile time) |
| Multi-char `INDLM=` | `re.split('[…]', s)` | Each char in INDLM is a separate delimiter |
| `DLM=` output delimiter | `dlm` parameter | Defaults to INDLM if single char, else space |
| `%UNQUOTE` r-value return | Direct return | No macro quoting needed in Python |
| `%SUPERQ` / `%BQUOTE` | Not needed | Python strings are not subject to macro tokenizing |

**SAS macro quoting:** The original macro requires `%str(,)` to pass commas as
delimiters without confusing the SAS macro tokenizer.  Python has no analog; the
delimiter is simply a string parameter.

### 1.6  `export_csv.sas` → `export_csv()`

| SAS construct | Python equivalent | Notes |
|---|---|---|
| `%export_dlm(dbms=csv)` | `DataFrame.to_csv()` | Direct pandas call |
| `LABEL=Y` header row | `labels` dict → custom header | SAS reads labels from dataset metadata; Python requires explicit mapping |
| `HEADER=N` | `header=False` | Suppresses column names row |
| `REPLACE=` | `replace` bool + `FileExistsError` | Matches SAS behaviour of aborting if file exists |
| `LRECL=` | Not applicable | CSV line length is unlimited in pandas |

### 1.7  `export_xlsx.sas` → `export_xlsx()`

| SAS construct | Python equivalent | Notes |
|---|---|---|
| `%export_dbms(dbms=xlsx)` | `DataFrame.to_excel(engine='openpyxl')` | openpyxl required for `.xlsx` |
| `LABEL=Y` | Custom header list | Same label-mapping pattern as CSV |
| `.bak` file cleanup | `os.remove()` | SAS PROC EXPORT creates backups; pandas does not, but cleanup is kept for parity |

### 1.8  `export_dbms.sas` → `export_dbms()`

| SAS construct | Python equivalent | Notes |
|---|---|---|
| `PROC EXPORT … DBMS=` | Dispatch on `dbms` string | Routes to `to_csv`, `to_excel`, `to_stata` |
| Quoted physical path vs fileref | `os.path` resolution | Quotes stripped; directory → auto-filename |
| `DBMS=SPSS` | `NotImplementedError` | Requires `pyreadstat`; not included by default |
| `DBMS=STATA` | `DataFrame.to_stata()` | Built-in pandas support |
| `.bak` cleanup | `os.remove()` if exists | Same as SAS |
| `FMTLIB=work.formats` | Not applicable | SAS user-defined formats have no direct pandas analog |

---

## 2  SAS-Specific Construct Mappings

### 2.1  Macro Variables (`&var`, `%let`, `%sysfunc`)

SAS macro variables are compile-time string substitutions.  In Python they map
to function parameters or module-level constants.  There is no need for a
separate "macro layer" because Python functions are first-class.

| SAS | Python |
|---|---|
| `%let x = value;` | `x = "value"` |
| `&syslast` | Explicit DataFrame argument |
| `%sysfunc(exist(&ds))` | `isinstance(df, pd.DataFrame)` or path checks |
| `%superq(var)` | Not needed — no quoting issues |

### 2.2  Formats and Informats

SAS formats control *display*, while informats control *input parsing*.

| SAS format | Snowflake / Python mapping |
|---|---|
| `DATE9.` (e.g. `01JAN2020`) | `DATE` column; `pd.to_datetime()` |
| `COMMA12.2` | `DECIMAL(12,2)`; float in pandas |
| `$CHAR200.` | `VARCHAR(200)`; `str` in Python |
| `MMDDYY10.` | `DATE` with `strptime('%m/%d/%Y')` |
| `BEST12.` | Default numeric display — no equivalent needed |

### 2.3  Missing Value Handling

| SAS behaviour | Python / Snowflake equivalent |
|---|---|
| Numeric missing = `.` | `NaN` / `None` in pandas; `NULL` in Snowflake |
| Character missing = `""` | `""` or `NaN` in pandas; `NULL` or `''` in Snowflake |
| `.A` – `.Z` special missings | No direct equivalent; use sentinel values or a separate flag column |
| `CALL MISSING(of _all_)` | `df.loc[idx] = np.nan` |
| `MISSING()` function | `pd.isna()` / `IS NULL` |
| Missing sorts low | `NaN` sorts differently in pandas; use `na_position='first'` to match SAS |

**Decision:** The Snowflake `COPY INTO` stage uses `NULL_IF = ('', 'NA', '.')` to
translate SAS missing markers (`.`, blank, `NA`) to SQL `NULL`.

### 2.4  BY-Group Processing

SAS `FIRST.var` / `LAST.var` automatic variables track group boundaries in a
sorted DATA step.  Pandas equivalents:

```python
# FIRST.var
df.groupby('var').cumcount() == 0

# LAST.var
df.groupby('var').cumcount(ascending=False) == 0
```

### 2.5  SAS Date Internals

SAS stores dates as the number of days since **1 January 1960**.  Python
`datetime` and Snowflake `DATE` use different epochs.  Conversion:

```python
import pandas as pd
sas_epoch = pd.Timestamp("1960-01-01")
python_date = sas_epoch + pd.to_timedelta(sas_date_value, unit="D")
```

The sample CSVs already contain ISO-8601 date strings, so no epoch conversion
is needed for this migration.

---

## 3  Snowflake DDL Design Decisions

### 3.1  Schema & Naming

- All tables reside in `SAS_MIGRATION` schema for isolation.
- Column names match CSV headers in UPPER_SNAKE_CASE.
- The CSV column `date` in DAILY_BALANCE was renamed to `BALANCE_DATE` to avoid
  colliding with the Snowflake reserved word `DATE`.

### 3.2  Data Types

| CSV column | SAS type | Snowflake type | Rationale |
|---|---|---|---|
| `customer_id` | Numeric 8 | `INTEGER` | Always whole numbers (1001–1100) |
| `account_id` | Char $36 | `VARCHAR(36)` | UUID-style hex string |
| `account_type` | Char $20 | `VARCHAR(20)` + CHECK | Bounded domain: CHECKING/SAVINGS/CREDIT |
| `is_active` | Char $10 | `VARCHAR(10)` + CHECK | ACTIVE or INACTIVE |
| `start_date` / `end_date` | SAS date | `DATE` | ISO-8601 in CSV |
| `end_of_day_balance` | Numeric 8 | `DECIMAL(12,2)` | Two decimal places observed |
| `month` | Char $7 | `VARCHAR(7)` | YYYY-MM format string |
| `reporting_month_yyyymm` | Numeric 6 | `INTEGER` | Compact YYYYMM representation |
| `average_monthly_balance` | Numeric 8 | `DECIMAL(12,2)` | Monetary precision |
| `date_computed` | SAS date | `DATE` | Calculation timestamp |

### 3.3  Constraints

- **Primary keys** enforce row uniqueness (composite keys on all three tables).
- **Foreign keys** link DAILY_BALANCE and MONTHLY_AMB back to CUST_ACCOUNTS.
- **CHECK constraints** restrict `ACCOUNT_TYPE` and `IS_ACTIVE` to known domains.
- **Date ordering** constraint ensures `END_DATE >= START_DATE`.

> **Note:** Snowflake enforces `NOT NULL` but does *not* enforce PK uniqueness or
> FK referential integrity at write time (they are informational constraints for
> query optimisation).  The validation queries in `load_and_validate.sql`
> explicitly check uniqueness and referential integrity post-load.

### 3.4  Clustering Keys

| Table | Cluster key | Reason |
|---|---|---|
| CUST_ACCOUNTS | `(CUSTOMER_ID)` | Most queries filter by customer |
| DAILY_BALANCE | `(CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE)` | Range scans by date within an account |
| MONTHLY_AMB | `(CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM)` | Aggregation by account + month |

### 3.5  Stage & COPY INTO

- A named internal stage `SAS_STAGE` is defined with CSV-specific file format
  options.
- `NULL_IF = ('', 'NA', '.')` handles all common SAS missing-value markers.
- `ERROR_ON_COLUMN_COUNT_MISMATCH = TRUE` catches structural problems early.
- Column-order transformations (e.g. `date` → `BALANCE_DATE`) are handled via a
  sub-select in the `COPY INTO` command.

---

## 4  Validation Strategy

### Row Counts
Each table's expected count is hard-coded from the source CSV (`wc -l` minus
header):
- `CUST_ACCOUNTS`: 1,980 rows
- `DAILY_BALANCE`: 122,760 rows
- `MONTHLY_AMB`: 1,980 rows

### Checksums
`SUM(CUSTOMER_ID)` and `SUM(END_OF_DAY_BALANCE)` provide deterministic
aggregate checks that can be compared between the SAS source and Snowflake
target.

### Cross-Table Consistency
The AMB validation query recalculates the monthly average from DAILY_BALANCE
and compares it to the stored value in MONTHLY_AMB, with a tolerance of ±0.01
for floating-point rounding.

### Null & Uniqueness
Dedicated queries confirm no unexpected NULLs in NOT NULL columns and no
duplicate primary keys.

---

## 5  Files Produced

| File | Description |
|---|---|
| `python_migration/sas_transforms.py` | Python/pandas translations of all 8 SAS macros |
| `python_migration/tests/test_sas_transforms.py` | pytest suite with unit + integration tests |
| `snowflake_ddl/create_tables.sql` | Snowflake DDL (schema, tables, constraints, clustering) |
| `snowflake_ddl/load_and_validate.sql` | COPY INTO + validation queries |
| `SAS_MIGRATION_NOTES.md` | This document |
