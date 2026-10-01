"""Django settings for the DapperWalls enquiry API.

Everything is configured from environment variables. In production these are
set in cPanel's "Setup Python App" screen. For local development an optional
`.env` file in the project root is loaded (real environment variables win).
See `.env.example` for every variable.
"""
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env", override=False)

TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"


def env(name, default=None):
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def env_bool(name, default=False):
    value = env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    value = env(name)
    return default if value is None else int(value)


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# --- Core -------------------------------------------------------------------

DEBUG = env_bool("DEBUG", False)
IS_PRODUCTION = not DEBUG and not TESTING

SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "django-insecure-local-dev-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is False.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "api.dapperwalls.co.uk")
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1", "[::1]"]

CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "https://dapperwalls.co.uk,https://www.dapperwalls.co.uk,https://api.dapperwalls.co.uk",
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "enquiries",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ---------------------------------------------------------------
# DATABASE_URL, e.g. mysql://user:password@localhost:3306/dbname
# Defaults to a local SQLite file for development.

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=env_int("DB_CONN_MAX_AGE", 60),
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        }
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache (used for rate limiting) -----------------------------------------
# Passenger runs several processes, so the cache must be shared: use the
# database. Create the table with `python manage.py createcachetable`.

if TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "django_cache",
        }
    }

# --- Auth / i18n ------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True

# --- Static files (Django admin), served by WhiteNoise -----------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or TESTING
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# --- Admin ------------------------------------------------------------------

ADMIN_URL = env("ADMIN_URL", "manage-dw/").strip("/") + "/"

# --- CORS -------------------------------------------------------------------
# In production the browser talks to the Cloudflare Pages proxy on the same
# origin, so CORS mainly matters for the local Next.js dev server.

CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "https://dapperwalls.co.uk,https://www.dapperwalls.co.uk"
)
if DEBUG:
    CORS_ALLOWED_ORIGINS += ["http://localhost:3000", "http://127.0.0.1:3000"]
CORS_URLS_REGEX = r"^/api/.*$"
CORS_ALLOW_METHODS = ["GET", "POST", "OPTIONS"]

# --- Proxy / client IP ------------------------------------------------------
# The Cloudflare Pages Function sends X-Proxy-Token: <PROXY_SHARED_SECRET> and
# X-Client-IP: <visitor IP>. X-Client-IP is trusted only with a valid token.

PROXY_SHARED_SECRET = env("PROXY_SHARED_SECRET", "")
TRUSTED_IP_HEADER = env("TRUSTED_IP_HEADER", "HTTP_CF_CONNECTING_IP")

# --- Enquiries --------------------------------------------------------------

# Comma-separated: every address gets each new-enquiry notification.
ENQUIRY_NOTIFY_EMAIL = env_list("ENQUIRY_NOTIFY_EMAIL", "support@dapperwalls.co.uk")
ENQUIRY_REPLY_TO_EMAIL = env("ENQUIRY_REPLY_TO_EMAIL", "support@dapperwalls.co.uk")
ENQUIRY_MIN_ELAPSED_MS = env_int("ENQUIRY_MIN_ELAPSED_MS", 2500)
ENQUIRY_MAX_BODY_BYTES = 32 * 1024
RATE_LIMIT_PER_HOUR = env_int("RATE_LIMIT_PER_HOUR", 5)
RATE_LIMIT_PER_DAY = env_int("RATE_LIMIT_PER_DAY", 20)

# --- Email ------------------------------------------------------------------

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "DapperWalls <support@dapperwalls.co.uk>")
SERVER_EMAIL = env("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 465)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", True)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 10)
EMAIL_SUBJECT_PREFIX = "[DapperWalls API] "

# --- Security (production) --------------------------------------------------

if not DEBUG:
    # Cloudflare terminates TLS and sets X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = not TESTING
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True

# --- Logging ----------------------------------------------------------------
# stderr is captured by Passenger (see the app's stderr.log in cPanel).
# Set LOG_FILE to also write to a file.

LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOG_FILE = env("LOG_FILE")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["stderr"], "level": "WARNING"},
    "loggers": {
        "django": {
            "handlers": ["stderr"],
            "level": "CRITICAL" if TESTING else "WARNING",
            "propagate": False,
        },
        "enquiries": {
            "handlers": ["stderr"],
            "level": "CRITICAL" if TESTING else LOG_LEVEL,
            "propagate": False,
        },
    },
}
if LOG_FILE:
    LOGGING["handlers"]["file"] = {
        "class": "logging.handlers.WatchedFileHandler",
        "filename": LOG_FILE,
        "formatter": "standard",
    }
    for logger in [LOGGING["root"], *LOGGING["loggers"].values()]:
        logger["handlers"].append("file")
