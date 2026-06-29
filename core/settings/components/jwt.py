from datetime import timedelta
import os

SECRET_KEY = os.getenv("SECRET_KEY")


ALGORITHYM = "HS256"
ACCESS_TOKEN_LIFETIME = timedelta(days=7)
REFRESH_TOKEN_LIFETIME = timedelta(days=30)
# ACCESS_TOKEN_LIFETIME = timedelta(minutes=1)
# 2 minuto para sa refresh token
# REFRESH_TOKEN_LIFETIME = timedelta(minutes=4)


SIMPLE_JWT = {
    # Access token expires in 6 days
    "ACCESS_TOKEN_LIFETIME": ACCESS_TOKEN_LIFETIME,
    "REFRESH_TOKEN_LIFETIME": REFRESH_TOKEN_LIFETIME,
    # Issue new refresh token when refreshing
    "ROTATE_REFRESH_TOKENS": True,
    # Blacklist previous refresh tokens
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": ALGORITHYM,
    "SIGNING_KEY": SECRET_KEY,  # Uses Django's SECRET_KEY
    # Authorization: Bearer <token>
    # Update user's last_login field
    "UPDATE_LAST_LOGIN": True,
}
