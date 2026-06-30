from django.core.management.base import BaseCommand
from debts.tasks import (
    fix_zero_balance_debts,
    force_zero_balance_fix,
    fix_specific_debt_zero_balance,
    preview_zero_balance_debts,
)


class Command(BaseCommand):
    help = 'Fix debts with zero remaining balance manually'

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
            help='Fix a specific debt ID',
        )
        parser.add_argument(
            '--preview',
            action='store_true',
            help='Preview which debts would be fixed without actually updating',
        )

    def handle(self, *args, **options):
        if options.get('preview'):
            result = preview_zero_balance_debts.apply()
            data = result.result

            self.stdout.write(self.style.WARNING(f"Preview: {data['count']} debts would be fixed"))
            for debt in data['debts'][:10]:
                self.stdout.write(
                    f"  - Debt #{debt['debt_id']}: {debt['debt_name']} "
                    f"({debt['borrower_name']}) - status: {debt['status']}, "
                    f"remaining: ₱{debt['remaining_balance']:.2f}"
                )
            if len(data['debts']) > 10:
                self.stdout.write(f"  ... and {len(data['debts']) - 10} more")
            return

        debt_id = options.get('debt_id')

        if debt_id:
            result = fix_specific_debt_zero_balance.apply(args=[debt_id])
            result_data = result.result

            if result_data['success']:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✅ Debt #{debt_id} fixed: "
                        f"{result_data['old_status']} → {result_data['new_status']}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f"❌ {result_data['message']}")
                )
            return

        self.stdout.write(self.style.WARNING('Running zero balance fixer...'))

        if options.get('sync'):
            # Run synchronously (blocking)
            task = fix_zero_balance_debts if not options.get('force') else force_zero_balance_fix
            result = task.apply()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Completed: {result.result['fixed']} debts fixed "
                    f"out of {result.result.get('total_checked', 0)} checked"
                )
            )
            if result.result.get('details'):
                self.stdout.write("\nDetails:")
                for detail in result.result['details'][:5]:
                    self.stdout.write(
                        f"  - Debt #{detail['debt_id']}: "
                        f"{detail['old_status']} → {detail['new_status']}"
                    )
                if len(result.result['details']) > 5:
                    self.stdout.write(f"  ... and {len(result.result['details']) - 5} more")
        else:
            # Run asynchronously
            task = fix_zero_balance_debts.delay if not options.get('force') else force_zero_balance_fix.delay
            result = task()
            self.stdout.write(
                self.style.SUCCESS(f"Task queued (ID: {result.id})")
            )
            self.stdout.write("Check logs for progress.")