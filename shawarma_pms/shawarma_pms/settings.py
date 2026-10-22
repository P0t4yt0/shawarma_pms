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
    "maintenance",
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


# ---- Smart Promotion Recommendations (reports_analytics/recommendations.py) -------------------------
PROMO_REC_LOOKBACK_DAYS = 56              # history window analysed (today is always excluded)
PROMO_REC_MIN_DAYS = 14                   # trading days with sales required before ANY forecast is shown
PROMO_REC_MIN_ORDERS = 100                # orders required before ANY forecast is shown
PROMO_REC_MIN_WEEKDAY_SAMPLES = 3         # e.g. at least 3 Tuesdays before judging Tuesday hours
PROMO_REC_MIN_BASELINE_ORDERS_PER_HOUR = 1.0   # ignore tiny volumes (noise)
PROMO_REC_TREND_DAYS = 14                 # "recent" vs "previous" window for declining products
PROMO_REC_MIN_PRIOR_UNITS = 10            # a product needs this many units in the previous window to count as declining
PROMO_REC_DECLINE_PCT = 25                # decline (%) that flags a product
PROMO_REC_SLOW_FREQ_PCT = 30              # sold on <= this % of trading days = slow mover
PROMO_REC_MIN_PAIR_ORDERS = 8             # orders containing both items before suggesting a bundle
PROMO_REC_MIN_LIFT = 1.2                  # bundle pair must occur this many times more than chance
PROMO_REC_MIN_MARGIN_PCT = 20             # PROFIT GUARDRAIL: margin after discount must stay >= this
PROMO_REC_MIN_COST_COVERAGE = 0.6         # share of revenue whose cost (recipe) must be known for whole-menu promos
PROMO_REC_STOCK_COVER_DAYS = 2            # STOCK GUARDRAIL: stock must cover this many days of promo-day demand...
PROMO_REC_STOCK_BUFFER = 1.2              # ...times this safety buffer
PROMO_REC_DISCOUNT_STEPS = (5, 10, 15, 20)
PROMO_REC_DEFAULT_ELASTICITY = 2.0        # used ONLY when no past promotion can be measured (labelled "assumed")
PROMO_REC_GAP_RECOVERY_PER_PCT = 0.02     # slow periods: share of the missing orders won back per 1% discount
PROMO_REC_MAX_GAP_RECOVERY = 0.5          # never assume more than half of the gap is recovered
PROMO_REC_CACHE_SECONDS = 300
