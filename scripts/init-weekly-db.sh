#!/bin/bash
set -e
# Creates the isolated weekly_reports database (runs once on fresh Postgres volume).
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    SELECT 'CREATE DATABASE weekly_reports'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'weekly_reports')\gexec
    GRANT ALL PRIVILEGES ON DATABASE weekly_reports TO $POSTGRES_USER;
EOSQL
