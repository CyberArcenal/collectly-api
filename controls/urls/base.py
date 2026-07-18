from django.urls import path
from controls.views.base import (
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
]