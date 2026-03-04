-- =============================================================================
-- Snowflake DDL: Create Database and Schemas
-- Migrated from SAS Finance_DB lineage
-- =============================================================================

CREATE DATABASE IF NOT EXISTS FINANCE_DB;

USE DATABASE FINANCE_DB;

-- RAW schema: landing zone for source data ingested from SAS
CREATE SCHEMA IF NOT EXISTS FINANCE_DB.RAW;

-- STAGING schema: cleansed and transformed data ready for analytics
CREATE SCHEMA IF NOT EXISTS FINANCE_DB.STAGING;
