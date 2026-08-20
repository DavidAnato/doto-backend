"""
Configuration Django - API DOTO+.

Écosystème santé numérique béninois : carte d'accès QR (DotoCard),
plateforme web pro (DotoHub), app patient (DotoPlus) et back-office (DotoPlus Admin).

Le fichier `.env` est **optionnel** : sans lui, le mode dév/test démarre
avec SQLite, CORS ouvert, CSRF assoupli, OTP mock, etc.
"""
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
# Ignore silencieusement l'absence de .env
load_dotenv(BASE_DIR / ".env", override=False)


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def env_list(key, default=""):
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me-in-production-dotoplus")
# Dév : True. Render définit RENDER=true → False même si DJANGO_DEBUG est absent
# (la page 500 technique Django réimporte config.urls → _DeadlockError).
DEBUG = env_bool("DJANGO_DEBUG", False if env("RENDER") else True)
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    # `*` = tous les hôtes (LAN, hotspot, Render, Expo) - adapté dév/test
    "*",
)

# Fernet optionnel : si vide, cards.services dérive depuis SECRET_KEY
CARD_TOKEN_KEY = env("CARD_TOKEN_KEY", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Tiers
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    # Applications métier DOTO+
    "accounts.apps.AccountsConfig",
    "patients",
    "medical",
    "cards",
    "audit",
    "notifications",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.OpenSecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "audit.middleware.AuditLogMiddleware",
]

# WhiteNoise optionnel (prod / collectstatic) - ne bloque pas le démarrage sans package
try:
    import whitenoise  # noqa: F401

    MIDDLEWARE.insert(2, "whitenoise.middleware.WhiteNoiseMiddleware")
    _HAS_WHITENOISE = True
except ImportError:
    _HAS_WHITENOISE = False

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Base de données : SQLite (dev), PostgreSQL via POSTGRES_* ou DATABASE_URL (Render).
_database_url = env("DATABASE_URL")
if _database_url:
    try:
        import dj_database_url

        DATABASES = {
            "default": dj_database_url.config(
                default=_database_url,
                conn_max_age=600,
                ssl_require=env_bool("DATABASE_SSL_REQUIRE", not DEBUG),
            )
        }
    except ImportError:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": env("POSTGRES_DB", "doto"),
                "USER": env("POSTGRES_USER", "postgres"),
                "PASSWORD": env("POSTGRES_PASSWORD", ""),
                "HOST": env("POSTGRES_HOST", "127.0.0.1"),
                "PORT": env("POSTGRES_PORT", "5432"),
            }
        }
elif env("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER", "postgres"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "127.0.0.1"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Porto-Novo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
if _HAS_WHITENOISE:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
        },
    }
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
PUBLIC_API_BASE = env(
    "PUBLIC_API_BASE",
    env("RENDER_EXTERNAL_URL")
    or ("https://doto-backend-71tk.onrender.com" if env("RENDER") else "http://127.0.0.1:8000"),
)

# Render / reverse-proxy HTTPS
if env_bool("DJANGO_BEHIND_PROXY", False) or env("RENDER"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DATETIME_FORMAT": "%Y-%m-%dT%H:%M:%S%z",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("ACCESS_TOKEN_MINUTES", "720"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("REFRESH_TOKEN_DAYS", "30"))),
    "ROTATE_REFRESH_TOKENS": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ─── CORS / CSRF - dév/test ouvert par défaut (pas de .env requis) ───────
# Forcer DJANGO_DEBUG=False + CORS_ALLOW_ALL_ORIGINS=False en prod stricte.
CORS_ALLOW_ALL_ORIGINS = env_bool("CORS_ALLOW_ALL_ORIGINS", True)
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:5174,http://127.0.0.1:5174,"
    "http://localhost:8081,http://127.0.0.1:8081,"
    "http://localhost:8082,http://127.0.0.1:8082,"
    "http://localhost:19006,http://127.0.0.1:19006,"
    "http://192.168.137.1:5173,http://192.168.100.3:5173",
)
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]
CORS_ALLOW_METHODS = ["DELETE", "GET", "OPTIONS", "PATCH", "POST", "PUT"]
# Liste large + middleware OpenSecurityMiddleware assouplit encore si OPEN_CSRF
_csrf_defaults = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:5174,http://127.0.0.1:5174,"
    "http://localhost:8081,http://127.0.0.1:8081,"
    "http://localhost:19006,http://127.0.0.1:19006,"
    "http://192.168.137.1:5173,http://192.168.100.3:5173,"
    "http://10.0.2.2:5173"
)
_render_url = env("RENDER_EXTERNAL_URL", "")
if _render_url:
    _csrf_defaults = f"{_csrf_defaults},{_render_url}"
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", _csrf_defaults)
# Si CORS tout ouvert : ne pas exiger Origin CSRF stricte (JWT porte l'auth)
OPEN_CSRF = env_bool("OPEN_CSRF", DEBUG or CORS_ALLOW_ALL_ORIGINS)

LOGIN_MAX_ATTEMPTS = int(env("LOGIN_MAX_ATTEMPTS", "3"))
LOGIN_LOCKOUT_MINUTES = int(env("LOGIN_LOCKOUT_MINUTES", "15"))
PATIENT_PIN_MAX_ATTEMPTS = int(env("PATIENT_PIN_MAX_ATTEMPTS", "5"))

# OTP démo produit : toujours « 00000 », même sans .env (non surchargeable).
DEMO_OTP_CODE = "00000"
# Sans SMS_PROVIDER (variable absente ou vide) → mock.
SMS_PROVIDER = (env("SMS_PROVIDER") or "mock").lower()
OTP_TTL_SECONDS = int(env("OTP_TTL_SECONDS", "300"))
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = env("TWILIO_FROM_NUMBER", "")

ANIP_PROVIDER = env("ANIP_PROVIDER", "mock")
ANIP_BASE_URL = env("ANIP_BASE_URL", "")
ANIP_API_KEY = env("ANIP_API_KEY", "")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "doto-otp",
    }
}

PRODUCT_NAME = "DOTO+"
BRAND = "DOTO+"
CARD_PRODUCT = "DotoCard"

ACCESS_REQUEST_TTL_MINUTES = int(env("ACCESS_REQUEST_TTL_MINUTES", "10"))
ACCESS_GRANT_TTL_MINUTES = int(env("ACCESS_GRANT_TTL_MINUTES", "60"))
ACCESS_EMERGENCY_GRANT_TTL_MINUTES = int(env("ACCESS_EMERGENCY_GRANT_TTL_MINUTES", "30"))

EXPO_ACCESS_TOKEN = env("EXPO_ACCESS_TOKEN", "")
