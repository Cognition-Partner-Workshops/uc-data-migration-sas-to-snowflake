# JOB06 — `Programs/Insurance/policy_valuation.sas` → `snowflake_sql/JOB06_POLICY_VALUATION.sql`

Source of truth: `ts-sas-legacy-analytics/Programs/Insurance/policy_valuation.sas`
(plus `Macro/parmv.sas`, `Macro/nobs.sas`, `Formats/insurance_formats.sas`,
`BatchJobs/run_daily_insurance.sas`, `Config/autoexec_local.sas`).
Business date: `CURR_DT=31JAN2024`.

## Target construct: Snowflake SQL

| Step in SAS | Shape | Verdict |
|---|---|---|
| 1–3 `proc sql; create table WORK.*` | filter + aggregate | set-based |
| 4 `data STG_INS.POLICY_VALUATION; merge … by POLICY_ID; if a;` | one-to-one MERGE (both right-hand inputs are `GROUP BY` results), no `RETAIN`, no `first./last.`, no multi-`output`, no hash objects | = `LEFT JOIN` + scalar `CASE` columns |
| 5 `proc means … class POLICY_TYPE nway; output out=…` | aggregate by class | = `GROUP BY` |
| 5b `data REPORTS.LOSS_RATIO_SUMMARY; set …;` | one row in → one row out, two derived columns | set-based |

Nothing procedural remains, so Snowflake SQL is the faithful and simplest target;
Snowpark would only wrap the same SQL. (The inventory proposal said the same.)

## Inputs / outputs — header vs body

| Header says | Body does | Action |
|---|---|---|
| Inputs `RAW_INS.POLICIES, RAW_INS.CLAIMS, RAW_INS.PREMIUMS` | read at :62, :92, :113 | converted |
| Input `TERA_DW.ACTUARIAL_TABLES` | **never referenced** | nothing to read — reported |
| Output `STG_INS.POLICY_VALUATION` | created :123 | converted |
| Output `REPORTS.LOSS_RATIO_SUMMARY` | created :173, rewritten :184 | converted |
| Output `REPORTS.RESERVE_ADEQUACY` | **never created** | nothing to produce — reported |
| Schedule "Monthly 5th business day, Control-M INS_MONTHLY_01" | `BatchJobs/run_daily_insurance.sas:112` runs it as **step 2 of the daily 07:00 INS_MASTER chain** after `claims_processing.sas` | reported; orchestration owned by the integration child |

## Construct mapping

SAS = `Programs/Insurance/policy_valuation.sas`; SQL = `snowflake_sql/JOB06_POLICY_VALUATION.sql`.

