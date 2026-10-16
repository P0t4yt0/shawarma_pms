from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "change-me"          # move to env var in production
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # --- project apps (same modular layout as jbson_v2) ---
    "security",
    "activity_log",
    "inventory",
    "point_of_sale",
    "promotions",
    "billing_payment",
    "reports_analytics",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "activity_log.middleware.CurrentRequestMiddleware",
]

ROOT_URLCONF = "shawarma_pms.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "shawarma_pms.wsgi.application"
# ASGI_APPLICATION = "shawarma_pms.asgi.application"   # Phase 2 (Channels)

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "shawarma_dev.db"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_TZ = True
STATIC_URL = "static/"
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
LOGIN_URL = "/security/login/"
LOGIN_REDIRECT_URL = "/security/home/"

# --- business settings ---
POS_VAT_RATE = Decimal("0.12")            # VAT-inclusive prices; set to 0 if not VAT-registered
POS_STORE_NAME = "Fake Arab Shawarma Station"
INVENTORY_BLOCK_ON_INSUFFICIENT = False   # False: keep selling, stock may dip below 0 and alert fires
KDS_POLL_SECONDS = 4                      # Phase 1 polling interval (3-5 s)
KDS_TARGET_TICKET_SECONDS = 600           # SLA used by analytics (10 min)
SLOW_PERIOD_THRESHOLD = 0.6               # hour is "slow" if < 60% of average hourly orders
STORE_OPEN_HOUR = 10                      # trading hours used by slow-period detection
STORE_CLOSE_HOUR = 22
KDS_REALTIME = False                      # Phase 2 switch: True = push over Channels (needs channels + redis)
# CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer",
#                               "CONFIG": {"hosts": [("127.0.0.1", 6379)]}}}
