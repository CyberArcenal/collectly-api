from django.urls import path

from users.views.session.session import LoginSessionCRUD
from users.views.session.session_utils import LoginSessionRevokeView

urlpatterns = [
    path('', LoginSessionCRUD.as_view(), name='session-list'),
    path('<uuid:id>/', LoginSessionCRUD.as_view(), name='session-detail'),
    path('<uuid:session_id>/revoke/', LoginSessionRevokeView.as_view(), name='session-revoke'),
    
]
