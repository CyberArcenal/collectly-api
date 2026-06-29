# users/serializers/fields.py
import base64
import uuid
from django.core.files.base import ContentFile
from rest_framework import serializers


class Base64ImageField(serializers.ImageField):
    """
    A custom field that accepts base64 encoded images.
    """
    def to_internal_value(self, data):
        if isinstance(data, str) and data.startswith('data:image'):
            # Decode base64 image
            format, imgstr = data.split(';base64,')
            ext = format.split('/')[-1]
            
            # Validate image format
            if ext not in ['jpeg', 'jpg', 'png', 'gif', 'webp']:
                raise serializers.ValidationError("Unsupported image format.")
            
            # Decode and create ContentFile
            decoded_file = base64.b64decode(imgstr)
            filename = f"{uuid.uuid4()}.{ext}"
            return ContentFile(decoded_file, name=filename)
        
        return super().to_internal_value(data)