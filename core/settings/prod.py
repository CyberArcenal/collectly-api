# core/settings/prod.py
from .base import *
import dj_database_url
import os
DEBUG = False
ALLOWED_HOSTS = ["*"]
# ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS").split(",")
CORS_ALLOW_ALL_ORIGINS = True


DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv("DATABASE_URL")
    )
}

# Add database timeout settings
DATABASES['default']['OPTIONS'] = {
    'connect_timeout': 10,
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv("REDIS_URL", "redis://127.0.0.1:6379")],
        },
    },
}