| SAS construct | SAS lines | Snowflake equivalent | SQL lines |
|---|---|---|---|
| `%policy_valuation(val_date=&CURR_DT, lob=ALL)` + `%parmv` validation | 17–20, 206 | session variables `$val_date`, `$lob` (runner validates `lob` against the `%parmv` list) | 55–58 |
| `%let val_ym = putn(val_date, yymmn6.)` (used only in the dataset label) | 23 | not reproduced (metadata only, see quirk Q12) | — |
| `intnx('year', val_date, 0, 'B')` | 59, 114 | `SET ytd_start = DATE_TRUNC('year', $val_date)` | 60 |
| `intnx('month', val_date, 3)` | 52 | `SET renewal_cutoff = DATE_TRUNC('month', DATEADD(month, 3, $val_date))` | 61 |
| `intnx('month', val_date, -12)` | 93 | `SET claims_from = DATE_TRUNC('month', DATEADD(month, -12, $val_date))` | 62 |
| `create table WORK.INFORCE as select … order by POLICY_ID` | 34–71 | `CREATE OR REPLACE TEMPORARY TABLE WORK_INFORCE … ORDER BY` | 67–105 |
| `intck('month', EFFECTIVE_DATE, val_date)` | 49 | `DATEDIFF('month', EFFECTIVE_DATE, $val_date)` (both count month boundaries) | 81 |
| `intck('month', val_date, EXPIRATION_DATE)` | 50 | `DATEDIFF('month', $val_date, EXPIRATION_DATE)` | 82 |
| `case when EXPIRATION_DATE <= intnx(...) then 'Y' else 'N' end … length=1` | 51–55 | `CASE WHEN EXPIRATION_DATE <= $renewal_cutoff THEN 'Y' ELSE 'N' END` | 83–86 |
| `ANNUAL_PREMIUM / 12 * min(12, intck('month', max(EFFECTIVE_DATE, yr_start), min(val_date, EXPIRATION_DATE)))` — PROC SQL `max`/`min` with 2 args are the SAS functions and **ignore missing** | 57–60 | `ANNUAL_PREMIUM / 12 * LEAST(12, DATEDIFF('month', GREATEST_IGNORE_NULLS(EFFECTIVE_DATE, $ytd_start), LEAST_IGNORE_NULLS($val_date, EXPIRATION_DATE)))` | 91–95 |
| `where STATUS = 'ACTIVE'` | 63 | `WHERE p.STATUS = 'ACTIVE'` | 97 |
| `and EFFECTIVE_DATE <= val_date` — TRUE for a **missing** date in SAS | 64 | `AND (EFFECTIVE_DATE <= $val_date OR EFFECTIVE_DATE IS NULL)` | 99 |
| `and EXPIRATION_DATE >= val_date` — FALSE for missing in SAS and NULL in SQL | 65 | `AND EXPIRATION_DATE >= $val_date` | 102 |
| `%if &lob ne ALL %then and POLICY_TYPE = "&lob"` | 66–68 | `AND ($lob = 'ALL' OR POLICY_TYPE = $lob)` | 104 |
| `create table WORK.CLAIMS_EXP … group by POLICY_ID` | 78–97 | `CREATE OR REPLACE TEMPORARY TABLE WORK_CLAIMS_EXP … GROUP BY` | 112–127 |
| `count(distinct CLAIM_ID)` | 82 | `COUNT(DISTINCT CLAIM_ID)` | 115 |
| `sum(INCURRED_AMOUNT)`, `sum(PAID_AMOUNT)`, `sum(RESERVED_AMOUNT)`, `max(LOSS_DATE)` | 83–86 | `SUM`, `SUM`, `SUM`, `MAX` (all skip NULL; all-NULL group → NULL, as SAS) | 116–119 |
| `sum(case when CLAIM_STATUS in ('OPEN','INV','ADJ','PEND') then RESERVED_AMOUNT else 0 end)` | 87–89 | identical `SUM(CASE …)` | 120–121 |
| `sum(case when CLAIM_STATUS = 'DENY' then 1 else 0 end)` | 90–91 | identical | 122–123 |
| `where LOSS_DATE >= intnx('month', val_date, -12) and LOSS_DATE <= val_date` | 93–94 | `WHERE LOSS_DATE >= $claims_from AND LOSS_DATE <= $val_date` | 125–126 |
| `create table WORK.PREMIUM_COLL … group by POLICY_ID` | 102–118 | `CREATE OR REPLACE TEMPORARY TABLE WORK_PREMIUM_COLL … GROUP BY` | 132–143 |
| `sum(PREMIUM_AMOUNT)`; `sum(case when PAYMENT_STATUS='RETURNED' then PREMIUM_AMOUNT else 0 end)`; `max(PAYMENT_DATE)` | 106–110 | `SUM`, `SUM(CASE …)`, `MAX` | 135–138 |
| `count(case when PAYMENT_STATUS = 'LATE' then 1 end)` (COUNT of non-missing) | 111–112 | `COUNT(CASE WHEN PAYMENT_STATUS = 'LATE' THEN 1 END)` | 139 |
| `where PAYMENT_DATE >= intnx('year', val_date, 0, 'B') and PAYMENT_DATE <= val_date` | 114–115 | `WHERE PAYMENT_DATE >= $ytd_start AND PAYMENT_DATE <= $val_date` | 141–142 |
| `merge INFORCE(in=a) CLAIMS_EXP(in=b) PREMIUM_COLL(in=c); by POLICY_ID; if a;` | 124–129 | `FROM WORK_INFORCE a LEFT JOIN WORK_CLAIMS_EXP b … LEFT JOIN WORK_PREMIUM_COLL c …` (right sides are unique per key, so MERGE ≡ LEFT JOIN; INFORCE duplicates stay duplicated as in SAS) | 154–187 |
| `format POLICY_TYPE $POLTYPE. RISK_CATEGORY $RISKCAT.` | 131–133 | presentation-layer; raw codes stored (quirk Q10) | — |
| `if YTD_EARNED_PREMIUM > 0 then LOSS_RATIO = coalesce(TOTAL_INCURRED,0)/YTD_EARNED_PREMIUM; else .;` | 136–139 | `CASE WHEN YTD_EARNED_PREMIUM > 0 THEN COALESCE(TOTAL_INCURRED,0)/YTD_EARNED_PREMIUM END` | 191–194 |
| `if YTD_EARNED_PREMIUM > 0 then COMBINED_RATIO = LOSS_RATIO + 0.30; else .;` | 143–146 | `CASE WHEN YTD_EARNED_PREMIUM > 0 THEN LOSS_RATIO + 0.30 END` | 200–203 |
| `if COMBINED_RATIO = . then 'N'; else if COMBINED_RATIO > 1.0 then 'N'; else 'Y';` | 150–152 | `CASE WHEN COMBINED_RATIO IS NULL THEN 'N' WHEN COMBINED_RATIO > 1.0 THEN 'N' ELSE 'Y' END` | 209–213 |
| `IBNR_ESTIMATE = max(0, YTD_EARNED_PREMIUM*0.15 - coalesce(TOTAL_PAID,0));` — DATA-step `max` ignores missing | 155 | `GREATEST_IGNORE_NULLS(0, YTD_EARNED_PREMIUM*0.15 - COALESCE(TOTAL_PAID,0))` | 216–217 |
| `TOTAL_RESERVE = coalesce(OPEN_RESERVES,0) + IBNR_ESTIMATE;` | 159 | `COALESCE(OPEN_RESERVES,0) + GREATEST_IGNORE_NULLS(…)` | 219–221 |
| `VALUATION_DATE = "&val_date"d;` | 162 | `$val_date AS VALUATION_DATE` | 222 |
| `format … dollar18.2 / percent8.2 / date9.` | 61,83–89,106–110,140,147,156,160,163 | presentation-layer; raw values stored (quirk Q10) | — |
| `proc means noprint nway; class POLICY_TYPE; var …; output out=…(drop=_TYPE_ _FREQ_) n=N_POLICIES sum(…)=…` | 169–181 | `GROUP BY POLICY_TYPE` with `COUNT(YTD_EARNED_PREMIUM)` (N of the **first** VAR variable) and `SUM(...)`; rows with missing class dropped by `WHERE POLICY_TYPE IS NOT NULL AND POLICY_TYPE <> ''` | 237–252 |
| `if TOTAL_EARNED > 0 then do; AGG_LOSS_RATIO = TOTAL_INCURRED/TOTAL_EARNED; AGG_COMBINED_RATIO = AGG_LOSS_RATIO + 0.30; end;` | 186–189 | two `CASE WHEN TOTAL_EARNED > 0 THEN …` columns | 260–261 |
| `%nobs(...)` / `%put NOTE:` | 73, 195–198 | logging only; nothing to convert | — |
| `proc datasets lib=WORK nolist; delete INFORCE CLAIMS_EXP PREMIUM_COLL;` | 200–202 | `DROP TABLE IF EXISTS WORK_*` | 266–268 |

