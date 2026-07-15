import uuid
from django.db import models

# Create your models here.
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from users.models.User import User
import uuid
from core import settings


class AuditLog(models.Model):
    ACTION_TYPES = (
        # Core CRUD
        ("create", "Create"),
        ("update", "Update"),
        ("partial_update", "Partial Update"),
        ("delete", "Delete"),
        ("read", "Read"),
        ("read_stats", "Read Stats"),
        ("status_change", "Status Change"),
        ("stats_read", "Stats Read"),
        ("read_grouped_config", "Read Grouped Config"),
        ("read_system_info", "Read System Info"),
        ("update_grouped_config", "Update Grouped Config"),
        # Authentication / Session
        ("login", "Login"),
        ("logout", "Logout"),
        ("logout_all", "Logout All"),
        ("login_failed", "Login Failed"),
        ("session_terminate", "Session Terminate"),
        ("password_change", "Password Change"),
        ("password_reset", "Password Reset"),
        # User / Role Management
        ("user_create", "User Create"),
        ("user_update", "User Update"),
        ("user_delete", "User Delete"),
        ("role_assign", "Role Assign"),
        ("role_revoke", "Role Revoke"),
        ("permission_update", "Permission Update"),
        # System / Config Management
        ("config_update", "Config Update"),
        ("settings_change", "Settings Change"),
        ("feature_toggle", "Feature Toggle"),
        # Notification / Activation
        ("notification_error", "Notification Error"),
        ("notification_update", "Notification Update"),
        ("notification_delete", "Notification Delete"),
        ("notification_create", "Notification Create"),
        ("notification_send", "Notification Send"),
        ("notification_read", "Notification Read"),
        
        ("activation_error", "Activation Error"),
        ("activation_create", "Activation Create"),
        ("activation_update", "Activation Update"),
        # Security / Monitoring
        ("suspicious_activity", "Suspicious Activity"),
        ("system_alert", "System Alert"),
        ("audit_export", "Audit Export"),
        ("2fa_initiated", "2 factor Initiated"),
        # ========== COLLECTLY-SPECIFIC ==========
        ("debt_create", "Debt Create"),
        ("debt_update", "Debt Update"),
        ("debt_delete", "Debt Delete"),
        ("debt_forgive", "Debt Forgiveness"),
        ("payment_create", "Payment Create"),
        ("payment_update", "Payment Update"),
        ("payment_delete", "Payment Delete"),
        ("penalty_create", "Penalty Create"),
        ("penalty_update", "Penalty Update"),
        ("penalty_delete", "Penalty Delete"),
        ("borrower_create", "Borrower Create"),
        ("borrower_update", "Borrower Update"),
        ("borrower_delete", "Borrower Delete"),
        ("loan_agreement_create", "Loan Agreement Create"),
        ("loan_agreement_signed", "Loan Agreement Signed"),
        ("loan_application_create", "Loan Application Create"),
        ("loan_application_approved", "Loan Application Approved"),
        ("loan_application_rejected", "Loan Application Rejected"),
        ("loan_application_submit", "Loan Application Submit"),
        ("group_create", "Group Create"),
        ("group_update", "Group Update"),
        ("group_delete", "Group Delete"),
        ("group_member_add", "Group Member Add"),
        ("group_member_remove", "Group Member Remove"),
        ("payment_method_create", "Payment Method Create"),
        ("payment_method_update", "Payment Method Update"),
        ("payment_method_delete", "Payment Method Delete"),
        ("credit_check_performed", "Credit Check Performed"),
        ("interest_rate_change", "Interest Rate Change"),
        ("sync_pull", "Sync Pull"),
        ("sync_push", "Sync Push"),
        ("print_receipt", "Print Receipt"),
        ("export_data", "Export Data"),
        ("import_data", "Import Data"),
        
        ("debt_create_from_application", "Debt Create From Application"),
        
        ("payment_apply", "Payment Apply"),
        
        ("interest_accrual", "Interest Accrual"),
        
        ("payment_confirm", "Payment Confirm"),
        
        ("debt_paid", "Debt Paid"),
        ("debt_status_auto_paid", "Debt Status Auto Paid"),
        ("sync_process_queue", "Sync Process Queue"),
        ("sync_queue_process_all", "Syn Queue Process All"),
        ("sync_receive", "Sync Receive"),
        ("sync_update", "Sync Update"),
        ("sync_status_updated", "Synce Status Updated"),
        ("sync_create", "Synce Create"),
    )

    event_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, null=True, blank=True)
    is_suspicious = models.BooleanField(default=False)
    suspicious_reason = models.CharField(max_length=255, null=True, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, editable=False)

    def clean(self):
        valid_actions = [choice[0] for choice in self.ACTION_TYPES]
        if self.action_type not in valid_actions:
            raise ValidationError(
                f"Invalid action_type '{self.action_type}'. Allowed values: {valid_actions}"
            )

    def save(self, *args, **kwargs):
        # enforce immutability
        if self.pk is not None:
            raise ValidationError(
                "AuditLog entries are immutable and cannot be updated."
            )
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"[{self.action_type}] {self.model_name} ({self.object_id}) by {self.user}"
        )
