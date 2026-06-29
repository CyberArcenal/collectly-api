

from django.urls import include, path

from users.views.login.password_recover import PasswordResetCompleteView, PasswordResetRequestView, PasswordResetVerifyView
from users.views.login.password_reset import PasswordChangeView, PasswordHistoryView, PasswordStrengthCheckView

password_change_urlpatterns = [
    path("change/", PasswordChangeView.as_view(), name="password_change"),
    path("check-strength/", PasswordStrengthCheckView.as_view(), name="password_strength_check"),
    path("history/", PasswordHistoryView.as_view(), name="password_history"),
]

urlpatterns = [
    path("", include(password_change_urlpatterns)),
    path("reset/", PasswordResetRequestView.as_view(), name="password_reset_request"),
    path("reset/verify/", PasswordResetVerifyView.as_view(), name="password_reset_verify"),
    path("reset/complete/", PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]