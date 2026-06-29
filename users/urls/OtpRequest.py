# accounts/base.py
from django.urls import path

from users.views.login.OtpRequest import OtpRequestCRUD

urlpatterns = [
    path('', OtpRequestCRUD.as_view(), name='otp-requests-list'),
    path('<int:id>/', OtpRequestCRUD.as_view(), name='otp-requests-detail'),
]