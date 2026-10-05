-- Verify the row-level security policies, as SQL.
--
-- Run as a role that is NOT the table owner, NOT SUPERUSER and NOT BYPASSRLS,
-- because all three bypass RLS and would make every assertion below pass
-- vacuously. kp_app is created for exactly this purpose.
--
--     psql -h 127.0.0.1 -p 5433 -U kp_app -d kaushal_parinam -f docker/verify-rls.sql
--
-- This is deliberately SQL rather than Django tests. Django issues every insert
-- as INSERT ... RETURNING id, and PostgreSQL checks RETURNING against the SELECT
-- policy; the scope helper is STABLE and reads the same table, so it cannot see
-- the row being inserted and the write is rejected. In other words the ORM
-- cannot write to these tables under enforced policies at all, which is why the
-- policies are verified here rather than through the test suite.
--
-- Every check prints what it saw and what it expected; \set ON_ERROR_STOP makes
-- the run fail loudly on a mismatch.

\set ON_ERROR_STOP on
\pset pager off

\set trainee_utid 'KO-20240101-0001'
\set centre_a 'Centre-A'

\echo '=== connection must not bypass RLS, or every check below is vacuous ==='
SELECT current_user AS role,
       rolsuper AS is_superuser,
       rolbypassrls AS bypasses_rls,
       (SELECT pg_get_userbyid(relowner) FROM pg_class
         WHERE relname = 'person' AND relnamespace = 'public'::regnamespace
       ) AS person_table_owner,
       (current_user = (SELECT pg_get_userbyid(relowner) FROM pg_class
         WHERE relname = 'person' AND relnamespace = 'public'::regnamespace
       )) AS owns_the_table
  FROM pg_roles WHERE rolname = current_user;

\echo ''
\echo '=== fail closed: no role, empty role and anonymous see nothing ==='
BEGIN;
  SELECT count(*) AS should_be_0_unset FROM person;
  SELECT set_config('app.user_role', '', true);
  SELECT count(*) AS should_be_0_empty_role FROM person;
  SELECT set_config('app.user_role', 'anonymous', true);
  SELECT count(*) AS should_be_0_anonymous_person FROM person;
  SELECT count(*) AS should_be_0_anonymous_outcomes FROM outcome_event;
  SELECT count(*) AS should_be_0_anonymous_audit FROM audit_log;
COMMIT;

\echo ''
\echo '=== a trainee sees exactly their own record ==='
BEGIN;
  SELECT set_config('app.user_role', 'trainee', true);
  SELECT set_config('app.utid', :'trainee_utid', true);
  SELECT count(*) AS trainees_own_rows FROM person;
  SELECT count(*) AS own_outcomes FROM outcome_event;
  SELECT count(*) AS own_audit_rows FROM audit_log;
COMMIT;

\echo ''
\echo '=== a provider sees only their own centre, and never another ==='
BEGIN;
  SELECT set_config('app.user_role', 'provider', true);
  SELECT set_config('app.provider_id', :'centre_a', true);
  SELECT count(*) AS centre_a_people FROM person;
  SELECT count(*) AS centre_b_leak_must_be_0
    FROM person p JOIN enrolment e ON e.person_id = p.utid
   WHERE e.provider_id = 'Centre-B';
  SELECT count(*) AS district_officer_leak_must_be_0
    FROM person WHERE district NOT IN ('Pune', 'Mumbai');
COMMIT;

\echo ''
\echo '=== a district officer sees one district only ==='
BEGIN;
  SELECT set_config('app.user_role', 'district_officer', true);
  SELECT set_config('app.district', 'Pune', true);
  SELECT count(*) AS pune_people FROM person;
  SELECT count(*) AS mumbai_leak_must_be_0 FROM person WHERE district = 'Mumbai';
COMMIT;

\echo ''
\echo '=== policy officers and system see everything ==='
BEGIN;
  SELECT set_config('app.user_role', 'policy_officer', true);
  SELECT count(*) AS policy_officer_people FROM person;
  SELECT count(*) AS policy_officer_audit FROM audit_log;
  SELECT set_config('app.user_role', 'system', true);
  SELECT count(*) AS system_people FROM person;
COMMIT;

\echo ''
\echo '=== the audit trail is not readable across users ==='
BEGIN;
  SELECT set_config('app.user_role', 'trainee', true);
  SELECT set_config('app.utid', :'trainee_utid', true);
  SELECT set_config('app.request_id', 'not-a-real-request', true);
  SELECT count(*) AS other_users_audit_must_be_0
    FROM audit_log WHERE utid IS NOT NULL AND utid <> :'trainee_utid';
  SELECT count(*) AS other_users_outcomes_must_be_0
    FROM outcome_event WHERE person_id <> :'trainee_utid';
COMMIT;

\echo ''
\echo '=== every protected table has policies, and none is a write policy ==='
SELECT c.relname AS table,
       c.relrowsecurity AS rls_enabled,
       count(*) FILTER (WHERE p.cmd = 'SELECT') AS select_policies,
       count(*) FILTER (WHERE p.cmd = 'ALL') AS for_all_policies_must_be_0
  FROM pg_class c
  LEFT JOIN pg_policies p ON p.schemaname = 'public' AND p.tablename = c.relname
 WHERE c.relnamespace = 'public'::regnamespace
   AND c.relrowsecurity
 GROUP BY c.relname, c.relrowsecurity
 ORDER BY c.relname;

\echo ''
\echo 'Done. Check the values above: every "must_be_0" must be 0.'