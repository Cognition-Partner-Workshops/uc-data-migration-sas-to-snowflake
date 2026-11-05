-- Set AMB_PROCEDURE to <database>.<target_schema>.SP_JOB03_CALC_AMB before deployment.
-- Set-based SQL; the procedure supplies replayable batch parameters to the Task.
CREATE OR REPLACE PROCEDURE IDENTIFIER($AMB_PROCEDURE)(
    SOURCE_NAMESPACE VARCHAR,
    TARGET_NAMESPACE VARCHAR,
    REPORTING_MONTH NUMBER,
    RUN_DATE DATE
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    accounts_table VARCHAR := SOURCE_NAMESPACE || '.CUST_ACCOUNTS';
    balances_table VARCHAR := SOURCE_NAMESPACE || '.DAILY_BALANCE';
    output_table VARCHAR := TARGET_NAMESPACE || '.MONTHLY_AMB';
    month_start DATE := TO_DATE(TO_VARCHAR(REPORTING_MONTH), 'YYYYMM');
    invalid_parameters EXCEPTION (-20001, 'Reporting month and run date are required');
BEGIN
    IF (REPORTING_MONTH IS NULL OR RUN_DATE IS NULL) THEN
        RAISE invalid_parameters;
    END IF;

    CREATE OR REPLACE TABLE IDENTIFIER(:output_table) AS
    SELECT
        c.CUSTOMER_ID,
        c.ACCOUNT_ID,
        :REPORTING_MONTH::NUMBER(6,0) AS REPORTING_MONTH_YYYYMM,
        ROUND(AVG(d.END_OF_DAY_BALANCE), 2) AS AVERAGE_MONTHLY_BALANCE,
        :RUN_DATE AS DATE_COMPUTED
    FROM IDENTIFIER(:accounts_table) c
    INNER JOIN IDENTIFIER(:balances_table) d
        ON c.CUSTOMER_ID = d.CUSTOMER_ID
       AND c.ACCOUNT_ID = d.ACCOUNT_ID
    -- The frozen legacy run includes July only and ALL account statuses.
    WHERE d.MONTH = :month_start
    GROUP BY c.CUSTOMER_ID, c.ACCOUNT_ID;

    RETURN output_table;
END;
$$;
