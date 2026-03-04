-- =============================================================================
-- dbt Model: stg_cust_accounts
-- SAS Job Equivalent: JOB01_LOAD_CUST_ACCOUNTS
-- Lineage: FINANCE_DB.RAW.CUST_ACCOUNTS -> FINANCE_DB.STAGING.STG_CUST_ACCOUNTS
--
-- Loads customer account records from the RAW schema into STAGING with
-- explicit type casting and data cleansing.
-- =============================================================================

{{
    config(
        materialized='table',
        schema='STAGING',
        alias='STG_CUST_ACCOUNTS'
    )
}}

WITH source AS (

    SELECT
        CUSTOMER_ID,
        ACCOUNT_ID,
        ACCOUNT_TYPE,
        IS_ACTIVE,
        START_DATE,
        END_DATE
    FROM {{ source('raw', 'CUST_ACCOUNTS') }}

),

cleaned AS (

    SELECT
        CUSTOMER_ID::INTEGER                        AS CUSTOMER_ID,
        TRIM(ACCOUNT_ID)::VARCHAR(8)                AS ACCOUNT_ID,
        UPPER(TRIM(ACCOUNT_TYPE))::VARCHAR(10)      AS ACCOUNT_TYPE,
        UPPER(TRIM(IS_ACTIVE))::VARCHAR(10)         AS IS_ACTIVE,
        START_DATE::DATE                             AS START_DATE,
        NULLIF(TRIM(END_DATE), '')::DATE             AS END_DATE
    FROM source

)

SELECT
    CUSTOMER_ID,
    ACCOUNT_ID,
    ACCOUNT_TYPE,
    IS_ACTIVE,
    START_DATE,
    END_DATE,
    CURRENT_TIMESTAMP()::TIMESTAMP_NTZ               AS LOADED_AT
FROM cleaned
