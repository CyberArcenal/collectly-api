REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "users.utils.authentications.IsAuthenticatedAndNotBlacklisted",
    ],
    "EXCEPTION_HANDLER": "utils.exceptionHandler.custom_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "utils.response.CustomPagination",
    "PAGE_SIZE": 20,
}
