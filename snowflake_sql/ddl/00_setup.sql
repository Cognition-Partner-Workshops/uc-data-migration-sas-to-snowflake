/*=====================================================================
  00_setup.sql — SAS-to-Snowflake migration bootstrap

  Creates the dedicated migration database, the RAW and ANALYTICS schemas,
  a reusable CSV file format, and an internal stage for load-ready exports.
=====================================================================*/

CREATE DATABASE IF NOT EXISTS SAS_MIGRATION;
CREATE SCHEMA IF NOT EXISTS SAS_MIGRATION.RAW;
CREATE SCHEMA IF NOT EXISTS SAS_MIGRATION.ANALYTICS;

CREATE OR REPLACE FILE FORMAT SAS_MIGRATION.RAW.SAS_MIGRATION_CSV_FILE_FORMAT
  TYPE = CSV
  SKIP_HEADER = 1
  FIELD_OPTIONALLY_ENCLOSED_BY = '"'
  NULL_IF = ('', 'NULL')
  EMPTY_FIELD_AS_NULL = TRUE
  DATE_FORMAT = 'YYYY-MM-DD';

CREATE OR REPLACE STAGE SAS_MIGRATION.RAW.SAS_MIGRATION_STAGE
  FILE_FORMAT = SAS_MIGRATION.RAW.SAS_MIGRATION_CSV_FILE_FORMAT;
