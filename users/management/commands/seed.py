# users/management/commands/seed.py
import random
import uuid
from decimal import Decimal
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save, pre_save, post_delete
from django.utils import timezone
from django.core.files.base import ContentFile

from audit.models.log import AuditLog
from audit.models.policy import AuditPolicy
from borrowers.models.credit_check_log import CreditCheckLog
from groups.models.debtor_group import DebtorGroup
from groups.models.debtor_group_member import DebtorGroupMember
from loan_agreements.models.loan_agreement import LoanAgreement
from loan_applications.models.loan_application import LoanApplication
from notifications.models.notification import Notification
from notifications.models.notification_log import NotificationLog
from payments.models.payment_transaction import PaymentTransaction
from payments.models.penalty_transaction import PenaltyTransaction
from users.enums.base import UserRole, UserStatus
from users.models import User as UserModel
from users.models.user_security_settings import UserSecuritySettings
from users.models.login_session import LoginSession
from users.models.otp_request import OtpRequest
from users.models.security_log import SecurityLog
from users.models.user_activity import UserActivity
from users.models.blacklisted_token import BlacklistedAccessToken
from borrowers.models.borrower import Borrower
from debts.models.debt import Debt
from debts.models.forgiveness_log import ForgivenessLog
from debts.models.interest_rate_change_log import InterestRateChangeLog
from payment_methods.models.payment_method import PaymentMethod
from payment_methods.models.payment_method_stat import PaymentMethodStat
from system_settings.models.system_setting import SystemSetting, SettingType
from users.signals.User import user_post_delete, user_post_save, user_pre_save
from system_settings.signals.base import system_setting_post_save

# Try to use Faker; if not available, fallback to random generators
try:
    from faker import Faker
    fake = Faker()
    FAKER_AVAILABLE = True
except ImportError:
    FAKER_AVAILABLE = False
    fake = None

User = get_user_model()

