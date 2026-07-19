# controls/urls/base.py
from django.urls import path
from controls.views import (
    TriggerInterestAccrualView,
    InterestAccrualStatusView,
    TriggerOverdueCorrectorView,
    OverdueCorrectorStatusView,
    TriggerOverdueUpdaterView,
    OverdueUpdaterStatusView,
    TriggerZeroBalanceFixerView,
    ZeroBalanceFixerStatusView,
    TriggerPenaltySchedulerView,
    PenaltySchedulerStatusView,
    OverdueStatusHealthView,
    ZeroBalanceHealthView,
    PenaltyHealthView,
    TriggerAuditCleanupView,
    AuditCleanupStatusView,
    TriggerOverdueRemindersView,
    OverdueRemindersStatusView,
    TriggerNotificationRetryView,
    NotificationRetryStatusView,
)

from controls.views.borrower import (
    TriggerCreditScoreRecalcView,
    TriggerBorrowerMergeView,
    TriggerBorrowerCleanupView,
    TriggerBorrowerStatusUpdateView,
)

from controls.views.group import (
    TriggerBulkAssignView,
    TriggerAutoAssignView,
    TriggerGroupCleanupView,
    TriggerGroupStatsUpdateView,
)

from controls.views.loan_agreement import (
    TriggerAgreementCleanupView,
    TriggerOverdueAgreementNotifyView,
    TriggerAutoAssignAgreementsView,
    TriggerSyncAgreementStatusView,
)

from controls.views.loan_application import (
    TriggerAutoApproveView,
    TriggerStaleCleanupView,
    TriggerPendingRemindersView,
    TriggerBulkImportApplicationsView,
)

from controls.views.payment_method import (
    TriggerPaymentMethodStatsRecalcView,
    TriggerPaymentMethodCleanupView,
    TriggerPaymentMethodReportView,
    TriggerEnsureDefaultMethodView,
)

from controls.views.sync import (
    TriggerSyncHealthCheckView,
    TriggerQueueRetryView,
    TriggerSyncCleanupView,
    TriggerSyncReportView,
)

from controls.views.system_setting import (
    TriggerSettingsCacheRefreshView,
    TriggerSettingsValidateView,
    TriggerSettingsBackupView,
    TriggerSettingsDiffView,
)

from controls.views.user import (
    TriggerSecurityCleanupView,
    TriggerSecurityMonitorView,
    TriggerAutoSuspendView,
    TriggerOrphanCleanupView,
    TriggerSecurityReportView,
)


urlpatterns = [
    # Interest Accrual
    path('interest-accrual/trigger/', TriggerInterestAccrualView.as_view(), name='trigger-interest-accrual'),
    path('interest-accrual/status/', InterestAccrualStatusView.as_view(), name='interest-accrual-status'),

    # Overdue Corrector
    path('overdue-corrector/trigger/', TriggerOverdueCorrectorView.as_view(), name='trigger-overdue-corrector'),
    path('overdue-corrector/status/', OverdueCorrectorStatusView.as_view(), name='overdue-corrector-status'),

    # Overdue Updater
    path('overdue-updater/trigger/', TriggerOverdueUpdaterView.as_view(), name='trigger-overdue-updater'),
    path('overdue-updater/status/', OverdueUpdaterStatusView.as_view(), name='overdue-updater-status'),

    # Zero Balance Fixer
    path('zero-balance-fixer/trigger/', TriggerZeroBalanceFixerView.as_view(), name='trigger-zero-balance-fixer'),
    path('zero-balance-fixer/status/', ZeroBalanceFixerStatusView.as_view(), name='zero-balance-fixer-status'),

    # Penalty Scheduler
    path('penalty-scheduler/trigger/', TriggerPenaltySchedulerView.as_view(), name='trigger-penalty-scheduler'),
    path('penalty-scheduler/status/', PenaltySchedulerStatusView.as_view(), name='penalty-scheduler-status'),

    # Health Checks
    path('health/overdue-status/', OverdueStatusHealthView.as_view(), name='health-overdue-status'),
    path('health/zero-balance/', ZeroBalanceHealthView.as_view(), name='health-zero-balance'),
    path('health/penalty/', PenaltyHealthView.as_view(), name='health-penalty'),

    # Audit Cleanup
    path('audit-cleanup/trigger/', TriggerAuditCleanupView.as_view(), name='trigger-audit-cleanup'),
    path('audit-cleanup/status/', AuditCleanupStatusView.as_view(), name='audit-cleanup-status'),

    # Overdue Reminders
    path('overdue-reminders/trigger/', TriggerOverdueRemindersView.as_view(), name='trigger-overdue-reminders'),
    path('overdue-reminders/status/', OverdueRemindersStatusView.as_view(), name='overdue-reminders-status'),

    # Notification Retry
    path('notification-retry/trigger/', TriggerNotificationRetryView.as_view(), name='trigger-notification-retry'),
    path('notification-retry/status/', NotificationRetryStatusView.as_view(), name='notification-retry-status'),
]


