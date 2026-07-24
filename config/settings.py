"""
Configuration Django — API DOTO+.

Écosystème santé numérique béninois : carte d'accès QR (DodoCard),
plateforme web pro (DotoHub), app patient (DotoPlus) et back-office (DotoPlus Admin).
"""
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


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
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    "localhost,127.0.0.1,testserver,192.168.100.3,10.0.2.2,0.0.0.0,*",
)

# Clé de chiffrement AES-256 pour les tokens QR (Fernet). Générée si absente.
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
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "audit.middleware.AuditLogMiddleware",
]

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

# Base de données : SQLite par défaut (dev), PostgreSQL recommandé en prod (CDC §6.1).
if env("POSTGRES_DB"):
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
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
# Base publique pour URLs absolues media (apps BlueStacks / LAN).
PUBLIC_API_BASE = env("PUBLIC_API_BASE", "http://127.0.0.1:8000")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── Django REST Framework ──────────────────────────────────────────────
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

# JWT access/refresh (CDC historique ~5–10 min access — défaut env 60 min pour démos).
# Inactivité UI côté front (idle logout) est séparée ; refresh auto sur 401 côté clients.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("ACCESS_TOKEN_MINUTES", "60"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("REFRESH_TOKEN_DAYS", "7"))),
    "ROTATE_REFRESH_TOKENS": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ─── CORS (frontends DotoHub / DotoPlus Admin / Expo) ───────────────────
# En DEBUG : tout autorisé (hotspot, LAN, Expo). En prod : liste explicite.
CORS_ALLOW_ALL_ORIGINS = env_bool("CORS_ALLOW_ALL_ORIGINS", DEBUG)
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
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://192.168.137.1:5173,http://192.168.100.3:5173,"
    "http://localhost:5174,http://127.0.0.1:5174",
)

# ─── Sécurité connexion (CDC §3.2 / §6.2) ───────────────────────────────
LOGIN_MAX_ATTEMPTS = int(env("LOGIN_MAX_ATTEMPTS", "3"))
LOGIN_LOCKOUT_MINUTES = int(env("LOGIN_LOCKOUT_MINUTES", "15"))
PATIENT_PIN_MAX_ATTEMPTS = int(env("PATIENT_PIN_MAX_ATTEMPTS", "5"))

# OTP / SMS (interface SmsProvider — mock par défaut, Twilio en stub).
DEMO_OTP_CODE = env("DEMO_OTP_CODE", "000000")
SMS_PROVIDER = env("SMS_PROVIDER", "mock")  # mock | twilio
OTP_TTL_SECONDS = int(env("OTP_TTL_SECONDS", "300"))
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = env("TWILIO_FROM_NUMBER", "")

# ANIP (interface AnipClient — mock par défaut, HTTP stub).
ANIP_PROVIDER = env("ANIP_PROVIDER", "mock")  # mock | http
ANIP_BASE_URL = env("ANIP_BASE_URL", "")
ANIP_API_KEY = env("ANIP_API_KEY", "")

# Cache mémoire pour OTP (dev). En prod : Redis recommandé.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "doto-otp",
    }
}

# Identité produit
PRODUCT_NAME = "DOTO+"
BRAND = "DOTO+"
CARD_PRODUCT = "DodoCard"

# Consentement d'accès dossier (TTL minutes)
# 10 min : laisse le temps au patient de voir la demande (polling RN ~4s).
ACCESS_REQUEST_TTL_MINUTES = int(env("ACCESS_REQUEST_TTL_MINUTES", "10"))
ACCESS_GRANT_TTL_MINUTES = int(env("ACCESS_GRANT_TTL_MINUTES", "60"))
ACCESS_EMERGENCY_GRANT_TTL_MINUTES = int(env("ACCESS_EMERGENCY_GRANT_TTL_MINUTES", "30"))

# Push Expo (optionnel — sans token : mock/log uniquement)
EXPO_ACCESS_TOKEN = env("EXPO_ACCESS_TOKEN", "")
