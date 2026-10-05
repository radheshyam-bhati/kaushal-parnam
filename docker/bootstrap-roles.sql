-- Database roles for Kaushal Parinam.
--
-- Applied once against a fresh cluster, by an operator:
--     psql -h 127.0.0.1 -p 5433 -U postgres -d kaushal_parinam \
--         -f docker/bootstrap-roles.sql
--
-- core/migrations/0002_rls_policies.py also creates kp_app, so running this is
-- only necessary when you want the verification role before the migrations, or
-- when the migration role cannot create roles.
--
-- Read README section 5 before changing any of these. The application
-- deliberately connects as kp_worker, which has BYPASSRLS, because Django's
-- INSERT ... RETURNING is rejected by the policies.

-- Verification role. Must not own the tables it reads.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kp_app') THEN
        CREATE ROLE kp_app LOGIN PASSWORD 'kp_app'
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO kp_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO kp_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO kp_app;

-- Application and batch-worker role. BYPASSRLS on purpose, and that is the
-- documented cost: no database-level isolation. Never use this role for a
-- connection that a different party can influence.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kp_worker') THEN
        CREATE ROLE kp_worker LOGIN PASSWORD 'kp_worker'
            NOSUPERUSER BYPASSRLS CREATEDB;
    END IF;
END $$;
GRANT USAGE, CREATE ON SCHEMA public TO kp_worker;
GRANT ALL ON ALL TABLES IN SCHEMA public TO kp_worker;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO kp_worker;
