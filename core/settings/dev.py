import dj_database_url

from .base import *
from .logger import *
DEBUG = True
ALLOWED_HOSTS = ["*"]
CORS_ALLOW_ALL_ORIGINS = True
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')



CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}
# Temporary CSRF disable for API testing



# SQLite database para sa development
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'collectly_db'),
        'USER': os.getenv('POSTGRES_USER', 'collectly'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'collectly_password'),
        'HOST': os.getenv('DB_HOST', 'db'),   # "db" is the service name in compose
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}
