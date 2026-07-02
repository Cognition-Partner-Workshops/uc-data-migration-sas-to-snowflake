/*=====================================================================
  03_MONTHLY_AMB.sql — target table for the MONTHLY_AMB SAS sample
=====================================================================*/

CREATE OR REPLACE TABLE SAS_MIGRATION.RAW.MONTHLY_AMB (
  CUSTOMER_ID NUMBER(10,0) NOT NULL COMMENT 'Customer identifier from SAS',
  ACCOUNT_ID VARCHAR(16) NOT NULL COMMENT 'Account identifier from SAS',
  REPORTING_MONTH_YYYYMM NUMBER(6,0) NOT NULL COMMENT 'Reporting month in YYYYMM format',
  AVERAGE_MONTHLY_BALANCE NUMBER(12,2) COMMENT 'Average monthly balance rounded to two decimal places',
  DATE_COMPUTED DATE COMMENT 'Date the monthly metric was computed',
  CONSTRAINT PK_MONTHLY_AMB PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM)
)
COMMENT = 'SAS MONTHLY_AMB sample loaded into Snowflake RAW for migration validation'
CLUSTER BY (REPORTING_MONTH_YYYYMM);
