from django.core.management.base import BaseCommand
from payments.tasks import (
    apply_auto_penalties,
    force_penalty_application,
    apply_penalty_to_specific_debt,
    preview_penalty_application,
)


class Command(BaseCommand):
    help = 'Apply auto-penalties manually'

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
            help='Apply penalty to a specific debt ID',
        )
        parser.add_argument(
            '--preview',
            action='store_true',
            help='Preview which debts would receive penalties without applying',
        )

    def handle(self, *args, **options):
        if options.get('preview'):
            result = preview_penalty_application.apply()
            data = result.result

            self.stdout.write(self.style.WARNING(f"Preview: {data['count']} debts eligible for penalty"))
            self.stdout.write(f"Settings: grace_days={data['settings']['grace_days']}, "
                             f"method={data['settings']['calculation_method']}, "
                             f"rate={data['settings']['default_rate']}")
            self.stdout.write("")

            for debt in data['debts'][:10]:
                status = "✅ WILL APPLY" if debt['will_apply'] else "⏸️ SKIPPED"
                self.stdout.write(
                    f"  {status} Debt #{debt['debt_id']}: {debt['debt_name']} "
                    f"({debt['days_overdue']} days overdue) "
                    f"→ ₱{debt['penalty_amount']:.2f}"
                )
                if not debt['will_apply']:
                    if debt['already_penalized']:
                        self.stdout.write(f"      ↳ Already penalized")
                    elif debt['penalty_amount'] <= 0:
                        self.stdout.write(f"      ↳ Penalty amount is zero")

            if len(data['debts']) > 10:
                self.stdout.write(f"  ... and {len(data['debts']) - 10} more")
            return

        debt_id = options.get('debt_id')

        if debt_id:
            result = apply_penalty_to_specific_debt.apply(args=[debt_id])
            result_data = result.result

            if result_data['success']:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✅ Penalty applied to debt #{debt_id}: "
                        f"₱{result_data['penalty_amount']:.2f} "
                        f"(Penalty ID: {result_data['penalty_id']})"
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f"❌ {result_data['message']}")
                )
            return

        self.stdout.write(self.style.WARNING('Running auto-penalty application...'))

        if options.get('sync'):
            # Run synchronously (blocking)
            task = apply_auto_penalties if not options.get('force') else force_penalty_application
            result = task.apply()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Completed: {result.result['applied']} applied, "
                    f"{result.result.get('skipped', 0)} skipped, "
                    f"{result.result.get('failed', 0)} failed"
                )
            )
            if result.result.get('details'):
                self.stdout.write("\nDetails:")
                for detail in result.result['details'][:5]:
                    self.stdout.write(
                        f"  - Debt #{detail['debt_id']}: "
                        f"₱{detail['penalty_amount']:.2f} penalty applied"
                    )
                if len(result.result['details']) > 5:
                    self.stdout.write(f"  ... and {len(result.result['details']) - 5} more")
        else:
            # Run asynchronously
            task = apply_auto_penalties.delay if not options.get('force') else force_penalty_application.delay
            result = task()
            self.stdout.write(
                self.style.SUCCESS(f"Task queued (ID: {result.id})")
            )
            self.stdout.write("Check logs for progress.")