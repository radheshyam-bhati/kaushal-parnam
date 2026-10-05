"""Dependency-free environment + DATABASE_URL helpers.

docs/02-TRD.md §9 lists the environment variables. The project must run with no
extra packages installed, so parsing is done here instead of pulling in
django-environ / dj-database-url.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

TRUE_VALUES = {"1", "true", "yes", "on", "y", "t"}
FALSE_VALUES = {"0", "false", "no", "off", "n", "f"}


def load_dotenv(path: Path | str | None = None) -> dict[str, str]:
    """Read a .env file into os.environ without overwriting real env vars."""
    env_path = Path(path) if path else BASE_DIR / ".env"
    loaded: dict[str, str] = {}
    if not env_path.is_file():
        return loaded
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value


def env_bool(key: str, default: bool = False) -> bool:
    raw = env(key)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    return default


def env_int(key: str, default: int) -> int:
    raw = env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_list(key: str, default: str = "") -> list[str]:
    raw = env(key, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def database_config(default_name: str) -> dict:
    """Build a Django DATABASES['default'] dict.

    Uses DATABASE_URL when set (docs/02-TRD.md §9). Falls back to SQLite so the
    app runs with zero setup; note that Row-Level Security policies and
    FOR UPDATE SKIP LOCKED require PostgreSQL (see docs/10-BUILD-PLAN.md A-01).
    """
    url = env("DATABASE_URL")
    if not url:
        sqlite_name = env("DB_NAME", "db.sqlite3")
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / sqlite_name),
            "OPTIONS": {"timeout": 20},
        }

    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql", "psql", "pgsql"}:
        raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

    config = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")) or default_name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        # Deliberately False. The request transaction is opened by
        # core.middleware.RLSMiddleware, not by Django, because the RLS session
        # variables are written with set_config(..., true) and that only binds
        # for the duration of the transaction that issued them. Django opens
        # ATOMIC_REQUESTS after every middleware has run, which is too late --
        # the settings were already committed and discarded. One explicit
        # boundary, in one place, is easier to reason about than two.
        "ATOMIC_REQUESTS": False,
        "CONN_MAX_AGE": env_int("CONN_MAX_AGE", 60),
        "OPTIONS": {},
    }
    if env_bool("DB_SSL", False):
        config["OPTIONS"]["sslmode"] = env("DB_SSLMODE", "require")
    return config