## Reproduced legacy quirks (flagged, NOT fixed)

| # | Quirk | Where |
|---|---|---|
| Q1 | **At `CURR_DT=31JAN2024` every in-force policy gets `YTD_EARNED_PREMIUM = 0`**: `intck('month', max(EFFECTIVE_DATE, 01JAN2024), 31JAN2024)` is 0 for any effective date in or before January 2024. Consequently `LOSS_RATIO`/`COMBINED_RATIO` are missing for every policy, `PREMIUM_ADEQUATE = 'N'` for the whole book, `IBNR_ESTIMATE = 0` everywhere and `TOTAL_RESERVE = OPEN_RESERVES`. The January valuation is structurally degenerate; the formula earns a whole month only once a month boundary has passed. | SAS :57–60, :136–160 |
| Q2 | **Missing `EFFECTIVE_DATE` passes the in-force filter** (`. <= date` is TRUE in SAS); such a policy is valued with `POLICY_AGE_MONTHS` missing and earned premium measured from the year start (`max()` ignores the missing date). Reproduced with `OR EFFECTIVE_DATE IS NULL` and `GREATEST_IGNORE_NULLS`. | SAS :59, :64 |
| Q3 | **"12-month" claims window is 13 calendar months**: `intnx('month', val_date, -12)` is the *first* day of the month 12 months back (01JAN2023 for 31JAN2024), so Jan-2023 losses are included. | SAS :76, :93 |
| Q4 | **Duplicate claim rows double the money but not the count**: `count(distinct CLAIM_ID)` de-duplicates, `sum()` does not — a claim present twice counts once in `NUM_CLAIMS` and twice in `TOTAL_INCURRED/PAID/RESERVED/OPEN_RESERVES`. | SAS :82–89 |
| Q5 | **Returned premiums are not netted out of `COLLECTED_PREMIUM`** — `sum(PREMIUM_AMOUNT)` includes `RETURNED` rows; `RETURNED_PREMIUM` is reported alongside but never subtracted. | SAS :106–109 |
| Q6 | **`N_POLICIES` is not a policy count**: `n=N_POLICIES` applies to the first VAR variable only, so it is the number of policies with a **non-missing `YTD_EARNED_PREMIUM`**; a policy with missing `ANNUAL_PREMIUM` is in `POLICY_VALUATION` but contributes 0 to `N_POLICIES` and nothing to `TOTAL_EARNED` (NULL when it is alone in its class). | SAS :171–175 |
| Q7 | **Policies with a blank `POLICY_TYPE` vanish from `LOSS_RATIO_SUMMARY`** (PROC MEANS drops missing CLASS values by default) while they stay in `POLICY_VALUATION` — the two outputs do not tie out. | SAS :170 |
| Q8 | **Missing `ANNUAL_PREMIUM`** → `YTD_EARNED_PREMIUM` missing → `IBNR_ESTIMATE = 0` (not missing) because DATA-step `max(0, .)` ignores the missing argument; `TOTAL_RESERVE` is therefore defined. `GREATEST_IGNORE_NULLS` reproduces it (plain `GREATEST` would yield NULL). | SAS :155, :159 |
| Q9 | **`OPEN_RESERVES` silently ignores open claims with a missing `RESERVED_AMOUNT`** (`sum` skips the missing contribution); `TOTAL_RESERVED` likewise. | SAS :85–88 |
| Q10 | **Formats are display-only**: `$POLTYPE.`, `$RISKCAT.` (`OTHER='Unrated'`), `dollar18.2`, `percent8.2`, `date9.` change how SAS prints/exports values, not the stored values. Raw codes/numbers are stored in Snowflake. Note a PROC EXPORT of the SAS tables would emit the formatted text (`'Auto Insurance'`, `'$3,900.00'`), and PROC MEANS groups CLASS values by *formatted* value — harmless here because `$POLTYPE.` is one-to-one and has no `OTHER` bucket. | SAS :131–133, :61, :140… |
| Q11 | **Boundary inclusivity**: a policy expiring *on* the valuation date is in force (`MONTHS_TO_EXPIRY = 0`, `RENEWAL_DUE_FLAG = 'Y'`); losses/payments *on* the valuation date are counted; a policy effective in the future or expired the day before is out. | SAS :63–65, :93–94, :114–115 |
| Q12 | **Dataset label `"Monthly Policy Valuation &val_ym"`** is SAS metadata; Snowflake `COMMENT` cannot take a session variable, so it is not reproduced (data unaffected). | SAS :123 |
| Q13 | **Header/body drift**: `TERA_DW.ACTUARIAL_TABLES` is declared but never read; `REPORTS.RESERVE_ADEQUACY` is declared but never produced; the header schedule (monthly, INS_MONTHLY_01) contradicts the batch (`run_daily_insurance.sas` step 2, daily). | SAS :6–10; batch :112 |
| Q14 | **`LOB` macro parameter** is validated by `%parmv` against `ALL AUTO HOME WL TL UL HLTH` only; `VL RENT UMBR DNTL VIS DISAB` (all valid `$POLTYPE.` codes) cannot be valued singly. The batch always passes `lob=ALL`. | SAS :20, :206 |
| Q15 | **`MERGE … if a;` keeps duplicate `POLICY_ID`s** from `RAW_INS.POLICIES` (each duplicate receives the same claims/premium aggregates). The LEFT JOIN behaves identically; no `Uniqueness` rule is asserted on `POLICY_VALUATION.POLICY_ID` because SAS does not guarantee it. | SAS :124–129 |

