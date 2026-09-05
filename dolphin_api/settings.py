from datetime import timedelta
from pathlib import Path

from decouple import Csv, UndefinedValueError, config
from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    value = str(config(name, default=str(default))).lower()
    return value in {"1", "true", "yes", "on", "debug", "development", "dev"}


SECRET_KEY = config("SECRET_KEY", default="dev-only-change-me-with-at-least-32-characters")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "commerce",
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
]

ROOT_URLCONF = "dolphin_api.urls"

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
            ],
        },
    }
]

WSGI_APPLICATION = "dolphin_api.wsgi.application"

def required_env(name, allow_blank=False):
    try:
        value = config(name, default="") if allow_blank else config(name)
    except UndefinedValueError as exc:
        raise ImproperlyConfigured(f"Variable d'environnement manquante: {name}. Copiez backend/env.example vers backend/.env puis configurez XAMPP MySQL.") from exc
    if not allow_blank and value == "":
        raise ImproperlyConfigured(f"Variable d'environnement vide: {name}. Configurez backend/.env pour XAMPP MySQL.")
    return value


DB_ENGINE = required_env("DB_ENGINE")
if DB_ENGINE != "django.db.backends.mysql":
    raise ImproperlyConfigured("DOLPHIN est configure pour XAMPP MySQL uniquement. DB_ENGINE doit valoir django.db.backends.mysql.")

DATABASES = {
    "default": {
        "ENGINE": DB_ENGINE,
        "NAME": required_env("DB_NAME"),
        "USER": required_env("DB_USER", allow_blank=True),
        "PASSWORD": required_env("DB_PASSWORD", allow_blank=True),
        "HOST": required_env("DB_HOST"),
        "PORT": required_env("DB_PORT"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

AUTH_USER_MODEL = "commerce.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr"
TIME_ZONE = "Africa/Casablanca"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
SERVE_MEDIA_FILES = env_bool("SERVE_MEDIA_FILES", True)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:5173,http://127.0.0.1:5173", cast=Csv())
CORS_ALLOW_HEADERS = (*default_headers, "x-session-key")
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="http://localhost:5173,http://127.0.0.1:5173", cast=Csv())

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 12,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "commerce.throttles.AuthThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "200/hour", "user": "1000/hour", "auth": "10/minute", "coupon": "20/hour"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=20),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "DOLPHIN API",
    "DESCRIPTION": "API REST pour la plateforme e-commerce DOLPHIN.",
    "VERSION": "1.0.0",
}

EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="no-reply@dolphin.local")
SITE_NAME = "DOLPHIN"
MOROCCAN_CURRENCY = "MAD"
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
SUPPLIER_IMAGE_DOMAINS = config("SUPPLIER_IMAGE_DOMAINS", default="", cast=Csv())
