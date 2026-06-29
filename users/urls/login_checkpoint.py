# accounts/base.py
from django.urls import path

from users.views.login.login_checkpoint import LoginCheckpointCRUD

urlpatterns = [
    path('', LoginCheckpointCRUD.as_view(), name='-list'),
    path('<int:id>/', LoginCheckpointCRUD.as_view(), name='-detail'),
]