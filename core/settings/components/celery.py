# Celery Configuration
#/settings/component/celery.py
from celery.schedules import crontab


CELERY_BROKER_URL = (
    "redis://localhost:6379/0"  # Same Redis can be used for both Celery and Channels
)
CELERY_RESULT_BACKEND = "redis://localhost:6379/1"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Manila"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"




CELERY_BEAT_SCHEDULE = {

}

