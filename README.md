# Kaushal Parinam — Skill Outcomes

*
A consent-based longitudinal outcome tracking system that links every
certification to a verified employment record, grades every claim **E0–E4**, and
prints **reach beside every rate**. It is a layer on top of Skill India Digital
and Mahaswayam — it does not replace either.

> All demo data are synthetic. No real person appears anywhere. Nothing here is
> production, and every integration is labelled SANDBOX / MOCK / STUB /
> SIMULATED on screen.

---

## 1. Run it

### With Docker (PostgreSQL)

```bash
docker compose up --build
docker compose exec web python manage.py seed_demo --trainees 500
docker compose exec web python manage.py generate_stats
docker compose exec web python manage.py load_definitions
open http://localhost:8000
```

The container runs gunicorn, and its entrypoint applies migrations and collects
static files on start, so `up --build` is the only command you need. For a
production-shaped run — `DEBUG=False`, HTTPS-only cookies, HSTS, no bind mount —
use the overlay in `compose.production.yml`:

```bash
SECRET_KEY=... ALLOWED_HOSTS=kaushal.example.gov.in \
CSRF_TRUSTED_ORIGINS=https://kaushal.example.gov.in \
DATABASE_URL=postgres://... \
  docker compose -f docker-compose.yml -f compose.production.yml up -d --build
```

### Without Docker (local PostgreSQL, SQLite fallback)

```bash
pip install -r requirements.txt
cp .env.example .env          # then point DATABASE_URL at your database
python manage.py migrate
python manage.py seed_demo --trainees 500
python manage.py generate_stats
python manage.py load_definitions
python manage.py runserver
```

If `DATABASE_URL` is empty the app falls back to SQLite so it runs with zero
setup. SQLite has **no row-level security** and no `FOR UPDATE SKIP LOCKED`, so
use PostgreSQL for the RLS demo.

**PostgreSQL 15 or later is required by Django.** A local cluster is enough:

```bash
initdb -D .pgdata -U postgres --auth-local=trust
pg_ctl -D .pgdata -l .pglog/postgres.log -o "-p 5433 -k /tmp" start
createdb -h /tmp -p 5433 -U postgres kaushal_parinam
# then in .env: DATABASE_URL=postgres://postgres@127.0.0.1:5433/kaushal_parinam
```

### Demo logins

`python manage.py seed_demo` creates these. Password for all: `demo-pass-1234`.

| Username | Role | What they can see |
|---|---|---|
| `trainee` | Trainee | only their own record |
| `provider_a` | Provider (Centre-A) | Centre-A trainees only |
| `provider_b` | Provider (Centre-B) | Centre-B trainees only |
| `officer_mumbai` / `officer_pune` | District officer | their district only |
| `policy` | Policy officer (MSInS) | all districts, aggregated |
| `coordinator_a` | Provider coordinator | CSV upload for Centre-A |

---

## 2. Management commands

| Command | Feature | What it does |
|---|---|---|
| `seed_demo [--trainees N] [--reset]` | launch checklist | 500 synthetic trainees, 3 courses, 2 districts, 3 planted patterns |
| `seed_demo_data` | docs/05 §4 | the 6-row starter data set |
| `load_definitions` | F-03 | seeds the four registry rows |
| `generate_stats [--grades E0 E1 E2]` | F-09/F-10/F-11 | rebuilds the three `stats_*` tables |
| `run_followup_scheduler [--loop]` | F-04 | drains due jobs with `FOR UPDATE SKIP LOCKED`, walks the 48h/72h SMS/IVR ladder, escalates what goes unanswered |
| `draw_audit_sample` | F-08/F-12 | department-drawn random sample of non-repliers, claimed jobs and self-employment outcomes; stores `p` so the weight `1/p` is reproducible |
| `gatekeeper_check <csv>` / `--template` | F-02 | runs the four intake checks from the shell |

---

## 3. The eight-minute demo script

Cut order if short on time: **S2 → S6 → S5 → S8**. Never cut **F2**, **F7** or
**F8**.

1. **S1 — The gap.** CAG found only 41% of 56.14 lakh certified PMKVY candidates
   reported placed, no retention data at all, and forged placement papers in
   Kerala. Certification and employment are not connected anywhere.
2. **S2 — Enrol a trainee live.** `/enrol/`. Four consent purposes, each one
   separate; language; own phone; second contact; device type. The device type
   decides the channel — this trainee is on a feature phone, so the follow-up
   will be an IVR call. Show the UTID `KO-YYYYMMDD-NNNN` that gets generated.