## Typing assumptions

SAS stores every numeric as an 8-byte double and dates as numeric days. The
synthetic `RAW_INS` tables are therefore created with `FLOAT` for amounts and
`DATE` for dates (`sample_data/Scenario_policy_valuation/run_local.py`,
`INPUT_DDL`). If production `RAW_INS` lands as `NUMBER(p,s)`, the arithmetic in
`YTD_EARNED_PREMIUM` and the ratios will be exact-decimal rather than IEEE
double; differences are below the 0.01 reconciliation tolerance but not
bit-identical. SAS character comparisons ignore trailing blanks; the loader must
not pad `STATUS`, `CLAIM_STATUS`, `PAYMENT_STATUS`.

## How the Snowflake-side CSVs were produced

Executed on Snowflake (account from `SNOWFLAKE_ACCOUNT`, PAT auth, warehouse
`COMPUTE_WH`) in a dedicated sandbox database `SAS_MIG_JOB06_PV` whose schemas
`RAW_INS` / `STG_INS` / `REPORTS` mirror the SAS librefs, so the `.sql` ran
unchanged. `run_local.py` loads `baseline_inputs/*.csv`, prepends
`SET val_date = '2024-01-31'::DATE; SET lob = 'ALL';`, executes the file
statement by statement and exports `STG_INS__POLICY_VALUATION.csv` and
`REPORTS__LOSS_RATIO_SUMMARY.csv` (+ `run_manifest.json`). A DuckDB fallback
(`--engine duckdb`, five regex shims: `SET`→`SET VARIABLE`, `$var`→`getvariable`,
`DATEADD(month,n,d)`→`d + INTERVAL n MONTH`, `*_IGNORE_NULLS`→`greatest/least`)
produced byte-identical CSVs and is kept for credential-less reproduction.

