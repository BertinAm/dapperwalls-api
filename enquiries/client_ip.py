import hmac
import ipaddress
from functools import lru_cache

from django.conf import settings

# Cloudflare's published edge ranges (https://www.cloudflare.com/ips/).
# Override with the CLOUDFLARE_IP_RANGES setting if they change.
CLOUDFLARE_IP_RANGES = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)


@lru_cache(maxsize=1)
def _cloudflare_networks(ranges):
    return tuple(ipaddress.ip_network(r) for r in ranges)


def is_cloudflare(ip):
    """True if `ip` belongs to Cloudflare's edge network."""
    if not ip:
        return False
    ranges = tuple(getattr(settings, "CLOUDFLARE_IP_RANGES", None) or CLOUDFLARE_IP_RANGES)
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in _cloudflare_networks(ranges))


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
    2. The header named by TRUSTED_IP_HEADER (default CF-Connecting-IP), only
       when REMOTE_ADDR is a Cloudflare edge address.
    3. REMOTE_ADDR.
    """
    if has_valid_proxy_token(request):
        ip = _clean_ip(request.META.get("HTTP_X_CLIENT_IP"))
        if ip:
            return ip
    remote = _clean_ip(request.META.get("REMOTE_ADDR"))
    # Only believe CF-Connecting-IP when the request really came from a
    # Cloudflare server. Anyone who connects to the origin directly could
    # otherwise send a fake value on every request and dodge rate limits.
    if settings.TRUSTED_IP_HEADER and (
        not getattr(settings, "TRUSTED_IP_HEADER_REQUIRES_CLOUDFLARE", True) or is_cloudflare(remote)
    ):
        ip = _clean_ip(request.META.get(settings.TRUSTED_IP_HEADER))
        if ip:
            return ip
    return remote
