"""
URL configuration for core project.
"""

import logging
import traceback

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.conf.urls.static import static
from django.shortcuts import redirect
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from core.views.base import HealthCheckView
from users.views.login.login import LoginView
from users.views.login.logout import LogoutView
from users.views.login.verify import TokenVerifyView
from users.views.security.jwt import RefreshTokenView

logger = logging.getLogger(__name__)


urlpatterns = [
    # Django admin
    path("admin/", admin.site.urls),
    # Schema endpoint (JSON)
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    # Swagger UI (explicit docs URL)
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Authentication endpoints
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("refresh/", RefreshTokenView.as_view(), name="token_refresh"),
]

# Dynamically include each app's urls/base.py under its own prefix
for app in settings.PROJECT_APPS:
    try:
        urlpatterns += [path(f"api/v1/{app}/", include(f"{app}.urls.base"))]
    except ModuleNotFoundError:
        logger.error(f"Module {app} in v1 not found, skipping.")
        logger.error(traceback.format_exc())
    try:
        urlpatterns += [path(f"api/v2/{app}/", include(f"{app}.urls_v2.base"))]
    except ModuleNotFoundError:
        logger.error(f"Module {app} in v2 not found, skipping.")
    try:
        urlpatterns += [path(f"api/v3/{app}/", include(f"{app}.urls_v3.base"))]
    except ModuleNotFoundError:
        logger.error(f"Module {app} in v3 not found, skipping.")

# License and transaction views (web/API endpoints)
urlpatterns += [

]

# Root path: redirect to Swagger UI (public)
urlpatterns += [
    path("", lambda request: redirect("/api/docs/"), name="home"),
]

# Health check (public)
urlpatterns += [
    path("health/", HealthCheckView.as_view(), name="health-check"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)