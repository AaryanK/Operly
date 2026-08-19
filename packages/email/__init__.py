"""Provider-neutral OPERLY transactional email."""

from packages.email.service import EmailDeliveryError, EmailService, get_email_service

__all__ = ["EmailDeliveryError", "EmailService", "get_email_service"]
