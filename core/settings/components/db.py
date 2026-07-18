
from django.db import connection

with connection.cursor() as cursor:
    try:
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')
        cursor.execute('PRAGMA cache_size=-64000')
    except Exception as e:
        # Log the error but don't crash
        import logging
        logging.warning(f"Could not set PRAGMA journal_mode=WAL: {e}")
    