import re
from django.conf import settings  # kung ginagamit mo ang settings.DEBUG

def camel_to_snake(name):
    """I-convert ang camelCase (o PascalCase) sa snake_case."""
    # Maglagay ng underscore bago ang bawat uppercase na sinusundan ng lowercase/digit
    s = re.sub(r'(?<=[a-z0-9])([A-Z])', r'_\1', name)
    return s.lower()  # gawing lowercase lahat

def filter_cleaner(filter_dict):
    """
    Nililinis ang filter dictionary:
      - Kino-convert ang mga camelCase keys → snake_case
      - Tinatanggal ang mga key na may value na None, '', [], {}, (), 'undefined', 'null'
    """
    # 1. I-convert ang keys
    converted = {camel_to_snake(k): v for k, v in filter_dict.items()}

    # 2. Linisin ang mga values
    clean = {
        k: v
        for k, v in converted.items()
        if v is not None
        and v != ""
        and v != []
        and v != {}
        and v != ()
        and v != "undefined"
        and v != "null"
    }

    if settings.DEBUG:
        print(f"Cleaned filter dictionary: {clean}")

    return clean