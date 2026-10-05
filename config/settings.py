"""Django settings for the Kaushal Parinam project (SIH26135).

Environment variables follow docs/02-TRD.md §9 and docs/05-BACKEND-SCHEMA.md §1.
Database is PostgreSQL (Row-Level Security, FOR UPDATE SKIP LOCKED); SQLite is a
zero-setup fallback for local development only.
"""

import sys

from core.env import (
    BASE_DIR,
    database_config,
    env,
    env_bool,
    env_int,
    env_list,
    load_dotenv,
)

load_dotenv()

#: True while `manage.py test` is running. Django offers no supported flag for
#: this, and it matters for exactly one decision: which staticfiles storage is
#: active. See the STORAGES block below.
_TESTING = 'test' in sys.argv

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", "django-insecure-dev-key-do-not-use-in-production")
DEBUG = env_bool("DEBUG", True)

# Automatic Host & Origin detection for local dev, Render, and Vercel
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME")
VERCEL_URL = env("VERCEL_URL")

default_allowed = ["localhost", "127.0.0.1", "[::1]", "testserver", ".onrender.com", ".vercel.app"]
if RENDER_EXTERNAL_HOSTNAME:
    default_allowed.append(RENDER_EXTERNAL_HOSTNAME)
if VERCEL_URL:
    default_allowed.append(VERCEL_URL)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", ",".join(default_allowed))
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
if ".onrender.com" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(".onrender.com")
if ".vercel.app" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(".vercel.app")

INTERNAL_UTID_PREFIX = env("INTERNAL_UTID_PREFIX", "KO")
MIN_GROUP_SIZE = env_int("MIN_GROUP_SIZE", 5)
SKILL_GAP_THRESHOLD = env_int("SKILL_GAP_THRESHOLD", 2)
RETENTION_DEFAULT_BREAK_DAYS = env_int("RETENTION_DEFAULT_BREAK_DAYS", 60)
RETENTION_DEFAULT_MIN_EMPLOYMENT_DAYS = env_int("RETENTION_DEFAULT_MIN_EMPLOYMENT_DAYS", 365)
CONSENT_WITHDRAWAL_STOP = env_bool("CONSENT_WITHDRAWAL_STOP", True)
DPDP_CHILD_AGE_LIMIT = env_int("DPDP_CHILD_AGE_LIMIT", 18)
F08_SIMPLIFIED_MVP = env_bool("F08_SIMPLIFIED_MVP", True)
NON_REPLIER_SAMPLE_RATE = float(env("NON_REPLIER_SAMPLE_RATE", "0.10"))
CLAIMED_JOB_SAMPLE_RATE = float(env("CLAIMED_JOB_SAMPLE_RATE", "0.05"))
SELF_EMPLOYMENT_SAMPLE_RATE = float(env("SELF_EMPLOYMENT_SAMPLE_RATE", "0.10"))
CSV_MAX_UPLOAD_BYTES = env_int("CSV_MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
AGEING_SNAPSHOT_DAYS = env_int("AGEING_SNAPSHOT_DAYS", 90)
DEMO_REPLY_PROBABILITY = float(env("DEMO_REPLY_PROBABILITY", "0.25"))

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_filters",
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
    "trainees.apps.TraineesConfig",
    "providers.apps.ProvidersConfig",
    "officers.apps.OfficersConfig",
    "policy.apps.PolicyConfig",
    "analytics.apps.AnalyticsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.UtidSessionMiddleware",
    "core.middleware.RLSMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.AuditLogMiddleware",
]

# WhiteNoise serves /static/ from the container. Django's own staticfiles view
# only answers when DEBUG is True, so under gunicorn with DEBUG=False every
# stylesheet 404s without it. It lives in requirements-prod.txt rather than
# requirements.txt, so it is imported defensively: a developer who has only run
# `pip install -r requirements.txt` still gets a working DEBUG server instead of
# an ImportError on the first request.
try:  # pragma: no cover - depends on which requirements set is installed
    import whitenoise  # noqa: F401

    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    _HAVE_WHITENOISE = True