urlpatterns += [
    # Borrower tasks
    path('borrower/credit-score-recalc/trigger/', TriggerCreditScoreRecalcView.as_view(), name='trigger-credit-score-recalc'),
    path('borrower/merge/trigger/', TriggerBorrowerMergeView.as_view(), name='trigger-borrower-merge'),
    path('borrower/cleanup/trigger/', TriggerBorrowerCleanupView.as_view(), name='trigger-borrower-cleanup'),
    path('borrower/status-update/trigger/', TriggerBorrowerStatusUpdateView.as_view(), name='trigger-borrower-status-update'),
]

urlpatterns += [
    # Group tasks
    path('group/bulk-assign/trigger/', TriggerBulkAssignView.as_view(), name='trigger-bulk-assign'),
    path('group/auto-assign/trigger/', TriggerAutoAssignView.as_view(), name='trigger-auto-assign'),
    path('group/cleanup/trigger/', TriggerGroupCleanupView.as_view(), name='trigger-group-cleanup'),
    path('group/stats-update/trigger/', TriggerGroupStatsUpdateView.as_view(), name='trigger-group-stats-update'),
]

urlpatterns += [
    # Loan Agreement tasks
    path('loan-agreement/cleanup/trigger/', TriggerAgreementCleanupView.as_view(), name='trigger-loan-agreement-cleanup'),
    path('loan-agreement/overdue-notify/trigger/', TriggerOverdueAgreementNotifyView.as_view(), name='trigger-loan-agreement-overdue-notify'),
    path('loan-agreement/auto-assign/trigger/', TriggerAutoAssignAgreementsView.as_view(), name='trigger-loan-agreement-auto-assign'),
    path('loan-agreement/sync-status/trigger/', TriggerSyncAgreementStatusView.as_view(), name='trigger-loan-agreement-sync-status'),
]

urlpatterns += [
    # Loan Application Tasks
    path('loan-application/auto-approve/trigger/', TriggerAutoApproveView.as_view(), name='trigger-auto-approve'),
    path('loan-application/stale-cleanup/trigger/', TriggerStaleCleanupView.as_view(), name='trigger-stale-cleanup'),
    path('loan-application/pending-reminders/trigger/', TriggerPendingRemindersView.as_view(), name='trigger-pending-reminders'),
    path('loan-application/bulk-import/trigger/', TriggerBulkImportApplicationsView.as_view(), name='trigger-bulk-import'),
]

urlpatterns += [
    # Payment Method tasks
    path('payment-method/stats-recalc/trigger/', TriggerPaymentMethodStatsRecalcView.as_view(), name='trigger-payment-method-stats-recalc'),
    path('payment-method/cleanup/trigger/', TriggerPaymentMethodCleanupView.as_view(), name='trigger-payment-method-cleanup'),
    path('payment-method/report/trigger/', TriggerPaymentMethodReportView.as_view(), name='trigger-payment-method-report'),
    path('payment-method/ensure-default/trigger/', TriggerEnsureDefaultMethodView.as_view(), name='trigger-ensure-default-method'),
]

urlpatterns += [
    # Sync maintenance tasks
    path('sync/health-check/trigger/', TriggerSyncHealthCheckView.as_view(), name='trigger-sync-health-check'),
    path('sync/queue-retry/trigger/', TriggerQueueRetryView.as_view(), name='trigger-sync-queue-retry'),
    path('sync/cleanup/trigger/', TriggerSyncCleanupView.as_view(), name='trigger-sync-cleanup'),
    path('sync/report/trigger/', TriggerSyncReportView.as_view(), name='trigger-sync-report'),
]

urlpatterns += [
    # System Settings tasks
    path('settings/cache-refresh/trigger/', TriggerSettingsCacheRefreshView.as_view(), name='trigger-settings-cache-refresh'),
    path('settings/validate/trigger/', TriggerSettingsValidateView.as_view(), name='trigger-settings-validate'),
    path('settings/backup/trigger/', TriggerSettingsBackupView.as_view(), name='trigger-settings-backup'),
    path('settings/diff/trigger/', TriggerSettingsDiffView.as_view(), name='trigger-settings-diff'),
]

urlpatterns += [
    # User/security tasks
    path('user/security-cleanup/trigger/', TriggerSecurityCleanupView.as_view(), name='trigger-security-cleanup'),
    path('user/security-monitor/trigger/', TriggerSecurityMonitorView.as_view(), name='trigger-security-monitor'),
    path('user/auto-suspend/trigger/', TriggerAutoSuspendView.as_view(), name='trigger-auto-suspend'),
    path('user/orphan-cleanup/trigger/', TriggerOrphanCleanupView.as_view(), name='trigger-orphan-cleanup'),
    path('user/security-report/trigger/', TriggerSecurityReportView.as_view(), name='trigger-security-report'),
]