class Command(BaseCommand):
    help = 'Seed the database with test data for Collectly'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing data before seeding',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        # --- DISABLE USER SIGNALS ---
        pre_save.disconnect(user_pre_save, sender=User)
        post_save.disconnect(user_post_save, sender=User)
        post_delete.disconnect(user_post_delete, sender=User)

        # --- DISABLE SYSTEM SETTING SIGNAL (avoids cache.delete_pattern errors) ---
        post_save.disconnect(system_setting_post_save, sender=SystemSetting)

        try:
            if options['clear']:
                self.stdout.write('Clearing existing data...')
                self.clear_data()

            self.stdout.write('Starting seed...')
            self.create_users()
            self.create_borrowers()
            self.create_groups()
            self.create_debts()
            self.create_payment_methods()
            self.create_payments()
            self.create_penalties()
            self.create_loan_agreements()
            self.create_loan_applications()
            self.create_credit_checks()
            self.create_forgiveness_logs()
            self.create_interest_rate_changes()
            self.create_notifications()
            self.create_notification_logs()
            self.create_system_settings()
            self.create_audit_policy()
            self.create_audit_logs()
            self.create_security_logs()
            self.create_user_activities()
            self.create_login_sessions()
            self.create_otp_requests()
            self.create_blacklisted_tokens()

            self.stdout.write(self.style.SUCCESS('Seed completed successfully!'))
            self.print_login_credentials()
        finally:
            # Reconnect signals
            pre_save.connect(user_pre_save, sender=User)
            post_save.connect(user_post_save, sender=User)
            post_delete.connect(user_post_delete, sender=User)
            post_save.connect(system_setting_post_save, sender=SystemSetting)

    def clear_data(self):
        """Delete all data from all models (order matters for FK)."""
        models = [
            BlacklistedAccessToken,
            OtpRequest,
            LoginSession,
            UserActivity,
            SecurityLog,
            AuditLog,
            AuditPolicy,
            SystemSetting,
            NotificationLog,
            Notification,
            LoanApplication,
            LoanAgreement,
            ForgivenessLog,
            InterestRateChangeLog,
            CreditCheckLog,
            PenaltyTransaction,
            PaymentTransaction,
            PaymentMethodStat,
            PaymentMethod,
            DebtorGroupMember,
            DebtorGroup,
            Debt,
            Borrower,
            User,
        ]
        for model in models:
            model.objects.all().delete()
        self.stdout.write('All data cleared.')

    # ---------- Helper methods ----------
    def random_date(self, start_date=None, end_date=None):
        """Return a random datetime between start_date and end_date (inclusive)."""
        if not start_date:
            start_date = timezone.now() - timedelta(days=365)
        if not end_date:
            end_date = timezone.now()

        if start_date > end_date:
            start_date, end_date = end_date, start_date

        delta = end_date - start_date
        int_delta = (delta.days * 24 * 60 * 60) + delta.seconds

        if int_delta <= 0:
            return start_date

        random_second = random.randrange(int_delta)
        return start_date + timedelta(seconds=random_second)

    def random_decimal(self, min_val, max_val, places=2):
        min_float = float(min_val)
        max_float = float(max_val)
        return Decimal(random.uniform(min_float, max_float)).quantize(Decimal(f'1e-{places}'))

    def random_phone(self):
        if FAKER_AVAILABLE:
            return fake.phone_number()
        return f"09{random.randint(100000000, 999999999)}"

    def random_email(self, name=None):
        if FAKER_AVAILABLE:
            return fake.email()
        if name:
            return f"{name.lower().replace(' ', '.')}@example.com"
        return f"user{random.randint(1,999)}@example.com"

    # ---------- Core seed methods ----------
    def create_users(self):
        self.stdout.write('Creating users...')
        roles = [
            (UserRole.ADMIN, 'admin', 'password123'),
            (UserRole.MANAGER, 'manager1', 'password123'),
            (UserRole.COLLECTOR, 'collector1', 'password123'),
            (UserRole.STAFF, 'staff1', 'password123'),
            (UserRole.VIEWER, 'viewer1', 'password123'),
            (UserRole.CUSTOMER, 'customer1', 'password123'),
        ]

        for role_value, username, password in roles:
            if not User.objects.filter(username=username).exists():
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    email=f"{username}@collectly.test",
                    first_name=fake.first_name() if FAKER_AVAILABLE else username.capitalize(),
                    last_name=fake.last_name() if FAKER_AVAILABLE else 'User',
                    user_type=role_value,
                    status=UserStatus.ACTIVE,
                )
                UserSecuritySettings.objects.get_or_create(user=user)
                self.stdout.write(f"  Created {role_value}: {username} / {password}")
            else:
                self.stdout.write(f"  User {username} already exists, skipping.")

        # Create additional random users (10)
        role_choices = [UserRole.STAFF, UserRole.COLLECTOR, UserRole.VIEWER, UserRole.CUSTOMER]
        for i in range(10):
            first = fake.first_name() if FAKER_AVAILABLE else f"User{i}"
            last = fake.last_name() if FAKER_AVAILABLE else f"Last{i}"
            username = f"{first.lower()}.{last.lower()}{random.randint(1,99)}"
            if not User.objects.filter(username=username).exists():
                role = random.choice(role_choices)
                user = User.objects.create_user(
                    username=username,
                    password='password123',
                    email=f"{username}@example.com",
                    first_name=first,
                    last_name=last,
                    user_type=role,
                    status=UserStatus.ACTIVE,
                )
                UserSecuritySettings.objects.get_or_create(user=user)
                self.stdout.write(f"  Created extra user: {username} ({role})")

    def create_borrowers(self):
        self.stdout.write('Creating borrowers...')
        self.borrowers = []
        for i in range(25):
            name = fake.name() if FAKER_AVAILABLE else f"Borrower {i}"
            email = self.random_email(name)
            while Borrower.objects.filter(email=email).exists():
                email = self.random_email(name)
            borrower = Borrower.objects.create(
                name=name,
                contact=self.random_phone(),
                email=email,
                address=fake.address() if FAKER_AVAILABLE else f"{random.randint(1,999)} Main St",
                notes=fake.text(max_nb_chars=100) if FAKER_AVAILABLE else None,
                credit_rating=random.choice(['Excellent', 'Good', 'Fair', 'Poor']),
                created_at=self.random_date(),
            )
            self.borrowers.append(borrower)
        self.stdout.write(f"  Created {len(self.borrowers)} borrowers.")

    def create_groups(self):
        self.stdout.write('Creating debtor groups...')
        group_names = ['VIP', 'High-Risk', 'Corporate', 'Standard', 'Priority']
        self.groups = []
        for name in group_names:
            group, _ = DebtorGroup.objects.get_or_create(
                name=name,
                defaults={
                    'description': f"Group for {name} borrowers",
                    'color': random.choice(['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6']),
                }
            )
            self.groups.append(group)
        for borrower in self.borrowers:
            group = random.choice(self.groups)
            DebtorGroupMember.objects.get_or_create(
                group=group,
                debtor=borrower,
                defaults={'assigned_at': self.random_date()}
            )
        self.stdout.write(f"  Created {len(self.groups)} groups, assigned members.")

    def create_debts(self):
        self.stdout.write('Creating debts...')
        self.debts = []
        statuses = ['active', 'paid', 'overdue', 'defaulted']
        for borrower in self.borrowers:
            num_debts = random.randint(1, 3)
            for _ in range(num_debts):
                total = self.random_decimal(1000, 50000)
                paid = self.random_decimal(0, total * Decimal('0.8'))
                remaining = total - paid
                if remaining < 0:
                    remaining = Decimal('0.00')
                due_date = self.random_date(start_date=timezone.now() - timedelta(days=60),
                                            end_date=timezone.now() + timedelta(days=90)).date()
                status = random.choices(statuses, weights=[0.5, 0.2, 0.2, 0.1])[0]
                if status == 'paid':
                    paid = total
                    remaining = Decimal('0.00')
                debt = Debt.objects.create(
                    borrower=borrower,
                    name=f"Loan for {borrower.name}" if FAKER_AVAILABLE else f"Debt-{random.randint(100,999)}",
                    total_amount=total,
                    paid_amount=paid,
                    remaining_amount=remaining,
                    due_date=due_date,
                    status=status,
                    interest_rate=self.random_decimal(5, 25) if random.random() > 0.3 else None,
                    penalty_rate=self.random_decimal(1, 5) if random.random() > 0.5 else None,
                    interest_calculation_period=random.choice(['per_annum', 'per_month']),
                    created_at=self.random_date(),
                    updated_at=self.random_date(),
                )
                self.debts.append(debt)
        self.stdout.write(f"  Created {len(self.debts)} debts.")

    def create_payment_methods(self):
        self.stdout.write('Creating payment methods...')
        methods = [
            ('Cash', 'Cash payment', 'DollarSign', True),
            ('Bank Transfer', 'Transfer from bank account', 'Building', False),
            ('GCash', 'Mobile payment via GCash', 'Smartphone', False),
            ('PayPal', 'Online payment via PayPal', 'CreditCard', False),
            ('Check', 'Payment by check', 'FileText', False),
        ]
        self.payment_methods = []
        for name, desc, icon, default in methods:
            method, _ = PaymentMethod.objects.get_or_create(
                name=name,
                defaults={'description': desc, 'icon': icon, 'is_default': default}
            )
            self.payment_methods.append(method)
            PaymentMethodStat.objects.get_or_create(method=method)
        self.stdout.write(f"  Created {len(self.payment_methods)} payment methods.")

    def create_payments(self):
        self.stdout.write('Creating payments...')
        for debt in self.debts:
            num_payments = random.randint(0, 3)
            if debt.status == 'paid':
                num_payments = random.randint(1, 3)
            for _ in range(num_payments):
                amount = self.random_decimal(100, min(debt.total_amount, 10000))
                if debt.paid_amount + amount > debt.total_amount:
                    amount = debt.total_amount - debt.paid_amount
                    if amount <= 0:
                        continue
                payment_date = self.random_date(start_date=debt.created_at,
                                                end_date=timezone.now()).date()
                PaymentTransaction.objects.create(
                    debt=debt,
                    method=random.choice(self.payment_methods),
                    amount=amount,
                    payment_date=payment_date,
                    reference=f"REF-{uuid.uuid4().hex[:8].upper()}",
                    notes=fake.sentence() if FAKER_AVAILABLE else None,
                    recorded_at=self.random_date(start_date=debt.created_at, end_date=timezone.now()),
                )
                debt.paid_amount += amount
                debt.remaining_amount = debt.total_amount - debt.paid_amount
                if debt.remaining_amount < 0:
                    debt.remaining_amount = Decimal('0.00')
                debt.save()
        self.stdout.write(f"  Created payments.")

    def create_penalties(self):
        self.stdout.write('Creating penalties...')
        for debt in self.debts:
            if debt.status in ['overdue', 'defaulted'] and random.random() > 0.5:
                PenaltyTransaction.objects.create(
                    debt=debt,
                    amount=self.random_decimal(50, 500),
                    penalty_date=timezone.now().date() - timedelta(days=random.randint(1, 30)),
                    reason="Late payment penalty",
                    is_auto=random.choice([True, False]),
                    created_at=self.random_date(),
                )
        self.stdout.write(f"  Created penalties.")

    def create_loan_agreements(self):
        self.stdout.write('Creating loan agreements...')
        for debt in random.sample(self.debts, min(10, len(self.debts))):
            if random.random() > 0.4:
                agreement = LoanAgreement.objects.create(
                    debt=debt,
                    status=random.choice(['draft', 'signed']),
                    agreement_date=self.random_date(start_date=debt.created_at).date(),
                    lender_name=fake.company() if FAKER_AVAILABLE else "Collectly Lending",
                    terms_text=fake.text(max_nb_chars=200) if FAKER_AVAILABLE else "Standard terms...",
                    signed_at=timezone.now() if random.random() > 0.5 else None,
                    signed_by=fake.name() if FAKER_AVAILABLE else "John Doe",
                    principal_amount=debt.total_amount,
                    interest_rate=debt.interest_rate,
                    penalty_rate=debt.penalty_rate,
                    due_date=debt.due_date,
                    purpose=fake.sentence() if FAKER_AVAILABLE else "Personal loan",
                )
                # Skip file upload to avoid Cloudinary errors
        self.stdout.write(f"  Created loan agreements.")

    def create_loan_applications(self):
        self.stdout.write('Creating loan applications...')
        for borrower in random.sample(self.borrowers, min(10, len(self.borrowers))):
            if random.random() > 0.5:
                LoanApplication.objects.create(
                    debtor=borrower,
                    debtor_name=borrower.name,
                    debtor_contact=borrower.contact,
                    debtor_email=borrower.email,
                    debtor_address=borrower.address,
                    requested_amount=self.random_decimal(5000, 100000),
                    purpose=fake.sentence() if FAKER_AVAILABLE else "Need funds",
                    proposed_due_date=(timezone.now() + timedelta(days=random.randint(30, 180))).date(),
                    interest_rate=self.random_decimal(5, 25),
                    status=random.choice(['pending', 'approved', 'rejected']),
                    approved_at=timezone.now() if random.random() > 0.5 else None,
                    rejected_at=timezone.now() if random.random() > 0.5 else None,
                    approved_by=fake.name() if FAKER_AVAILABLE else "Manager",
                    rejection_reason=fake.sentence() if FAKER_AVAILABLE else None,
                )
        self.stdout.write(f"  Created loan applications.")

    def create_credit_checks(self):
        self.stdout.write('Creating credit checks...')
        for borrower in random.sample(self.borrowers, min(15, len(self.borrowers))):
            for _ in range(random.randint(1, 3)):
                score = random.randint(300, 850)
                CreditCheckLog.objects.create(
                    debtor=borrower,
                    score=score,
                    risk_level=random.choice(['Low', 'Medium', 'High']),
                    remarks=fake.sentence() if FAKER_AVAILABLE else None,
                    date_checked=self.random_date(),
                    performed_by=None,
                    external_reference=f"CR-{uuid.uuid4().hex[:10].upper()}",
                )
        self.stdout.write(f"  Created credit checks.")

    def create_forgiveness_logs(self):
        self.stdout.write('Creating forgiveness logs...')
        for debt in random.sample(self.debts, min(5, len(self.debts))):
            if debt.remaining_amount > 100:
                forgiven = self.random_decimal(100, debt.remaining_amount)
                ForgivenessLog.objects.create(
                    debt=debt,
                    borrower=debt.borrower,
                    amount_forgiven=forgiven,
                    previous_total_amount=debt.total_amount,
                    new_total_amount=debt.total_amount - forgiven,
                    reason=fake.sentence() if FAKER_AVAILABLE else "Goodwill",
                    created_by=fake.name() if FAKER_AVAILABLE else "Admin",
                    status=random.choice(['pending', 'approved', 'rejected']),
                    approved_by=fake.name() if FAKER_AVAILABLE else "Manager",
                    approved_at=timezone.now() if random.random() > 0.5 else None,
                )
        self.stdout.write(f"  Created forgiveness logs.")

    def create_interest_rate_changes(self):
        self.stdout.write('Creating interest rate changes...')
        for _ in range(10):
            debt = random.choice(self.debts) if random.random() > 0.5 else None
            InterestRateChangeLog.objects.create(
                setting_key=f"loan_{debt.id}" if debt else "default_interest_rate",
                old_value=self.random_decimal(5, 15) if random.random() > 0.5 else None,
                new_value=self.random_decimal(5, 15),
                changed_by=fake.name() if FAKER_AVAILABLE else "system",
                reason=fake.sentence() if FAKER_AVAILABLE else None,
                loan=debt,
            )
        self.stdout.write(f"  Created interest rate changes.")

    def create_notifications(self):
        self.stdout.write('Creating notifications...')
        for debt in random.sample(self.debts, min(15, len(self.debts))):
            Notification.objects.create(
                debt=debt,
                title=f"Notification for {debt.name}",
                message=fake.text(max_nb_chars=150) if FAKER_AVAILABLE else "Reminder",
                type=random.choice(['error', 'info', 'reminder', 'overdue', 'payment_confirmation']),
                is_read=random.choice([True, False]),
                scheduled_for=timezone.now() + timedelta(days=random.randint(1, 10)) if random.random() > 0.5 else None,
            )
        self.stdout.write(f"  Created notifications.")

    def create_notification_logs(self):
        self.stdout.write('Creating notification logs...')
        for _ in range(30):
            NotificationLog.objects.create(
                recipient_email=self.random_email(),
                subject=f"Subject {random.randint(1,100)}",
                payload=fake.text(max_nb_chars=200) if FAKER_AVAILABLE else "Content",
                status=random.choice(['queued', 'sent', 'failed', 'resend']),
                error_message=fake.sentence() if FAKER_AVAILABLE and random.random() > 0.7 else None,
                retry_count=random.randint(0, 3),
                resend_count=random.randint(0, 2),
                sent_at=timezone.now() if random.random() > 0.5 else None,
                last_error_at=timezone.now() if random.random() > 0.7 else None,
            )
        self.stdout.write(f"  Created notification logs.")

    def create_system_settings(self):
        self.stdout.write('Creating system settings...')
        settings_data = [
            ('company_name', 'Collectly Inc.', SettingType.GENERAL, 'Company name'),
            ('default_interest_rate', '10.00', SettingType.LOANS, 'Default interest rate'),
            ('default_penalty_rate', '2.00', SettingType.LOANS, 'Default penalty rate'),
            ('enable_sms_notifications', 'true', SettingType.NOTIFICATIONS, 'Enable SMS'),
            ('enable_email_notifications', 'true', SettingType.NOTIFICATIONS, 'Enable email'),
            ('audit_retention_days', '365', SettingType.AUDIT_SECURITY, 'Audit log retention'),
            ('sync_interval_minutes', '5', SettingType.INTEGRATIONS, 'Sync interval'),
        ]
        for key, value, stype, desc in settings_data:
            SystemSetting.objects.get_or_create(
                setting_type=stype,
                key=key,
                defaults={'value': value, 'description': desc, 'is_public': random.choice([True, False])}
            )
        self.stdout.write(f"  Created system settings.")

    def create_audit_policy(self):
        self.stdout.write('Creating audit policy...')
        # Do NOT specify 'id' – let Django auto‑generate it to avoid immutability check on creation.
        AuditPolicy.objects.get_or_create(
            defaults={'retention_years': 5, 'immutable': True}
        )
        self.stdout.write(f"  Created audit policy.")

    def create_audit_logs(self):
        self.stdout.write('Creating audit logs...')
        action_types = [c[0] for c in AuditLog.ACTION_TYPES]
        users = list(User.objects.all())
        for _ in range(250):
            user = random.choice([None] + users)
            model_name = random.choice(['Borrower', 'Debt', 'PaymentTransaction', 'User', 'LoanAgreement'])
            object_id = str(random.randint(1, 100))
            changes = {'field': random.choice(['name', 'amount', 'status']), 'old': 'old', 'new': 'new'} if random.random() > 0.5 else {}
            AuditLog.objects.create(
                event_id=uuid.uuid4(),
                user=user,
                action_type=random.choice(action_types),
                model_name=model_name,
                object_id=object_id,
                changes=changes,
                ip_address=f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
                user_agent=fake.user_agent() if FAKER_AVAILABLE else "Mozilla/5.0",
                is_suspicious=random.choice([True, False]),
                suspicious_reason=fake.sentence() if random.random() > 0.9 else None,
                timestamp=self.random_date(),
            )
        self.stdout.write(f"  Created 250 audit logs.")

    def create_security_logs(self):
        self.stdout.write('Creating security logs...')
        users = list(User.objects.all())
        event_types = [c[0] for c in SecurityLog.EVENT_TYPES]
        for _ in range(50):
            user = random.choice(users)
            SecurityLog.objects.create(
                user=user,
                event_type=random.choice(event_types),
                ip_address=f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
                user_agent=fake.user_agent() if FAKER_AVAILABLE else "Mozilla/5.0",
                created_at=self.random_date(),
                updated_at=timezone.now(),
                details=fake.sentence() if FAKER_AVAILABLE else None,
            )
        self.stdout.write(f"  Created security logs.")

    def create_user_activities(self):
        self.stdout.write('Creating user activities...')
        users = list(User.objects.all())
        action_types = [c[0] for c in UserActivity.ACTION_TYPES]
        for _ in range(40):
            user = random.choice(users)
            UserActivity.objects.create(
                user=user,
                action=random.choice(action_types),
                description=fake.sentence() if FAKER_AVAILABLE else None,
                ip_address=f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
                user_agent=fake.user_agent() if FAKER_AVAILABLE else "Mozilla/5.0",
                timestamp=self.random_date(),
                location=fake.city() if FAKER_AVAILABLE else None,
                metadata={'key': 'value'} if random.random() > 0.5 else {},
            )
        self.stdout.write(f"  Created user activities.")

    def create_login_sessions(self):
        self.stdout.write('Creating login sessions...')
        users = list(User.objects.all())
        for user in random.sample(users, min(10, len(users))):
            for _ in range(random.randint(1, 2)):
                LoginSession.objects.create(
                    id=uuid.uuid4(),
                    user=user,
                    device_name=fake.word() if FAKER_AVAILABLE else "Device",
                    ip_address=f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
                    created_at=self.random_date(),
                    last_used=timezone.now(),
                    expires_at=timezone.now() + timedelta(days=7),
                    is_active=random.choice([True, False]),
                    refresh_token=uuid.uuid4().hex,
                    access_token=uuid.uuid4().hex,
                )
        self.stdout.write(f"  Created login sessions.")

    def create_otp_requests(self):
        self.stdout.write('Creating OTP requests...')
        users = list(User.objects.all())
        for _ in range(20):
            user = random.choice([None] + users)
            OtpRequest.objects.create(
                user=user,
                otp_code=f"{random.randint(100000, 999999)}",
                email=user.email if user else self.random_email(),
                phone=self.random_phone() if random.random() > 0.5 else None,
                created_at=self.random_date(),
                expires_at=timezone.now() + timedelta(minutes=5),
                is_used=random.choice([True, False]),
                attempt_count=random.randint(0, 3),
                type=random.choice(['email', 'phone']),
                is_email_delivered=random.choice([True, False]),
                is_phone_delivered=random.choice([True, False]),
            )
        self.stdout.write(f"  Created OTP requests.")

    def create_blacklisted_tokens(self):
        self.stdout.write('Creating blacklisted tokens...')
        users = list(User.objects.all())
        for _ in range(10):
            user = random.choice(users)
            BlacklistedAccessToken.objects.create(
                jti=uuid.uuid4().hex,
                user=user,
                expires_at=timezone.now() + timedelta(days=1),
                created_at=self.random_date(),
            )
        self.stdout.write(f"  Created blacklisted tokens.")

    def print_login_credentials(self):
        self.stdout.write(self.style.SUCCESS('\n=== Test Login Credentials ==='))
        credentials = [
            (UserRole.ADMIN, 'admin', 'password123'),
            (UserRole.MANAGER, 'manager1', 'password123'),
            (UserRole.COLLECTOR, 'collector1', 'password123'),
            (UserRole.STAFF, 'staff1', 'password123'),
            (UserRole.VIEWER, 'viewer1', 'password123'),
            (UserRole.CUSTOMER, 'customer1', 'password123'),
        ]
        for role, user, pw in credentials:
            self.stdout.write(f"  {role.capitalize()}: {user} / {pw}")
        self.stdout.write('  (Additional random users also have password: password123)')
        self.stdout.write('====================================\n')