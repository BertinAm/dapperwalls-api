from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def send_password_reset(user, link):
    context = {
        "user": user,
        "name": user.first_name or user.get_username(),
        "link": link,
        "minutes": settings.PASSWORD_RESET_TIMEOUT // 60,
        "site_url": settings.SITE_URL,
        "assets": getattr(settings, "EMAIL_ASSET_BASE_URL", settings.SITE_URL + "/email"),
        "support_email": settings.ENQUIRY_REPLY_TO_EMAIL,
        "checkatrade_url": "https://www.checkatrade.com/trades/dapperwallsltd",
    }
    message = EmailMultiAlternatives(
        subject="Reset your DapperWalls dashboard password",
        body=render_to_string("dashboard/emails/password_reset.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(render_to_string("dashboard/emails/password_reset.html", context), "text/html")
    message.send(fail_silently=False)
