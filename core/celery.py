import os
import django
from celery import Celery
from django.conf import settings

# 1. Set the settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')

# 2. Initialize Django (only once)
django.setup()

# 3. Setup Celery
app = Celery('core')

# 4. Load configuration from Django settings
app.config_from_object('django.conf:settings', namespace='CELERY')

# 5. Autodiscover tasks from all installed apps
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)

# Ensure broker URL from settings
app.conf.broker_url = settings.CELERY_BROKER_URL
app.conf.result_backend = settings.CELERY_RESULT_BACKEND

# 6. Optional: Load Django's logging configuration
app.conf.update(
    worker_hijack_root_logger=False,
    worker_log_format='[%(asctime)s: %(levelname)s/%(processName)s] %(message)s',
    worker_task_log_format='[%(asctime)s: %(levelname)s/%(processName)s] %(task_name)s[%(task_id)s]: %(message)s',
)


@app.task(bind=True)
def debug_task(self):
    """Debug task to test Celery is working."""
    print(f'Request: {self.request!r}')