#!/bin/sh
# Runs once, when the production database volume is created for the first time.
# Same as postgres-init.sql, but with the app password from the environment and no test DB.
set -e

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -v app_password="$APP_DB_PASSWORD" <<'SQL'
-- The app connects as a normal role because superusers always skip row-level security.
CREATE ROLE app LOGIN PASSWORD :'app_password';
CREATE DATABASE datafog OWNER app;
\c datafog
CREATE EXTENSION IF NOT EXISTS vector;
SQL
