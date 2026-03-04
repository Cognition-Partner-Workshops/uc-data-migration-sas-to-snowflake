-- =============================================================================
-- dbt Model: stg_daily_balance
-- SAS Job Equivalent: JOB02_LOAD_DAILY_BALANCE
-- Lineage: FINANCE_DB.RAW.DAILY_BALANCE -> FINANCE_DB.STAGING.STG_DAILY_BALANCE
--
-- Loads daily balance records from the RAW schema into STAGING with
-- explicit type casting and data cleansing.
-- =============================================================================

{{
    config(
        materialized='table',
        schema='STAGING',
        alias='STG_DAILY_BALANCE'
    )
}}

WITH source AS (

    SELECT
        CUSTOMER_ID,
        ACCOUNT_ID,
        DATE,
        END_OF_DAY_BALANCE,
        MONTH
    FROM {{ source('raw', 'DAILY_BALANCE') }}

),

cleaned AS (

    SELECT
        CUSTOMER_ID::INTEGER                        AS CUSTOMER_ID,
        TRIM(ACCOUNT_ID)::VARCHAR(8)                AS ACCOUNT_ID,
        DATE::DATE                                   AS DATE,
        END_OF_DAY_BALANCE::NUMBER(12, 2)            AS END_OF_DAY_BALANCE,
        TRIM(MONTH)::VARCHAR(7)                      AS MONTH
    FROM source

)

SELECT
    CUSTOMER_ID,
    ACCOUNT_ID,
    DATE,
    END_OF_DAY_BALANCE,
    MONTH,
    CURRENT_TIMESTAMP()::TIMESTAMP_NTZ               AS LOADED_AT
FROM cleaned
