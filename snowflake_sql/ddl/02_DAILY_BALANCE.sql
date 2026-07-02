/*=====================================================================
  02_DAILY_BALANCE.sql — target table for the DAILY_BALANCE SAS sample

  The source column named DATE is mapped to BALANCE_DATE to avoid reserved
  word collisions and to make explicit COPY INTO column mapping possible.
=====================================================================*/

CREATE OR REPLACE TABLE SAS_MIGRATION.RAW.DAILY_BALANCE (
  CUSTOMER_ID NUMBER(10,0) NOT NULL COMMENT 'Customer identifier from SAS',
  ACCOUNT_ID VARCHAR(16) NOT NULL COMMENT 'Account identifier from SAS',
  BALANCE_DATE DATE NOT NULL COMMENT 'Source DATE column mapped to a physical balance date',
  END_OF_DAY_BALANCE NUMBER(12,2) COMMENT 'End-of-day balance rounded to two decimal places',
  MONTH DATE COMMENT 'Normalized to the first day of the month',
  CONSTRAINT PK_DAILY_BALANCE PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE)
)
COMMENT = 'SAS DAILY_BALANCE sample loaded into Snowflake RAW for migration validation'
CLUSTER BY (MONTH, CUSTOMER_ID);
