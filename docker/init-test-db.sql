-- Runs once, only when the postgres_data volume is initialized from
-- scratch (docker-entrypoint-initdb.d scripts don't re-run against an
-- existing volume). Keeps the integration test suite's data fully
-- separate from the events database used for local dev/manual testing.
CREATE DATABASE events_test;
