import os


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT_URLCONF = "core.urls"
STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")
AUTH_USER_MODEL = "users.User"
SITE_ID = 1
DJANGO_VERSION = "5.2.3"
SYSTEM_VERSION = os.getenv("SYSTEM_VERSION", None)
SECRET_KEY = os.getenv("SECRET_KEY")
BASE_URL = os.getenv("BASE_URL", None)
WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:8000")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
