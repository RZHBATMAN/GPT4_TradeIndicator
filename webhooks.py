"""Backward-compat shim — delegates to core.webhooks."""
from core.webhooks import send_webhook, _post_with_retry, MAX_RETRIES, RETRY_DELAYS

__all__ = ['send_webhook', '_post_with_retry', 'MAX_RETRIES', 'RETRY_DELAYS']
