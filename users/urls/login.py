
from django.urls import path

from users.views.login.login import LoginView, Resend2FAOTPView, Verify2FALoginView
from users.views.login.logout import LogoutAllView, LogoutView

urlpatterns = [
    path("", LoginView.as_view(), name="login"),
    path("verify-2fa/", Verify2FALoginView.as_view(), name="verify_2fa"),
    path("resend-2fa/", Resend2FAOTPView.as_view(), name="resend_2fa"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("logout/all/", LogoutAllView.as_view(), name="logout_all"),
]