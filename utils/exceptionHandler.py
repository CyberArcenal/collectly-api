import traceback
from rest_framework.views import exception_handler
from django.http import JsonResponse
from rest_framework import status
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

from users.utils.authentications import is_blacklisted


def custom_exception_handler(exc, context):
    # Get default DRF response first
    response = exception_handler(exc, context)

    exception_class = exc.__class__.__name__

    handlers = {
        "ValidationError": _handle_validation_error,
        "Http404": _handle_http404_error,
        "PermissionDenied": _handle_permission_denied,
        "NotAuthenticated": _handle_authentication_error,
        "AuthenticationFailed": _handle_authentication_error,
        "InvalidToken": _handle_invalid_token,
        "MethodNotAllowed": _handle_method_error,
        "ParseError": _handle_generic_error,
        "UnsupportedMediaType": _handle_generic_error,
        "Throttled": _handle_generic_error,
        "DoesNotExist": _handle_http404_error,
        "APIException": _handle_generic_error,
        "UnicodeDecodeError": _handle_unicode_error,
        "Exception": _handle_server_error,
    }

    # Log the error details
    traceback.print_exc()
    print(f"\033[1;91mException: \033[1;93m{exception_class}\033[1;97m → {str(exc)}")

    # Find the appropriate handler
    handler = handlers.get(exception_class, handlers["Exception"])

    return handler(exc, context, response)


def _handle_permission_denied(exc, context, response):
    """
    Return 401 if user is not authenticated,
    otherwise 403 if user is authenticated but lacks permission.
    Check if token is blacklisted.
    """
    request = context.get("request", None)
    user = getattr(request, "user", None)
    is_auth = bool(user and user.is_authenticated)

    # Check if token is blacklisted
    token_blacklisted = is_blacklisted(request) if request else False
    
    if token_blacklisted:
        status_code = status.HTTP_401_UNAUTHORIZED
        message = "Token is blacklisted"
    elif is_auth:
        status_code = status.HTTP_403_FORBIDDEN
        message = "Permission denied"
    else:
        status_code = status.HTTP_401_UNAUTHORIZED
        message = "Authentication credentials were not provided"

    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": message,
        },
        status=status_code,
    )


def _handle_validation_error(exc, context, response):
    status_code = 400
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": "Validation error",
            "errors": exc.detail,  # Include the actual validation errors
        },
        status=status_code,
    )


# Exception Handlers
def _handle_server_error(exc, context, response):
    status_code = 500
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": "Internal server error",
        },
        status=status_code,
    )


def _handle_invalid_token(exc, context, response):
    status_code = response.status_code if response else 401
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": exc.__class__.__name__,
        },
        status=status_code,
    )


def _handle_unicode_error(exc, context, response):
    status_code = response.status_code if response else 400
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": "Unicode Decode Error",
        },
        status=status_code,
    )


def _handle_invalidated_error(exc, context, response):
    status_code = response.status_code if response else 403
    return JsonResponse(
        {"status": False, "status_code": status_code, "message": "Invalid credentials"},
        status=status_code,
    )


def _handle_authentication_error(exc, context, response):
    status_code = response.status_code if response else 401
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": "Authentication error",
        },
        status=status_code,
    )


def _handle_generic_error(exc, context, response):
    status_code = response.status_code if response else 400
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": exc.__class__.__name__,
        },
        status=status_code,
    )


def _handle_http404_error(exc, context, response):
    status_code = 404
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": exc.__class__.__name__,
        },
        status=status_code,
    )


def _handle_type_error(exc, context, response):
    status_code = 400
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": exc.__class__.__name__,
        },
        status=status_code,
    )


def _handle_method_error(exc, context, response):
    status_code = response.status_code if response else 405
    return JsonResponse(
        {
            "status": False,
            "status_code": status_code,
            "message": exc.__class__.__name__,
        },
        status=status_code,
    )
