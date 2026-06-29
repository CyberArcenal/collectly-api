from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken
from django.utils import timezone

from users.models.blacklisted_token import BlacklistedAccessToken



class IsAuthenticatedAndNotBlacklisted(IsAuthenticated):
    def has_permission(self, request, view):
        # Una, normal IsAuthenticated check
        if not super().has_permission(request, view):
            return False
        try:
            if is_blacklisted(request=request):
                return False
        except InvalidToken:
            return False

        return True
    
def get_token_jti(request):
    token = request.auth
    if token is None:
        return None
    try:
        access_token = AccessToken(str(token))
        jti = access_token.get("jti")
        return jti
    except InvalidToken:
        return None
    
def is_blacklisted(request):
    try:
        jti = get_token_jti(request)
        if not jti:
            return True
        return BlacklistedAccessToken.is_blacklisted(jti)
    except InvalidToken:
        return True