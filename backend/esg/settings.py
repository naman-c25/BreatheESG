import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "core",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.middleware.TenantMiddleware",
]

ROOT_URLCONF = "esg.urls"
WSGI_APPLICATION = "esg.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}]

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# In production we drop the built React bundle here so whitenoise can serve it.
_FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
STATICFILES_DIRS = [_FRONTEND_DIST] if _FRONTEND_DIST.exists() else []
# Note: not using ManifestStaticFilesStorage to avoid hashing Vite's already-hashed assets.
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 100,
}

# Cookie auth requires explicit origin allowlist (can't combine with allow_all)
# and credentials enabled. In dev we accept localhost; in prod the SPA is
# served from the same origin as the API so CORS is a non-issue.
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
CORS_ALLOWED_ORIGIN_REGEXES = [r"^https://.*\.onrender\.com$"]
SESSION_COOKIE_SAMESITE = "Lax"

# We removed TenantListView (auth provides tenant), but the view itself still
# exists for reference. The TenantViewSet from earlier is not registered.