## Reconciliation

No golden exists for this program (the Docker runner covers banking only), so the
baseline is synthetic and hand-derived: see
`sample_data/Scenario_policy_valuation/BASELINE_ASSUMPTIONS.md`.

- `sample_data/Scenario_policy_valuation/reconciliation.md` — 42/42 PASS at
  `CURR_DT=31JAN2024` (the required business date; exercises Q1–Q11 but, because
  of Q1, every ratio is missing).
- `sample_data/Scenario_policy_valuation/logic_check_30JUN2024/reconciliation.md`
  — 42/42 PASS with the same inputs at `val_date=30JUN2024`, added only so the
  non-zero branches (`LOSS_RATIO`, `COMBINED_RATIO > 1.0`, `PREMIUM_ADEQUATE='Y'`,
  `IBNR_ESTIMATE > 0`, `AGG_*` ratios) are proven too.

## Open questions

1. Is the January-degenerate earned-premium formula (Q1) intended, or should the
   business want month-of-valuation accrual? Not changed here.
2. Should missing `EFFECTIVE_DATE` policies really be valued (Q2)?
3. Which schedule is authoritative — header (monthly, 5th business day) or
   `run_daily_insurance.sas` (daily 07:00 after claims)? The Task definition in
   `orchestration/tasks.sql` belongs to the integration child.
4. `REPORTS.RESERVE_ADEQUACY` and `TERA_DW.ACTUARIAL_TABLES` appear in the header
   only — dead documentation or a lost code path?
5. No real `RAW_INS` seed exists anywhere in the estate; parity against production
   data still has to be run once a real extract is available (the harness and
   `validations.csv` are ready for it).
