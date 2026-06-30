from django.core.management.base import BaseCommand
from debts.tasks import update_overdue_statuses, force_overdue_update, update_specific_debt_status, preview_overdue_update


class Command(BaseCommand):
    help = 'Update overdue statuses for debts manually'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force run even if already ran today',
        )
        parser.add_argument(
            '--sync',
            action='store_true',
            help='Run synchronously instead of async',
        )
        parser.add_argument(
            '--debt-id',
            type=int,
            help='Update a specific debt ID',
        )
        parser.add_argument(
            '--preview',
            action='store_true',
            help='Preview which debts would be updated without actually updating',
        )

    def handle(self, *args, **options):
        if options.get('preview'):
            from debts.tasks import preview_overdue_update
            result = preview_overdue_update.apply()
            data = result.result

            self.stdout.write(self.style.WARNING(f"Preview: {data['count']} debts would be marked as overdue"))
            for debt in data['debts'][:10]:
                self.stdout.write(
                    f"  - Debt #{debt['debt_id']}: {debt['debt_name']} "
                    f"({debt['days_overdue']} days overdue)"
                )
            if len(data['debts']) > 10:
                self.stdout.write(f"  ... and {len(data['debts']) - 10} more")
            return

        debt_id = options.get('debt_id')

        if debt_id:
            result = update_specific_debt_status.apply(args=[debt_id])
            self.stdout.write(
                self.style.SUCCESS(f"Result for debt #{debt_id}:")
            )
            self.stdout.write(f"  {result.result}")
            return

        self.stdout.write(self.style.WARNING('Running overdue status update...'))

        if options.get('sync'):
            # Run synchronously (blocking)
            task = update_overdue_statuses if not options.get('force') else force_overdue_update
            result = task.apply()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Completed: {result.result['updated']} updated, "
                    f"{result.result.get('failed', 0)} failed"
                )
            )
            if result.result.get('details'):
                self.stdout.write("\nDetails:")
                for detail in result.result['details'][:5]:
                    self.stdout.write(
                        f"  - Debt #{detail['debt_id']}: "
                        f"{detail['days_overdue']} days overdue"
                    )
                if len(result.result['details']) > 5:
                    self.stdout.write(f"  ... and {len(result.result['details']) - 5} more")
        else:
            # Run asynchronously
            task = update_overdue_statuses.delay if not options.get('force') else force_overdue_update.delay
            result = task()
            self.stdout.write(
                self.style.SUCCESS(f"Task queued (ID: {result.id})")
            )
            self.stdout.write("Check logs for progress.")