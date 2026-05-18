/*
  stg_cust_accounts.sql
  Migrated from: Programs/Banking/load_customer_accounts.sas (Step 1)

  SAS Original:
    PROC SQL extracting from ORA_DW.CUST_ACCOUNTS joined to ORA_DW.CUST_DEMOGRAPHICS

  dbt Equivalent:
    Staging model reading from Databricks external table (Unity Catalog)
    replaces the LIBNAME ORA_DW Oracle connection + PROC SQL extract
*/

with source as (
    select * from {{ source('banking_raw', 'cust_accounts') }}
),

demographics as (
    select * from {{ source('banking_raw', 'cust_demographics') }}
),

joined as (
    select
        a.account_id,
        a.customer_id,
        a.account_type,
        a.account_status,
        a.open_date,
        a.close_date,
        a.current_balance,
        a.available_balance,
        a.credit_limit,
        a.interest_rate,
        a.branch_id,
        a.officer_id,
        a.last_activity_date,
        d.first_name,
        d.last_name,
        d.ssn_hash,
        d.date_of_birth,
        d.customer_segment,
        d.risk_rating,
        d.region_code,
        d.primary_email,
        d.phone_number
    from source a
    inner join demographics d
        on a.customer_id = d.customer_id
    where a.account_status not in ('W', 'C')
      and a.open_date <= current_date()
)

select * from joined
