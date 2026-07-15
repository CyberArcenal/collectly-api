INSTALLED_APPS = [
    # "daphne",
    # "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.sites",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "qrcode",
    "celery",
    "django_celery_beat",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "cloudinary",
    "cloudinary_storage",
    "drf_spectacular",
]

PROJECT_APPS = [
    "audit",
    "users",
    
    # Core business (10 apps)
    "borrowers",
    "debts",
    "payments",
    "loan_agreements",
    "loan_applications",
    "groups",
    "notifications",
    "payment_methods",
    "system_settings",
    "analytics",
    'sync',
]


INSTALLED_APPS += PROJECT_APPS
