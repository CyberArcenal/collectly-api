from django.urls import include, path

from users.views.login.verify import TokenVerifyView
from users.views.security.base import DisableTwoFactorAPIView, EnableTwoFactorAPIView, SecurityHealthAPIView, SecurityLogDetailAPIView, SecurityStatsAPIView, SendEmailOTPView, SendPhoneOTPView, TerminateAllSessionsAPIView, TerminateSessionAPIView, TestSecurityAlertsAPIView, UserLoginSessionsAPIView, UserSecurityConfigAPIView, VerifyEmailOTPView, VerifyPhoneOTPView, VerifyRecoveryEmailAPIView, VerifyRecoveryPhoneAPIView
from users.views.security.security_settings import UserSecuritySettingsAPIView
from users.views.security_log.security_log import SecurityLogListAPIView
from .login import urlpatterns as login_urls
from .jwt import urlpatterns as jwt_urlpatterns
from .user import urlpatterns as user_urlpatterns
from .OtpRequest import urlpatterns as otp


security_urlpatterns = [
    path("settings/", UserSecuritySettingsAPIView.as_view(), name="security-settings"),
    path("settings/config/", UserSecurityConfigAPIView.as_view(), name="security-config"),
    path("settings/health/", SecurityHealthAPIView.as_view(), name="security-health"),
    path("settings/stats/", SecurityStatsAPIView.as_view(), name="security-stats"),
    path("settings/enable-2fa/", EnableTwoFactorAPIView.as_view(), name="enable-2fa"),
    path("settings/disable-2fa/", DisableTwoFactorAPIView.as_view(), name="disable-2fa"),
    path("settings/verify-recovery-email/", VerifyRecoveryEmailAPIView.as_view(), name="verify-recovery-email"),
    path("settings/verify-recovery-phone/", VerifyRecoveryPhoneAPIView.as_view(), name="verify-recovery-phone"),
    path("settings/test-alerts/", TestSecurityAlertsAPIView.as_view(), name="test-alerts"),
    path("settings/sessions/", UserLoginSessionsAPIView.as_view(), name="user-sessions"),
    path("settings/sessions/terminate-all/", TerminateAllSessionsAPIView.as_view(), name="terminate-all-sessions"),
    path("settings/sessions/<uuid:session_id>/", TerminateSessionAPIView.as_view(), name="terminate-session"),
    path("logs/", SecurityLogListAPIView.as_view(), name="security-logs"),
    path("logs/<int:id>/", SecurityLogDetailAPIView.as_view(), name="security-log-detail"),
    path("send-email-otp/", SendEmailOTPView.as_view(), name="send-email-otp"),
    path("verify-email-otp/", VerifyEmailOTPView.as_view(), name="verify-email-otp"),
    path("send-phone-otp/", SendPhoneOTPView.as_view(), name="send-phone-otp"),
    path("verify-phone-otp/", VerifyPhoneOTPView.as_view(), name="verify-phone-otp"),
]


urlpatterns = [
    path("token/", include(jwt_urlpatterns)),
    path("login/", include(login_urls)),
    path("otp-requests/", include(otp)),
]


urlpatterns += [
    path("security/", include((security_urlpatterns, "security"))),
    path("verify/", TokenVerifyView.as_view(), name="custom_token_verify"),
    path("", include(user_urlpatterns)),
]