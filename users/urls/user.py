



from django.urls import path

from users.views.User.base import UserCRUDView


urlpatterns = [
    path("", UserCRUDView.as_view(), name="user"),
    path("<int:pk>/", UserCRUDView.as_view(), name="user-detail"),
]
