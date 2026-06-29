# BarangaySystem/celery.py

import os
import django
from django.conf import settings
from celery import Celery

# 1. Itakda ang settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')

# 2. I-initialize ang Django at i-apply ang LOGGING config
# django.setup()

# 3. I-setup ang Celery
app = Celery('core')
app.config_from_object('django.conf:settings', namespace='CELERY')

# 4. Autodiscover tasks
app.autodiscover_tasks()

if __name__ == "__main__":
    # Huwag kalimutang gamitin ang worker_main() para sa Windows
    app.worker_main()