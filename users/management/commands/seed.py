# seed.py
import random
import string
from decimal import Decimal
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import transaction

from analytics.models.activation_trend import ActivationTrend
from licensing.models.telemetry import Telemetry
from products.models.license_key_batch import LicenseKeyBatch
from settings.models.config import SystemLogo

try:
    from faker import Faker

    fake = Faker()
except ImportError:
    fake = None

# ===== Correct imports per app =====
from analytics.models.license_usage import LicenseUsage
from users.models import (
    User,
    LoginSession,
    OtpRequest,
    LoginCheckpoint,
    SecurityLog,
    UserActivity,
    UserSecuritySettings,
    BlacklistedAccessToken,
    Organization,
)
from billing.models import SubscriptionPlan, Transaction
from licensing.models import License, Activation, DeviceBinding
from audit.models import AuditLog, AuditPolicy
from notifications.models import NotificationChannel, NotificationEvent
from products.models import (
    SoftwareProduct,
    LicenseProduct,
    ProductBundle,
    BundleItem,
    SoldLicense,
    ActivationRecord,
)
from settings.models import SystemSetting

User = get_user_model()


class Command(BaseCommand):
    help = "Seeds the database with sample data for development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing data before seeding (except superuser).",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            self.clear_data()
        self.seed_data()
        self.stdout.write(self.style.SUCCESS("Database seeded successfully!"))

    def clear_data(self):
        """Clear all tables in order of dependencies."""
        self.stdout.write("Clearing existing data...")
        models_to_clear = [
            BlacklistedAccessToken,
            LoginSession,
            OtpRequest,
            LoginCheckpoint,
            SecurityLog,
            UserActivity,
            UserSecuritySettings,
            ActivationRecord,
            SoldLicense,
            LicenseKeyBatch,
            BundleItem,
            ProductBundle,
            DeviceBinding,
            Telemetry,
            LicenseUsage,
            Activation,
            License,
            AuditLog,
            NotificationEvent,
            NotificationChannel,
            Transaction,
            SubscriptionPlan,
            Organization,
            SystemSetting,
            SystemLogo,
        ]
        for model in reversed(models_to_clear):
            try:
                model.objects.all().delete()
                self.stdout.write(f"  Cleared {model.__name__}")
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f"  Could not clear {model.__name__}: {e}")
                )

    def seed_data(self):
        self.stdout.write("Seeding data...")
        with transaction.atomic():
            self.create_users()
            self.create_subscription_plans()
            self.create_software_products()
            self.create_license_products()
            self.create_bundles()
            self.create_licenses()
            self.create_activations()
            self.create_device_bindings()
            self.create_telemetry()
            self.create_usage_trends()
            self.create_audit_data()
            self.create_notification_data()
            self.create_settings()
            self.create_user_extras()

    # ----------------------------------------------------------------------
    # Helper methods
    # ----------------------------------------------------------------------
    def random_string(self, length=10):
        return "".join(random.choices(string.ascii_letters + string.digits, k=length))

    def random_license_key(self):
        return "".join(random.choices(string.digits, k=16))

    def random_date(self, days_ago_min=0, days_ago_max=365):
        days = random.randint(days_ago_min, days_ago_max)
        return timezone.now() - timedelta(days=days)

    def random_future_date(self, days_min=1, days_max=365):
        days = random.randint(days_min, days_max)
        return timezone.now() + timedelta(days=days)

    def get_or_create_user(self, username, **kwargs):
        user, _ = User.objects.get_or_create(username=username, defaults=kwargs)
        return user

    # ----------------------------------------------------------------------
    # Seed functions
    # ----------------------------------------------------------------------
    def create_users(self):
        self.stdout.write("  Creating users...")
        self.get_or_create_user(
            "admin",
            email="admin@example.com",
            is_staff=True,
            is_superuser=True,
            first_name="Admin",
            last_name="User",
            status="active",
            user_type="admin",
        )
        self.get_or_create_user(
            "staff",
            email="staff@example.com",
            is_staff=True,
            is_superuser=False,
            first_name="Staff",
            last_name="User",
            status="active",
            user_type="staff",
        )
        for i in range(5):
            username = f"customer_{i}"
            email = f"customer{i}@example.com"
            self.get_or_create_user(
                username,
                email=email,
                is_staff=False,
                is_superuser=False,
                first_name=fake.first_name() if fake else f"First{i}",
                last_name=fake.last_name() if fake else f"Last{i}",
                status="active",
                user_type="customer",
            )
        self.stdout.write("  Users created.")

    def create_subscription_plans(self):
        self.stdout.write("  Creating subscription plans...")
        plans = [
            {
                "plan_code": "basic",
                "features": {"max_users": 5, "storage": "10GB"},
                "price": Decimal("9.99"),
                "billing_cycle": "monthly",
            },
            {
                "plan_code": "pro",
                "features": {"max_users": 50, "storage": "100GB"},
                "price": Decimal("49.99"),
                "billing_cycle": "monthly",
            },
            {
                "plan_code": "enterprise",
                "features": {"max_users": 1000, "storage": "1TB"},
                "price": Decimal("199.99"),
                "billing_cycle": "annual",
            },
        ]
        for plan_data in plans:
            plan, created = SubscriptionPlan.objects.get_or_create(
                plan_code=plan_data["plan_code"], defaults=plan_data
            )
            if created:
                self.stdout.write(f"    Created plan: {plan.plan_code}")

    def create_software_products(self):
        self.stdout.write("  Creating software products...")
        software_list = [
            {
                "name": "Inventory Pro",
                "slug": "inventory-pro",
                "publisher": "Acme Inc.",
                "description": "Advanced inventory management.",
            },
            {
                "name": "Sales Tracker",
                "slug": "sales-tracker",
                "publisher": "Beta Corp.",
                "description": "Real-time sales analytics.",
            },
        ]
        for sw in software_list:
            obj, created = SoftwareProduct.objects.get_or_create(
                slug=sw["slug"], defaults=sw
            )
            if created:
                self.stdout.write(f"    Created software: {obj.name}")

    def create_license_products(self):
        self.stdout.write("  Creating license products...")
        software = SoftwareProduct.objects.first()
        if not software:
            return
        products = [
            {
                "name": "Personal License",
                "slug": "personal-license",
                "product_type": "perpetual",
                "tier": "basic",
                "base_price": Decimal("99.00"),
                "sku": f"LIC-PERS-{self.random_string(4)}",
                "validity_days": 365,
                "max_activations": 1,
            },
            {
                "name": "Business License",
                "slug": "business-license",
                "product_type": "subscription",
                "tier": "standard",
                "base_price": Decimal("299.00"),
                "sku": f"LIC-BUS-{self.random_string(4)}",
                "validity_days": 365,
                "max_activations": 5,
            },
            {
                "name": "Enterprise License",
                "slug": "enterprise-license",
                "product_type": "enterprise",
                "tier": "enterprise",
                "base_price": Decimal("999.00"),
                "sku": f"LIC-ENT-{self.random_string(4)}",
                "validity_days": None,
                "max_activations": 20,
            },
        ]
        for prod in products:
            prod["software"] = software
            obj, created = LicenseProduct.objects.get_or_create(
                slug=prod["slug"], defaults=prod
            )
            if created:
                self.stdout.write(f"    Created license product: {obj.name}")

    def create_bundles(self):
        self.stdout.write("  Creating product bundles...")
        license_products = list(LicenseProduct.objects.all())
        if len(license_products) < 2:
            return
        bundle, created = ProductBundle.objects.get_or_create(
            slug="starter-bundle",
            defaults={
                "name": "Starter Bundle",
                "description": "Bundle of essential licenses.",
                "bundle_price": Decimal("150.00"),
                "individual_price": Decimal("200.00"),
                "status": "active",
            },
        )
        if created:
            for lp in license_products[:2]:
                BundleItem.objects.get_or_create(bundle=bundle, product=lp, quantity=1)
            self.stdout.write("    Created bundle: Starter Bundle")

    def create_licenses(self):
        self.stdout.write("  Creating licenses...")
        plan = SubscriptionPlan.objects.filter(plan_code="pro").first()
        users = User.objects.filter(user_type="customer")[:3]
        if not users:
            users = [User.objects.first()]

        # licensing.License entries
        license_types = ["trial", "personal", "business"]
        statuses = ["active", "expired", "active", "suspended"]
        for i in range(10):
            license_type = random.choice(license_types)
            status = random.choice(statuses)
            expiry = self.random_future_date(30, 365)
            if status == "expired":
                expiry = self.random_date(1, 30)
            license_obj = License.objects.create(
                license_key=self.random_license_key(),
                product_code="inventory-pro",
                license_type=license_type,
                status=status,
                subscription_plan=plan if license_type != "trial" else None,
                activation_date=(
                    timezone.now() if random.choice([True, False]) else None
                ),
                expiry_date=expiry,
                grace_period=7,
                max_devices=random.randint(1, 5),
                action_type=random.choice(["issue", "activate", "renew"]),
                features=[],
                limits={},
                customer_email=(
                    random.choice([u.email for u in users]) if users else None
                ),
                customer_name=(
                    random.choice([u.get_full_name() for u in users]) if users else None
                ),
            )
            if i % 3 == 0:
                self.stdout.write(f"    Created license: {license_obj.license_key}")

        # products.SoldLicense
        license_products = list(LicenseProduct.objects.all())
        for lp in license_products:
            for _ in range(3):
                customer = random.choice(users) if users else None
                sold = SoldLicense.objects.create(
                    license_key=self.random_license_key(),
                    product=lp,
                    customer_name=customer.get_full_name() if customer else "Test User",
                    customer_email=customer.email if customer else "test@example.com",
                    sold_price=lp.base_price * Decimal(random.uniform(0.8, 1.2)),
                    currency="USD",
                    issue_date=timezone.now() - timedelta(days=random.randint(0, 30)),
                    expiry_date=self.random_future_date(30, 365),
                    max_activations=random.randint(1, 5),
                    status=random.choice(["available", "sold", "activated"]),
                )
                if random.choice([True, False]):
                    ActivationRecord.objects.create(
                        license=sold,
                        device_id=f"device-{self.random_string(8)}",
                        device_name=fake.hostname() if fake else "test-device",
                        ip_address=fake.ipv4() if fake else "192.168.1.1",
                        user_agent="Mozilla/5.0",
                        is_active=True,
                    )
        self.stdout.write("  Licenses created.")

    def create_activations(self):
        self.stdout.write("  Creating activations...")
        licenses = License.objects.all()[:5]
        for lic in licenses:
            Activation.objects.get_or_create(
                license=lic,
                defaults={
                    "activation_key": f"ACT-{self.random_string(12)}",
                    "status": random.choice(["active", "trial", "expired"]),
                    "license_type": random.choice(["personal", "business"]),
                    "activated_at": timezone.now()
                    - timedelta(days=random.randint(0, 30)),
                    "expires_at": self.random_future_date(30, 365),
                    "last_seen": timezone.now() - timedelta(days=random.randint(0, 5)),
                },
            )
        self.stdout.write("  Activations created.")

    def create_device_bindings(self):
        self.stdout.write("  Creating device bindings...")
        licenses = License.objects.all()[:5]
        for lic in licenses:
            for _ in range(random.randint(1, 2)):
                DeviceBinding.objects.get_or_create(
                    license=lic,
                    device_id=f"dev-{self.random_string(8)}",
                    defaults={
                        "device_info": {"model": "Dell XPS", "os": "Windows 11"},
                        "binding_status": random.choice(["bound", "pending"]),
                        "action_type": "bind",
                    },
                )
        self.stdout.write("  Device bindings created.")

    def create_telemetry(self):
        self.stdout.write("  Creating telemetry...")
        licenses = License.objects.all()[:5]
        for lic in licenses:
            for _ in range(random.randint(1, 3)):
                Telemetry.objects.create(
                    device_id=f"device-{self.random_string(6)}",
                    license_key=lic.license_key if lic else None,
                    event_type=random.choice(["app_start", "feature_used", "sync"]),
                    event_data={"key": "value"},
                    app_version="1.0.0",
                    platform=random.choice(["windows", "macos", "linux"]),
                    ip_address=fake.ipv4() if fake else "127.0.0.1",
                )
        self.stdout.write("  Telemetry created.")

    def create_usage_trends(self):
        self.stdout.write("  Creating usage trends...")
        # LicenseUsage
        licenses = License.objects.all()[:5]
        for lic in licenses:
            LicenseUsage.objects.create(
                license=lic,
                device_id=f"device-{self.random_string(6)}",
                usage_count=random.randint(0, 2000),
                last_check=timezone.now() - timedelta(hours=random.randint(0, 24)),
                geo_location=fake.city() if fake else "Manila",
                anomaly_score=0.0,
            )
        # ActivationTrend
        for _ in range(5):
            product_code = random.choice(["inventory-pro", "sales-tracker"])
            period = f"{timezone.now().year}-Q{random.randint(1,4)}"
            ActivationTrend.objects.create(
                product_code=product_code,
                period=period,
                activations=random.randint(10, 100),
                revocations=random.randint(0, 20),
            )
        self.stdout.write("  Usage trends created.")

    def create_audit_data(self):
        self.stdout.write('  Creating audit data...')
        # Ensure one AuditPolicy exists
        if not AuditPolicy.objects.exists():
            AuditPolicy.objects.create(retention_years=5, immutable=True)
        
        users = User.objects.all()[:5]
        for _ in range(10):
            user = random.choice(users) if users else None
            AuditLog.objects.create(
                user=user,
                action_type=random.choice(['create', 'update', 'delete', 'login', 'logout']),
                model_name=random.choice(['License', 'User', 'Activation']),
                object_id=str(random.randint(1, 100)),
                changes={'field': 'value'},
                ip_address=fake.ipv4() if fake else '127.0.0.1',
                user_agent='Mozilla/5.0',
                is_suspicious=random.choice([True, False]),
                suspicious_reason='Too many attempts' if random.choice([True, False]) else '',
            )
        self.stdout.write('  Audit data created.')

    def create_notification_data(self):
        self.stdout.write("  Creating notification data...")
        NotificationChannel.objects.get_or_create(
            channel_type="email",
            defaults={
                "config": {"host": "smtp.example.com", "port": 587},
                "is_active": True,
            },
        )
        licenses = License.objects.all()[:5]
        for lic in licenses:
            NotificationEvent.objects.create(
                event_type=random.choice(
                    ["license_expired", "license_activated", "policy_updated"]
                ),
                recipient=random.choice(["admin@example.com", "user@example.com"]),
                license=lic,
                payload={"message": "Test notification"},
                delivery_status=random.choice(["pending", "sent", "failed"]),
            )
        self.stdout.write("  Notification data created.")

    def create_settings(self):
        self.stdout.write("  Creating system settings...")
        SystemSetting.objects.get_or_create(
            setting_type="general",
            key="site_name",
            defaults={
                "value": "My Inventory System",
                "description": "Site name",
                "is_public": True,
            },
        )
        SystemLogo.objects.get_or_create(
            name="main",
            defaults={"logo": "logos/default.png", "description": "Main logo"},
        )
        self.stdout.write("  Settings created.")

    def create_user_extras(self):
        self.stdout.write("  Creating user extras...")
        users = User.objects.all()[:5]
        for user in users:
            UserSecuritySettings.objects.get_or_create(
                user=user,
                defaults={
                    "two_factor_enabled": random.choice([True, False]),
                    "recovery_email": user.email,
                    "alert_on_new_device": True,
                },
            )
            LoginSession.objects.create(
                user=user,
                device_name=fake.hostname() if fake else "Device",
                ip_address=fake.ipv4() if fake else "192.168.1.1",
                expires_at=timezone.now() + timedelta(days=7),
                refresh_token=f"refresh-{self.random_string(20)}",
                access_token=f"access-{self.random_string(20)}",
            )
            OtpRequest.objects.create(
                user=user,
                otp_code="123456",
                email=user.email,
                expires_at=timezone.now() + timedelta(minutes=5),
                type="email",
                is_email_delivered=True,
            )
            for _ in range(3):
                SecurityLog.objects.create(
                    user=user,
                    event_type=random.choice(["login", "logout", "password_change"]),
                    ip_address=fake.ipv4() if fake else "192.168.1.1",
                    user_agent="Mozilla/5.0",
                )
                UserActivity.objects.create(
                    user=user,
                    action=random.choice(["login", "update_profile", "view_logs"]),
                    description="Sample activity",
                    ip_address=fake.ipv4() if fake else "192.168.1.1",
                )
        self.stdout.write("  User extras created.")
