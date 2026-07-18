import dj_database_url

from .base import *
from .logger import *
DEBUG = True
ALLOWED_HOSTS = ["*"]
CORS_ALLOW_ALL_ORIGINS = True
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
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