3. **S3 — Gatekeeper.** Sign in as `coordinator_a`, upload a CSV with a
   duplicate phone, an under-age row, a bad date order and an unregistered
   centre. Four checks, four flags, the batch still uploads.
4. **S4 — The scheduler.** `python manage.py run_followup_scheduler`. It claims
   due jobs with `SKIP LOCKED` and routes them: WhatsApp form (SANDBOX), IVR
   stub, SMS stub. After three attempts it opens a task for the district
   officer.
5. **S5 — Grade it.** The trainee answers; the outcome lands at **E0**. Send the
   employer the one-tap link; the employer confirms in two taps with no login.
   The record becomes **E2** — and when the employer's wage differs, *both* values
   are kept, not averaged away.
6. **S6 — The honest rate.** Sign in as `policy`. Every rate carries its response
   rate, its grade mix and its 95% range. Switch the minimum grade from E0 to E2
   and watch the placement rate move; narrow to one district or course and watch
   the cohort composition change. That is the whole argument: the number a
   provider reports and the number an employer verified are different numbers.
7. **S7 — Fair peer comparison.** `/policy/providers/`. Centre-B looks like the
   worst provider on the raw rate — and the adjustment explains a large part of
   why, because it trains in a district with almost no vacancies. Centre-C's rate
   is high and its call-back sample mostly fails. Small providers show "data
   withheld". Say the residual gap out loud: the adjustment narrows the
   difference, it does not erase it.
8. **S8 — Skill gaps and the cutoff.** `/policy/skill-gaps/`. Mason in Pune is
   red: all three signals agree. Two of three is orange. One signal is never
   listed. Then `/policy/funding-report/`: only E2+ outcomes, never E0 or E1.

Integration badges are visible on every screen, so nobody has to ask whether a
number came from a live government system.

---

## 4. The three planted patterns

`seed_demo` deliberately plants these so the dashboards have something real to
show. The demo dataset is not a representative sample of Maharashtra, and the
skill-gap flag rate in it (4 of 6 course-district cells) is far above the
10–20% the PRD expects from a real pilot — the flags exist because they were
planted.

1. **Weak labour market** — Centre-B (Sahyadri Skills, Pune) trains to the same
   standard as Centre-A and is the lowest on the raw rate: ~37% against ~62%.
   In the built demo, fair-peer adjustment lifts its expected rate to ~46%,
   closing roughly half the gap and moving it from "far below peers" to "below
   peers". That residual is honest: a linear model fitted on a vacancy index
   cannot fully recover a multiplicative demand effect, so the adjustment narrows
   the difference rather than erasing it. It is not there to make any provider
   look good.
2. **Claims that fail verification** — Centre-C reports a high placement rate;
   only ~30% of its E2 outcomes survive the department's call-back sample,
   against ~85–95% for the other two centres.
3. **A specific skill gap** — Mason in Pune is red: all three signals agree
   (vacancies exist, employers report "skills lacking", trainees say the course
   did not match). Plumbing in Mumbai is orange at two of three. In the built
   demo 3 of 5 course-district cells are flagged, well above the 10–20% the PRD
   expects from a real pilot, because the flags were planted.

