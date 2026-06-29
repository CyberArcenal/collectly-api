from pathlib import Path
import os
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.


load_dotenv()


# SECURITY WARNING: don't run with debug turned on in production!
from .components.variables          import *
from .components.apps               import *

from .components.middleware         import *
from .components.validators         import *
from .components.templates          import *
from .components.pymongo            import *

from .components.rest_frameworks    import *
from .components.cors               import *
from .components.celery             import *
from .components.time               import *
from .components.mail               import *
# from .components.pdfkit             import *
from .components.jwt                import *
from .components.spectacular        import *

from .components.cloudinary         import *
from .components.activation_config import *





CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "TIMEOUT": 36000,
        "KEY_PREFIX": "django_mail_admin",
        "LOCATION": "unique-snowflake",
    },
    "django_mail_admin": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "TIMEOUT": 36000,
        "KEY_PREFIX": "django_mail_admin",
    },
}



