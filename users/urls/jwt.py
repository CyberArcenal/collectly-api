
from django.urls import path

from users.views.security.jwt import RefreshTokenView





urlpatterns = [
        path('refresh/', RefreshTokenView.as_view(), name='token_refresh'),
]