Local demand genuinely drives placement in the demo data (`seed_demo` multiplies
each centre's base rate by its vacancy index). Without that, "weak market" would
be a caption rather than a mechanism and the adjustment would have nothing to
correct for.

---

## 5. Project structure

```
kaushal_parinam/           repo root (the project itself)
├── config/                Django configuration only — no models
│   ├── settings.py        env-driven settings, installed apps, RLS/i18n/logging
│   ├── urls.py            root URLconf (docs/03-APP-FLOW.md §1 page list)
│   └── wsgi.py asgi.py    WSGI/ASGI entry points
│
├── core/                  shared domain layer (was "coreApp")
│   ├── models.py          every table from docs/05-BACKEND-SCHEMA.md §1.1
│   ├── services/          utid · rls · gatekeeper · followup · channel_router
│   │                      evidence · retention · weights · stats · otp
│   │                      employer_link · audit · identity · filters
│   ├── adapters/          channels (WhatsApp/SMS/IVR/officer stubs) and
│   │                      government systems (all MOCK/SIMULATED)
│   ├── management/commands/ seed_demo · seed_demo_data · load_definitions
│   │                      run_followup_scheduler · draw_audit_sample
│   │                      gatekeeper_check
│   ├── migrations/        0001_initial, 0002_rls_policies (PostgreSQL-only SQL),
│   │                      0003/0004 (choice widenings for F-08 / F-12 / F-03)
│   ├── middleware.py      session UTID, RLS session vars, audit log
│   ├── templatetags/      kp_tags.py
│   └── tests.py           49 end-to-end tests
│
├── accounts/              RBAC user: role, provider_id, district (the RLS scope)
├── trainees/              enrolment, consent passport, outcome capture, My Data, Help me
├── providers/             provider dashboard, employer one-tap confirm
├── officers/              assisted follow-up tasks, E1 recording, audit sample
├── policy/                MSInS dashboard, definitions registry, fair-peer
│                          comparison, skill-gap view, funding report,
│                          identity near-match queue (F-03)
├── analytics/             statistics batch helpers, data-quality score
│
├── templates/             base + partials; dashboard/_rate.html is the only
│                          place a percentage is rendered
├── static/                css/custom.css (design tokens), js/app.js (HTMX)
├── docs/                  PRD, TRD, APP-FLOW, UI-UX, BACKEND-SCHEMA, PLAN …
│                          (local only, excluded from the GitHub push)
├── manage.py
├── requirements.txt  requirements-prod.txt
├── Dockerfile  docker-compose.yml  compose.production.yml  .dockerignore
├── docker/entrypoint.sh   migrate + collectstatic, then exec gunicorn
├── docker/verify-rls.sql  asserts every RLS scope and every leak, as kp_app
└── README.md
```

**Conventions worth knowing:**

- `config/` holds settings and URLconf only. Models and views live in the app
  packages, so there is one obvious home for each kind of code.
- Every app package is lowercase and PEP 8. The former `coreApp` is now `core`.
- A service module owns one concern and is importable on its own; views stay thin
  and delegate to `core/services/`.
- No module reaches into another app's models for anything that belongs in a
  service.
- `django-admin makemigrations core` after any change to `core/models.py`.

**Two stores.** Identity tables (`person`, `contact`, `consent`, `enrolment`,
`outcome_event`, …) hold names and phones. Statistics tables (`stats_*`) hold
aggregates with no names at all. Dashboards query the statistics store.

**Append-only.** `OutcomeEvent` cannot be updated or deleted; the ORM raises
`ValidationError`. Current status = the latest valid event. The single sanctioned
exception is `OutcomeEvent.objects.anonymise()`, which sets only the
`is_anonymous` privacy flag when a trainee requests erasure.

**Row-level security.** `core/migrations/0002_rls_policies.py` installs
PostgreSQL policies driven by `app.user_role`, `app.utid`, `app.provider_id` and
`app.district`, set per request by `RLSMiddleware`. `core/services/rls.py`
applies the same scope in the ORM so behaviour is identical on SQLite and a
provider cannot widen their own view by hand-writing a query.

**The policies are correct, and they are not what currently protects the data.**
Run `psql -U kp_app -f docker/verify-rls.sql` to check them directly; the script
asserts every scope and every leak. Three defects were found and fixed while
verifying them (0005, 0006, 0007, 0008):

| Defect | Effect |
|---|---|
| `''` was in the privileged role list | a session with no role saw **every row**; now fails closed |
| `rls_write` was `FOR ALL`, so it governed SELECT too | permissive policies are OR-ed, so the write policy **overrode the read policy** and exposed all 501 rows; now split per command |
| session variables were set with `set_config(..., true)` under autocommit | they were discarded before the view ran, so every request read back an empty role; the transaction is now opened by `RLSMiddleware` |

Even with those fixed, the policies **cannot** be used to constrain this
application, and this is a design incompatibility rather than a bug:

* Django issues every insert as `INSERT ... RETURNING id`.
* PostgreSQL checks the rows returned by `RETURNING` against the **SELECT**
  policy.
* The scope helper is `STABLE` and reads the same table, so its snapshot cannot
  include the row being inserted.
* Result: the write is rejected. Marking the helper `VOLATILE` fixes the
  snapshot and immediately causes infinite recursion, because the helper reads
  the table whose policy calls it.

Verified directly: `INSERT` alone succeeds, `INSERT ... RETURNING id` fails with
`new row violates row-level security policy`. So isolation is enforced in
`core/services/rls.py`, applied to every view, and the database policies are
defence in depth that is verified but not load-bearing.

To make them load-bearing the shape has to change — read-only views the app role
can select from, with writes going through `SECURITY DEFINER` functions — which
is a much larger change than this project took on.

**Database roles.** `core/migrations/0002_rls_policies.py` creates `kp_app`
(`NOSUPERUSER NOBYPASSRLS`) for verification. The application itself connects as
`kp_worker` (`BYPASSRLS`), for the reason above; treat that as the price of
using Django's ORM and note it explicitly, because a role with `BYPASSRLS` has no
database-level protection at all.

Verify the policies yourself, as a role that does not bypass them:

```bash
psql -h 127.0.0.1 -p 5433 -U kp_app -d kaushal_parinam -f docker/verify-rls.sql
```

Two traps make such a check pass vacuously, and the script prints both so you
can see they are not tripped: a **superuser** ignores RLS, and so does the
**table's own owner**. A role that creates the test database owns its tables, so
"the suite passes as a non-superuser" is not evidence that anything was enforced.

**Checks that can fire.** Three metrics in this codebase are deliberately *not*
implemented, because a unique constraint makes the state they would look for
unreachable and they would silently report zero forever:

| Check | Why it is absent |
|---|---|
| "two UTIDs claim one external scheme ID" | `uniq_crosswalk_scheme_programme` forbids it, so the near-match queue detects shared phone and shared name+DOB instead |
| "duplicate schemes inside one crosswalk" | `uniq_crosswalk_person_scheme` forbids it; the S-05 penalty counts trainees with no crosswalk row, or an unresolved near match |
| third signal of the skill-gap rule | It is built, but only an employer confirmation can set it — see `skills_lacking` in `core/services/employer_link.py` |

**No queue server.** The scheduler is a PostgreSQL job table drained with
`SELECT ... FOR UPDATE SKIP LOCKED` (docs/02-TRD.md §1). Redis is not needed and
is not configured.

**No ML.** Rules plus a small least-squares adjustment for the fair-peer view
(`policy/comparison.py`), reproducible by hand. `statsmodels` is used when it is
installed and a ridge-stabilised numpy solve otherwise.

---

## 6. Tests

```bash
python manage.py test core            # 49 tests, all roles, end to end
python manage.py check --deploy
```

The suite covers the acceptance criteria that matter: UTID format, four consent
purposes, all four gatekeeper flags, the under-18 guardian gate, append-only
enforcement, employer one-tap raising E2 while keeping both wages, the
minimum-grade filter changing the rate, provider RLS isolation, officer E1
recording, cross-district denial, the 2-of-3 skill-gap rule, the funding report
excluding E0/E1, OTP-gated erasure, and the definitions registry changing the
break rule.

The cohort filters and the four regressions they closed are covered too, because
each of those was a check that existed but could never fire:

| Test | What it pins down |
|---|---|
| `test_audit_draw_stamps_sample_type` | A department draw stamps `sample_type`; both the non-response weighting and the funding report's audit columns filter on it |
| `test_non_replier_samples_change_the_weighted_rate` | Verified non-repliers move the estimate, and a published rate carries a range |
| `test_self_employment_field_verification_raises_to_e1` | F-12 E0 → E1 via a sampled field check, with the append-only outcome row left untouched |
| `test_near_match_detection_suggests_and_never_merges` | F-03 queues a pair with every signal that agreed, and merges nothing |

The row-level security policies are **not** covered by this suite, and cannot be.
Django's `INSERT ... RETURNING` is rejected by the policies, so the ORM cannot
write to those tables while they are enforced; verifying them is what
`docker/verify-rls.sql` is for. See section 5.

---



---

## 8. Known gap: Marathi and Hindi text

Django i18n is configured (`LANGUAGES`, `LOCALE_PATHS`, `Asia/Kolkata`) and the
consent passport offers a language selector, but **no translation catalogs are
compiled**. Until `locale/mr/LC_MESSAGES/django.po` and the `hi` equivalent
exist, every page renders in English regardless of the selector. There is
deliberately no half-empty `locale/` directory pretending otherwise.

What the selector already does:

- is stored on `contact.language` and on every `consent.language` row, so a
  consent record can prove which notice language the trainee actually saw;
- selects the recorded IVR prompt language (`core/adapters/channels.py` already
  carries Marathi and Hindi scripts for `IvrChannel.PROMPTS`);
- travels with the WhatsApp template context.

To finish it: wrap strings in `{% trans %}` / `{% blocktrans %}`, run
`python manage.py makemessages -l mr -l hi`, have a Marathi and a Hindi speaker
fill in the `.po` files, then `python manage.py compilemessages` (needs GNU
gettext). Suggested order: `trainees/enrolment.html` (consent purposes and the
DPDP age-gate notice), `trainees/outcome_form.html` (the seven statuses and
reason codes), `trainees/my_data.html`, then
`providers/employer_confirm.html` — the employer cannot be assumed to read
English and never logs in.

Machine translation is not acceptable for the consent wording: it is a
compliance artefact under DPDP s.6 and s.5(3).
