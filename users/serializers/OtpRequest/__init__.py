# users/serializers/OtpRequest/__init__.py
from .read import OtpRequestReadSerializer
from .write import OtpRequestWriteSerializer

__all__ = [
    'OtpRequestReadSerializer',
    'OtpRequestWriteSerializer',
]