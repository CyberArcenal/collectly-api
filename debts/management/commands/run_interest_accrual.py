from django.core.management.base import BaseCommand
from debts.tasks import run_interest_accrual


class Command(BaseCommand):
    help = 'Run interest accrual manually'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sync',
            action='store_true',
            help='Run synchronously instead of async',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Running interest accrual...'))

        if options.get('sync'):
            # Run synchronously (blocking)
            result = run_interest_accrual.apply()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Completed: {result.result['processed']} processed, "
                    f"{result.result['errors']} errors"
                )
            )
        else:
            # Run asynchronously
            task = run_interest_accrual.delay()
            self.stdout.write(
                self.style.SUCCESS(f"Task queued (ID: {task.id})")
            )
            self.stdout.write("Check logs for progress.")