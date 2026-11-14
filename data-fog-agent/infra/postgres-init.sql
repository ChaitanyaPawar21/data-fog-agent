-- Runs once, when the Postgres container is created for the first time.
-- The app connects as a normal role because superusers always skip row-level security.
CREATE ROLE app LOGIN PASSWORD 'app';
CREATE DATABASE datafog OWNER app;
CREATE DATABASE datafog_test OWNER app;

-- Creating the vector extension needs a superuser, so do it here instead of in a migration.
\c datafog
CREATE EXTENSION IF NOT EXISTS vector;
\c datafog_test
CREATE EXTENSION IF NOT EXISTS vector;
