from django.conf import settings
from django.core.checks import Error, register

MIN_PROXY_SECRET_LENGTH = 32


@register()
def proxy_secret_check(app_configs, **kwargs):
    """In production the Cloudflare proxy's shared secret must be set and long."""
    if not settings.IS_PRODUCTION:
        return []
    if len(settings.PROXY_SHARED_SECRET) < MIN_PROXY_SECRET_LENGTH:
        return [
            Error(
                f"PROXY_SHARED_SECRET must be set to at least {MIN_PROXY_SECRET_LENGTH} characters.",
                hint="Use the same value as the Cloudflare Pages Function's PROXY_SHARED_SECRET.",
                id="enquiries.E001",
            )
        ]
    return []
