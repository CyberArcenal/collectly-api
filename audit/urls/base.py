from .log import urlpatterns as log_urls
from .policy import urlpatterns as policy_urls
urlpatterns = [
    *log_urls,
    *policy_urls,
]
