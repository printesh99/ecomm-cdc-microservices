DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'debezium') THEN
    CREATE ROLE debezium WITH LOGIN PASSWORD 'dbz';
  END IF;
END $$;

ALTER ROLE debezium REPLICATION;
GRANT CONNECT ON DATABASE ecomm TO debezium;