except ModuleNotFoundError:  # pragma: no cover
    _HAVE_WHITENOISE = False

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "core.context_processors.app_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {"default": database_config("kaushal_parinam")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"
SESSION_COOKIE_AGE = 60 * 60 * 24  # 24 hours (docs/02-TRD.md §5)
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# ---------------------------------------------------------------------------
# Internationalisation — Marathi, Hindi, English (docs/04-UI-UX-BRIEF.md §3)
# ---------------------------------------------------------------------------
LANGUAGE_CODE = env("DJANGO_LANGUAGE_CODE", "en")
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("mr", "Marathi"),
    ("hi", "Hindi"),
    ("en", "English"),
]
# No .po/.mo catalogs are compiled yet, so this directory is absent and Django
# renders every page in English. See README.md section 8.
LOCALE_PATHS = [BASE_DIR / "locale"]

# ---------------------------------------------------------------------------
# Static and media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# Hash-named static files (WhiteNoise's CompressedManifest) are the right
# production choice: immutable filenames, so they can be cached for a year.
# They have a hard dependency on a build artefact, though -- every {% static %}
# lookup consults staticfiles/staticfiles.json and raises
# "Missing staticfiles manifest entry" without it. That failure is exactly what
# you want in production, where it means collectstatic was never run. It is pure
# noise in a test run, so the test command uses the plain storage; the template
# code under test is identical either way, only the resolved URL differs.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if _TESTING or not _HAVE_WHITENOISE
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

# No FileField or ImageField exists in this MVP, so nothing writes here. The
# directory is created on demand if an upload feature is added later.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

LOG_DIR = BASE_DIR / "logs"
# logs/ is gitignored and git does not track empty directories, so a fresh clone
# has no logs/ at all. Without this the RotatingFileHandler below cannot open its
# file and Django refuses to start, which breaks every command including
# `migrate` and `test`.
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Email — console backend for the demo (docs/02-TRD.md §11)
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-reply@kaushal-parinam.local")

# ---------------------------------------------------------------------------
# Security (docs/02-TRD.md §5, docs/05-BACKEND-SCHEMA.md §5)
# ---------------------------------------------------------------------------
# CSRF_TRUSTED_ORIGINS must list every origin the browser will POST from. It was
# unset, which is invisible on localhost (the Origin header matches the host) and
# fatal in production: with DEBUG=False every form submission, login included,
# fails the CSRF check and the app is unusable. Derived from ALLOWED_HOSTS so the
# two cannot drift, and overridable for a deployment whose Origin differs from
# its Host (a CDN or a TLS-terminating proxy).
def _build_default_csrf_origins(hosts: list[str]) -> list[str]:
    origins = ["https://*.onrender.com", "https://*.vercel.app"]
    for host in hosts:
        if host.startswith("."):
            origins.append(f"https://*{host}")
        elif host not in {"localhost", "127.0.0.1", "[::1]", "testserver"}:
            origins.append(f"https://{host}")
            origins.append(f"http://{host}")
        else:
            origins.append(f"http://{host}")
            origins.append(f"https://{host}")
    return list(dict.fromkeys(origins))

CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    ",".join(_build_default_csrf_origins(ALLOWED_HOSTS)),
)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env_bool("USE_X_FORWARDED_PROTO", True)
    else None
)
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
DATA_UPLOAD_MAX_MEMORY_SIZE = CSV_MAX_UPLOAD_BYTES
FILE_UPLOAD_MAX_MEMORY_SIZE = CSV_MAX_UPLOAD_BYTES

# ---------------------------------------------------------------------------
# Logging — structured JSON lines (docs/02-TRD.md §11)
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "()": "core.logging.StructuredJsonFormatter",
        },
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
        },
        "errors_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "errors.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 14,
            "formatter": "structured",
            "level": "ERROR",
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {
            "handlers": ["console", "errors_file"],
            "level": "ERROR",
            "propagate": False,
        },
        "core.audit": {
            "handlers": ["console", "errors_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}