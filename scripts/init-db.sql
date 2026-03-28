-- PostgreSQL initialisation script
-- Runs once when the DB container is first created.

-- Enable trigram extension for full-text search (used by product name index)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Enable UUID generation functions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Application-level read-only role (useful for analytics / reporting queries)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ecommerce_readonly') THEN
        CREATE ROLE ecommerce_readonly;
    END IF;
END
$$;
