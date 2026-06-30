from django.core.management.base import BaseCommand
from debts.tasks import correct_misoverdue_debts, force_overdue_correction, correct_specific_debt


class Command(BaseCommand):
    help = 'Correct misclassified overdue debts manually'

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
            help='Correct a specific debt ID',
        )

    def handle(self, *args, **options):
        debt_id = options.get('debt_id')

        if debt_id:
            result = correct_specific_debt.apply(args=[debt_id])
            self.stdout.write(
                self.style.SUCCESS(f"Result for debt #{debt_id}:")
            )
            self.stdout.write(f"  {result.result}")
            return

        self.stdout.write(self.style.WARNING('Running overdue status correction...'))

        if options.get('sync'):
            # Run synchronously (blocking)
            task = correct_misoverdue_debts if not options.get('force') else force_overdue_correction
            result = task.apply()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Completed: {result.result['corrected']} corrected "
                    f"out of {result.result.get('total_checked', 0)} checked"
                )
            )
            if result.result.get('details'):
                self.stdout.write("\nDetails:")
                for detail in result.result['details'][:5]:
                    self.stdout.write(
                        f"  - Debt #{detail['debt_id']}: "
                        f"{detail['old_status']} → {detail['new_status']} "
                        f"({detail['reason']})"
                    )
                if len(result.result['details']) > 5:
                    self.stdout.write(f"  ... and {len(result.result['details']) - 5} more")
        else:
            # Run asynchronously
            task = correct_misoverdue_debts.delay if not options.get('force') else force_overdue_correction.delay
            result = task()
            self.stdout.write(
                self.style.SUCCESS(f"Task queued (ID: {result.id})")
            )
            self.stdout.write("Check logs for progress.")