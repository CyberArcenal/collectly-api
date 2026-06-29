

from django.urls import path
from audit.views.policy import AuditPolicyCRUD


urlpatterns = [
    path("policy/", AuditPolicyCRUD.as_view(), name=""),
    path("policy/<int:id>/", AuditPolicyCRUD.as_view(), name=""),
    
]
