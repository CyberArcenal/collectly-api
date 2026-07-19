import os

from celery.schedules import crontab

# Celery configuration
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://redis:6379/0")
)
CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://redis:6379/0")
)

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Manila"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_WORKER_CONCURRENCY = 1  # or 2 at most for SQLite
CELERY_TASK_ALWAYS_EAGER = False

CELERY_BEAT_SCHEDULE = {
    # Retry failed notifications every hour
    "retry-failed-notifications": {
        "task": "notifications.tasks.reminder.retry_failed_notifications",
        "schedule": crontab(minute=0),
    },
    # Clean up old notification logs daily at 3 AM
    "cleanup-old-notification-logs": {
        "task": "notifications.tasks.reminder.cleanup_old_notification_logs",
        "schedule": crontab(hour=3, minute=0),
        "args": (90,),
    },
    # Send scheduled notifications every 15 minutes
    "send-scheduled-notifications": {
        "task": "notifications.tasks.reminder.send_scheduled_notifications",
        "schedule": crontab(minute="*/15"),
    },
    # Audit trail cleanup daily at 2 AM
    "cleanup-old-audit-trails": {
        "task": "audit.tasks.cleanup_old_audit_trails",
        "schedule": crontab(hour=2, minute=0),
    },
    # Interest accrual daily at midnight
    "run-interest-accrual": {
        "task": "debts.tasks.run_interest_accrual",
        "schedule": crontab(hour=0, minute=0),
    },
    # Overdue reminders daily at 9 AM
    "send-overdue-reminders": {
        "task": "notifications.tasks.reminder.send_overdue_reminders",
        "schedule": crontab(hour=9, minute=0),
    },
    # Overdue status corrector daily at 1 AM
    "correct-misoverdue-debts": {
        "task": "debts.tasks.correct_misoverdue_debts",
        "schedule": crontab(hour=1, minute=0),
    },
    # Overdue status updater daily at 12:15 AM
    "update-overdue-statuses": {
        "task": "debts.tasks.update_overdue_statuses",
        "schedule": crontab(hour=0, minute=15),
    },
    # Overdue health check every Sunday at 2 AM
    "check-overdue-status-health": {
        "task": "debts.tasks.check_overdue_status_health",
        "schedule": crontab(hour=2, minute=0, day_of_week=0),
    },
    # Penalty application scheduler daily at 1:30 AM
    "apply-auto-penalties": {
        "task": "payments.tasks.apply_auto_penalties",
        "schedule": crontab(hour=1, minute=30),
    },
    # Penalty health check every Monday at 3 AM
    "check-penalty-health": {
        "task": "payments.tasks.check_penalty_application_health",
        "schedule": crontab(hour=3, minute=0, day_of_week=1),
    },
    # ✅ Zero balance fixer daily at 4 AM
    "fix-zero-balance-debts": {
        "task": "debts.tasks.fix_zero_balance_debts",
        "schedule": crontab(hour=4, minute=0),  # 4:00 AM daily
    },
    # ✅ Zero balance health check every Wednesday at 4:30 AM
    "check-zero-balance-health": {
        "task": "debts.tasks.check_zero_balance_health",
        "schedule": crontab(hour=4, minute=30, day_of_week=3),  # Wednesday 4:30 AM
    },
}
