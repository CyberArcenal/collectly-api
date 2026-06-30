import logging
import re
from django.conf import settings
from twilio.rest import Client

from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


class SmsService:
    """
    Service for sending SMS using Twilio.
    """
    
    def __init__(self):
        self.client = None
        self._initialize()
    
    def _initialize(self):
        """Initialize Twilio client."""
        account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', None)
        auth_token = getattr(settings, 'TWILIO_AUTH_TOKEN', None)
        
        if account_sid and auth_token:
            self.client = Client(account_sid, auth_token)
            logger.info("[SMS] Twilio client initialized")
        else:
            logger.warning("[SMS] Twilio credentials not configured")
    
    def format_phone_number(self, phone):
        """Format phone number for Twilio."""
        # Remove all non-digit characters
        formatted = re.sub(r'\D', '', phone)
        
        # Philippine number formatting
        if formatted.startswith('0'):
            formatted = '+63' + formatted[1:]
        elif not formatted.startswith('+'):
            formatted = '+' + formatted
        
        return formatted
    
    def send(self, to, message, options=None):
        """
        Send SMS message.
        """
        if not self.client:
            raise Exception("Twilio client not initialized")
        
        options = options or {}
        
        # Format phone number
        formatted_to = self.format_phone_number(to)
        
        # Get from number
        from_number = getattr(settings, 'TWILIO_PHONE_NUMBER', None)
        messaging_service_sid = getattr(settings, 'TWILIO_MESSAGING_SERVICE_SID', None)
        
        if messaging_service_sid:
            from_param = {'messaging_service_sid': messaging_service_sid}
        elif from_number:
            from_param = {'from_': from_number}
        else:
            raise Exception("No Twilio sender configured")
        
        try:
            result = self.client.messages.create(
                body=message,
                to=formatted_to,
                **from_param,
                **options
            )
            
            logger.info(f"[SMS] Sent to {formatted_to}, SID: {result.sid}")
            
            # Audit log
            log_audit_event(
                request=None,
                user=None,
                action_type="notification_send",
                model_name="SMS",
                object_id=result.sid,
                changes={
                    "to": formatted_to,
                    "status": result.status,
                }
            )
            
            return {
                "success": True,
                "sid": result.sid,
                "status": result.status,
                "price": result.price,
            }
            
        except Exception as e:
            logger.error(f"[SMS] Failed to send to {formatted_to}: {e}")
            
            # Audit log for failure
            log_audit_event(
                request=None,
                user=None,
                action_type="notification_error",
                model_name="SMS",
                object_id="failed",
                changes={
                    "to": formatted_to,
                    "error": str(e),
                }
            )
            
            raise
    
    def send_batch(self, recipients, message, options=None):
        """
        Send SMS to multiple recipients.
        """
        results = []
        logger.info(f"[SMS] Sending batch to {len(recipients)} recipients")
        
        for recipient in recipients:
            try:
                result = self.send(recipient, message, options)
                results.append({"recipient": recipient, **result})
            except Exception as e:
                results.append({
                    "recipient": recipient,
                    "success": False,
                    "error": str(e)
                })
        
        success_count = sum(1 for r in results if r.get('success', False))
        logger.info(f"[SMS] Batch complete: {success_count}/{len(recipients)} sent")
        
        return results