


from django.urls import path
from audit.views.log import AuditLogCRUD


urlpatterns = [
    path("log/", AuditLogCRUD.as_view(), name="log"),
    path("log/<int:id>/", AuditLogCRUD.as_view(), name="log"),
    
]
