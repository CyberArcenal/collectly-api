from .base import *
from .logger import *
DEBUG = True
ALLOWED_HOSTS = ["*"]
CORS_ALLOW_ALL_ORIGINS = True
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
# Temporary CSRF disable for API testing



# SQLite database para sa development
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
         'OPTIONS': {
            'timeout': 30,  # default ay 5 seconds, dagdagan mo
        }
    }
}
