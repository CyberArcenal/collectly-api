# Create a management command to set up the periodic task
# audit/management/commands/setup_audit_cleanup.py

from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule
import json


class Command(BaseCommand):
    help = 'Setup audit trail cleanup periodic task'

    def handle(self, *args, **options):
        # Create or get interval schedule
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=1,
            period=IntervalSchedule.DAYS,
        )

        # Create periodic task
        PeriodicTask.objects.get_or_create(
            name='Clean up old audit trails',
            defaults={
                'task': 'audit.tasks.cleanup_old_audit_trails',
                'interval': schedule,
                'enabled': True,
                'args': json.dumps([]),
                'kwargs': json.dumps({}),
            }
        )

        self.stdout.write(self.style.SUCCESS('Successfully created audit cleanup task'))