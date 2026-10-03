"""Helpers for the cookieless site analytics."""
import hashlib
import hmac
import re
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.utils import timezone

from enquiries.client_ip import get_client_ip, has_valid_proxy_token, is_cloudflare

BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|headless|lighthouse|pagespeed|preview|facebookexternalhit|"
    r"embedly|quora|pinterestbot|whatsapp|telegram|curl|wget|python|httpx|okhttp|java/|go-http",
    re.I,
)

SEARCH_HOSTS = ("google.", "bing.com", "duckduckgo.com", "yahoo.", "ecosia.org", "baidu.com", "yandex.", "search.brave.com")
SOCIAL_HOSTS = {
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "pinterest.": "Pinterest",
    "pin.it": "Pinterest",
    "t.co": "X (Twitter)",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "linkedin.com": "LinkedIn",
    "lnkd.in": "LinkedIn",
    "youtube.com": "YouTube",
    "whatsapp.com": "WhatsApp",
    "reddit.com": "Reddit",
}
SOCIAL_UTM = {"facebook", "fb", "instagram", "ig", "tiktok", "pinterest", "linkedin", "twitter", "x", "youtube", "whatsapp"}
PAID_MEDIUMS = {"cpc", "ppc", "paid", "paidsearch", "paid_search", "display", "paid_social", "paidsocial"}

# Channels, in the order the dashboard shows them.
CHANNELS = ["Google Ads", "Paid", "Search", "Social", "Email", "Referral", "Direct"]


def visitor_hash(ip, user_agent, day=None):
    """A daily-rotating, keyed hash of IP + browser. Not reversible without the
    server's SECRET_KEY, and different every day by design."""
    day = day or timezone.localdate()
    msg = f"{day.isoformat()}|{ip or ''}|{user_agent or ''}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:32]


def is_bot(user_agent):
    return not user_agent or bool(BOT_RE.search(user_agent))


def _host(url):
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def site_hosts():
    hosts = {_host(settings.SITE_URL)}
    hosts |= {_host(o) for o in getattr(settings, "CORS_ALLOWED_ORIGINS", [])}
    return {h for h in hosts if h}


def classify(referrer, utm_source="", utm_medium="", gclid=False):
    """Return (channel, source, referrer_host) for a landing page view."""
    host = _host(referrer) if referrer else ""
    if host in site_hosts() or host in {"localhost", "127.0.0.1"}:
        host = ""
    us, um = utm_source.lower(), utm_medium.lower()

    if gclid or (um in PAID_MEDIUMS and us in {"google", "adwords", "googleads"}):
        return "Google Ads", "google", host
    if um in PAID_MEDIUMS:
        return "Paid", us or host or "unknown", host
    if um == "email" or us in {"email", "newsletter"}:
        return "Email", us or "email", host
    if us in SOCIAL_UTM or um in {"social", "social-media", "bio"}:
        return "Social", us or host, host
    if host:
        if any(s in host for s in SEARCH_HOSTS):
            return "Search", host.split(".")[0] if not host.startswith("search.") else host, host
        for needle, name in SOCIAL_HOSTS.items():
            if host == needle or host.endswith("." + needle) or (needle.endswith(".") and needle in host):
                return "Social", name, host
        return "Referral", host, host
    if us:
        return "Referral", us, host
    return "Direct", "", ""


def parse_user_agent(ua):
    """Tiny UA sniffing: just enough for device, browser and OS charts."""
    ua = ua or ""
    low = ua.lower()
    if "ipad" in low or ("android" in low and "mobile" not in low) or "tablet" in low:
        device = "Tablet"
    elif "mobi" in low or "iphone" in low or "android" in low:
        device = "Mobile"
    else:
        device = "Desktop"

    if "edg/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "samsungbrowser" in low:
        browser = "Samsung Internet"
    elif "firefox" in low or "fxios" in low:
        browser = "Firefox"
    elif "chrome" in low or "crios" in low:
        browser = "Chrome"
    elif "safari" in low:
        browser = "Safari"
    else:
        browser = "Other"

    if "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "iOS"
    elif "android" in low:
        os_name = "Android"
    elif "windows" in low:
        os_name = "Windows"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macOS"
    elif "linux" in low:
        os_name = "Linux"
    else:
        os_name = "Other"
    return device, browser, os_name


def _clean(value, length):
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()[:length]


def get_location(request):
    """Country / region / city from Cloudflare.

    Through our Worker these arrive as X-Client-Country/-Region/-City (trusted
    only with a valid proxy token). A request straight through Cloudflare has
    CF-IPCountry, trusted only from a Cloudflare address.
    """
    meta = request.META
    if has_valid_proxy_token(request):
        country = meta.get("HTTP_X_CLIENT_COUNTRY", "")
        # The Worker URL-encodes these (header values must be ASCII).
        region = unquote(meta.get("HTTP_X_CLIENT_REGION", ""))
        city = unquote(meta.get("HTTP_X_CLIENT_CITY", ""))
    elif is_cloudflare(meta.get("REMOTE_ADDR")):
        country, region, city = meta.get("HTTP_CF_IPCOUNTRY", ""), "", ""
    else:
        country = region = city = ""
    country = _clean(country, 2).upper()
    if not re.fullmatch(r"[A-Z]{2}", country) or country in {"XX", "T1"}:
        country = ""
    return country, _clean(region, 80), _clean(city, 80)


def request_visitor(request):
    ua = request.headers.get("User-Agent", "")
    return visitor_hash(get_client_ip(request), ua), ua


def attribution_for(visitor):
    """Channel/source of a visitor's first page view today (for enquiries)."""
    from .models import VisitEvent

    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    first = (
        VisitEvent.objects.filter(visitor=visitor, kind=VisitEvent.Kind.PAGEVIEW, created_at__gte=start)
        .exclude(channel="")
        .order_by("created_at")
        .first()
    )
    if not first:
        return {}
    return {
        "channel": first.channel,
        "source": first.source,
        "utm_campaign": first.utm_campaign,
    }
