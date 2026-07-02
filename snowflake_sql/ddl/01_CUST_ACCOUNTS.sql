/*=====================================================================
  01_CUST_ACCOUNTS.sql — target table for the CUST_ACCOUNTS SAS sample

  Business rules:
    - account_type domain: CHECKING, CREDIT, SAVINGS
    - is_active domain: ACTIVE, INACTIVE
    - account_id is the primary key candidate; customer_id/account_id is
      also known to be unique in the sample data
=====================================================================*/

CREATE OR REPLACE TABLE SAS_MIGRATION.RAW.CUST_ACCOUNTS (
  CUSTOMER_ID NUMBER(10,0) NOT NULL COMMENT 'Customer identifier from SAS',
  ACCOUNT_ID VARCHAR(16) NOT NULL COMMENT '8-character hex account identifier',
  ACCOUNT_TYPE VARCHAR(20) NOT NULL COMMENT 'Intended domain: CHECKING, CREDIT, SAVINGS',
  IS_ACTIVE VARCHAR(10) NOT NULL COMMENT 'Intended domain: ACTIVE, INACTIVE',
  START_DATE DATE NOT NULL COMMENT 'Account start date',
  END_DATE DATE COMMENT 'Account end date; null when the account is active',
  CONSTRAINT PK_CUST_ACCOUNTS PRIMARY KEY (ACCOUNT_ID),
  CONSTRAINT UQ_CUST_ACCOUNTS_CUSTOMER_ACCOUNT UNIQUE (CUSTOMER_ID, ACCOUNT_ID)
)
COMMENT = 'SAS CUST_ACCOUNTS sample loaded into Snowflake RAW for migration validation'
CLUSTER BY (CUSTOMER_ID);
