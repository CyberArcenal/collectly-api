from ipaddress import ip_address as validate_ip
import logging
import socket
from django.http import HttpRequest
from typing import Optional, Dict, Any, Union, List







logger = logging.getLogger(__name__)


def get_client_ip(request: HttpRequest) -> Optional[str]:
    """
    Extract the real client IP address from request headers.
    Honors X-Forwarded-For (may contain comma-separated list) and falls
    back to REMOTE_ADDR. Validates format using ipaddress.ip_address.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    raw_ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")
    try:
        # Validate and normalize
        ip_obj = validate_ip(raw_ip)
        return str(ip_obj)
    except Exception:
        logger.warning("Invalid IP address format: %r", raw_ip)
        return None

def get_server_ip() -> str:
    """
    Get the server's IP address.
    """
    try:
        # Try to get server IP by connecting to a remote address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # Fallback to localhost
        return "127.0.0.1"