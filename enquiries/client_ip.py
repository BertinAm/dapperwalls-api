import hmac
import ipaddress

from django.conf import settings


def _clean_ip(value):
    """Return a normalised IP from a header value (first of a list), or None."""
    if not value:
        return None
    candidate = value.split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def has_valid_proxy_token(request):
    """True if the request carries the Cloudflare Pages proxy's shared secret."""
    secret = settings.PROXY_SHARED_SECRET
    token = request.META.get("HTTP_X_PROXY_TOKEN", "")
    if not secret or not token:
        return False
    return hmac.compare_digest(token.encode(), secret.encode())


def get_client_ip(request):
    """Resolve the visitor's IP address.

    1. X-Client-IP, but only when X-Proxy-Token matches PROXY_SHARED_SECRET
       (set by our Cloudflare Pages Function; at the origin CF-Connecting-IP
       is the proxy's address, not the visitor's).
    2. The header named by TRUSTED_IP_HEADER (default CF-Connecting-IP).
    3. REMOTE_ADDR.
    """
    if has_valid_proxy_token(request):
        ip = _clean_ip(request.META.get("HTTP_X_CLIENT_IP"))
        if ip:
            return ip
    if settings.TRUSTED_IP_HEADER:
        ip = _clean_ip(request.META.get(settings.TRUSTED_IP_HEADER))
        if ip:
            return ip
    return _clean_ip(request.META.get("REMOTE_ADDR"))
