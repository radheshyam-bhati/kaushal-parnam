"""Django settings for the Kaushal Parinam project (SIH26135).

Environment variables follow docs/02-TRD.md §9 and docs/05-BACKEND-SCHEMA.md §1.
Database is PostgreSQL (Row-Level Security, FOR UPDATE SKIP LOCKED); SQLite is a
zero-setup fallback for local development only.
"""

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

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", "django-insecure-dev-key-do-not-use-in-production")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver")

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
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if env_bool("USE_X_FORWARDED_PROTO", False) else None
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