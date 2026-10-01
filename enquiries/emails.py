import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Enquiry

logger = logging.getLogger(__name__)


def _send(subject, to, reply_to, template, context):
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string(f"enquiries/emails/{template}.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=list(to) if isinstance(to, (list, tuple)) else [to],
        reply_to=[reply_to],
    )
    message.attach_alternative(
        render_to_string(f"enquiries/emails/{template}.html", context), "text/html"
    )
    message.send(fail_silently=False)


def send_notification(enquiry):
    """Email the business about a new enquiry. Reply-To is the customer."""
    _send(
        subject=f"New enquiry {enquiry.reference}: {enquiry.full_name}",
        to=settings.ENQUIRY_NOTIFY_EMAIL,
        reply_to=enquiry.email,
        template="notification",
        context={"enquiry": enquiry},
    )


def send_confirmation(enquiry):
    """Email the customer a short confirmation with their reference."""
    _send(
        subject=f"Your DapperWalls enquiry {enquiry.reference}",
        to=enquiry.email,
        reply_to=settings.ENQUIRY_REPLY_TO_EMAIL,
        template="confirmation",
        context={"enquiry": enquiry, "support_email": settings.ENQUIRY_REPLY_TO_EMAIL},
    )


def send_enquiry_emails(enquiry):
    """Send whichever emails haven't gone yet. Never raises.

    Failures are logged and the *_sent_at field stays null so the
    `send_pending_enquiry_emails` command can retry later.
    """
    for field, sender in (
        ("notification_sent_at", send_notification),
        ("confirmation_sent_at", send_confirmation),
    ):
        if getattr(enquiry, field):
            continue
        try:
            sender(enquiry)
        except Exception:
            logger.exception("Failed to send %s for %s", field.split("_")[0], enquiry.reference)
            continue
        now = timezone.now()
        setattr(enquiry, field, now)
        Enquiry.objects.filter(pk=enquiry.pk).update(**{field: now})
