import datetime
import decimal
import ipaddress
import logging
import threading
import queue
import time
import traceback
from typing import Optional, Dict, Any

from django.db import OperationalError
from django.utils import timezone
from django.http import HttpRequest
from django.contrib.auth.models import AbstractBaseUser

from audit.models import AuditLog
from utils.security import get_client_ip

logger = logging.getLogger(__name__)

# =============================================================================
# Background thread worker para sa pag-save ng audit logs
# =============================================================================

class AuditLogQueue:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_queue()
            return cls._instance
    
    def _init_queue(self):
        self.queue = queue.Queue()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        logger.info("AuditLog background worker started")
    
    def enqueue(self, log_data: dict):
        """Idagdag ang audit log data sa queue para i-process ng worker."""
        self.queue.put(log_data)
    
    def _worker(self):
        """Background worker na kumukuha ng items mula sa queue at nagse-save sa database."""
        while True:
            log_data = self.queue.get()
            try:
                self._save_with_retry(log_data)
            except Exception as e:
                traceback.print_exc()
                logger.error(f"AuditLog worker failed to save: {e}")
            finally:
                self.queue.task_done()
    
    def _save_with_retry(self, log_data: dict, max_retries: int = 3):
        """Subukang i-save ang audit log na may retry logic."""
        for attempt in range(max_retries):
            try:
                AuditLog.objects.create(**log_data)
                logger.debug(f"AuditLog saved successfully")
                return
            except OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    wait_time = 0.1 * (2 ** attempt)  # exponential backoff
                    logger.warning(f"Database locked, retry {attempt+1}/{max_retries} in {wait_time:.2f}s")
                    time.sleep(wait_time)
                    continue
                raise
            except Exception as e:
                logger.error(f"Unexpected error saving audit log: {e}")
                raise

# Singleton instance
_audit_log_queue = AuditLogQueue()

# =============================================================================
# Helper functions
# =============================================================================

def _sanitize_ip(ip: str | None) -> str | None:
    """Return the ip if it’s valid IPv4/IPv6, else None."""
    if not ip:
        return None
    try:
        ipaddress.ip_address(ip)
        return ip
    except ValueError:
        return None

def _serialize_for_json(obj):
    """I-convert ang non-JSON-serializable objects sa JSON-safe types."""
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_for_json(item) for item in obj]
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if hasattr(obj, '__dict__') and not isinstance(obj, (str, int, float, bool, type(None))):
        # fallback: convert to string representation para sa mga unknown objects
        return str(obj)
    return obj

def log_audit_event(
    *,
    request: Optional[HttpRequest] = None,
    user: Optional[AbstractBaseUser] = None,
    action_type: str,
    model_name: str,
    object_id: str,
    changes: Optional[Dict[str, Any]] = None,
    is_suspicious: Optional[bool] = False,
    suspicious_reason: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """
    I-queue ang audit log entry para i-save sa background.
    Hindi na nagre-return ng AuditLog object para hindi maghintay ang caller.
    """
    excluded_action = ["read"]
    if action_type in excluded_action:
        logger.info(f"Excluded action: {action_type} skipping..")
        return
    # Extract client info from request if available
    if not ip_address and request:
        ip_address = get_client_ip(request)
    if not user_agent and request:
        user_agent = request.META.get("HTTP_USER_AGENT", "")

    # Ensure user is valid
    try:
        user = user if user and user.is_authenticated else None
    except Exception:
        user = None

    clean_ip = _sanitize_ip(ip_address)

    log_data = {
        "user": user,
        "action_type": action_type,
        "model_name": model_name,
        "object_id": object_id,
        "changes": _serialize_for_json(changes or {}),
        "is_suspicious": is_suspicious,
        "suspicious_reason": suspicious_reason,
        "ip_address": clean_ip,
        "user_agent": user_agent,
        "timestamp": timezone.now(),
    }
    # Remove None values
    log_data = {k: v for k, v in log_data.items() if v is not None}

    # I-enqueue ang data para sa background worker
    _audit_log_queue.enqueue(log_data)