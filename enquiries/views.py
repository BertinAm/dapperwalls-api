import json
import logging
from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import ratelimit
from .client_ip import get_client_ip, has_valid_proxy_token
from .emails import send_enquiry_emails
from .models import Enquiry, generate_reference
from .validation import MAX_LINKS, count_links, validate_enquiry

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = (
    "Too many enquiries. Please try again later or email support@dapperwalls.co.uk."
)


def error(status, message, field="__all__", **headers):
    response = JsonResponse({"ok": False, "errors": {field: [message]}}, status=status)
    for name, value in headers.items():
        response[name.replace("_", "-")] = value
    return response


def health(request):
    if request.method not in ("GET", "HEAD"):
        return error(405, "Method not allowed.", Allow="GET, HEAD")
    return JsonResponse({"ok": True})


def _looks_like_bot(data):
    if str(data.get("website") or "").strip():
        return "honeypot filled"
    elapsed = data.get("elapsed_ms")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        return "elapsed_ms missing or not a number"
    if elapsed < settings.ENQUIRY_MIN_ELAPSED_MS:
        return f"submitted too fast ({int(elapsed)} ms)"
    return None


@csrf_exempt
def create_enquiry(request):
    if request.method != "POST":
        return error(405, "Method not allowed.", Allow="POST")

    if settings.REQUIRE_PROXY_TOKEN and not has_valid_proxy_token(request):
        logger.warning("Rejected enquiry without a valid proxy token from %s", get_client_ip(request))
        return error(403, "Please send your enquiry through dapperwalls.co.uk.")

    origin = request.headers.get("Origin")
    if origin and origin not in settings.CORS_ALLOWED_ORIGINS and not has_valid_proxy_token(request):
        logger.warning("Rejected enquiry from origin %s", origin)
        return error(403, "Origin not allowed.")

    if request.content_type != "application/json":
        return error(415, "Send the enquiry as JSON (Content-Type: application/json).")

    max_bytes = settings.ENQUIRY_MAX_BODY_BYTES
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    if declared > max_bytes:
        return error(413, "Enquiry is too large.")
    body = request.body
    if len(body) > max_bytes:
        return error(413, "Enquiry is too large.")

    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return error(400, "Invalid JSON.")
    if not isinstance(data, dict):
        return error(400, "Expected a JSON object.")

    ip = get_client_ip(request)
    if not ratelimit.hit(ip):
        logger.warning("Rate limited enquiry from %s", ip)
        return error(429, RATE_LIMIT_MESSAGE, Retry_After="3600")

    bot_reason = _looks_like_bot(data)
    if bot_reason:
        # Pretend it worked so bots don't learn anything.
        logger.info("Discarded likely-bot enquiry from %s: %s", ip, bot_reason)
        return JsonResponse({"ok": True, "reference": generate_reference()}, status=201)

    cleaned, errors = validate_enquiry(data)
    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    if count_links(cleaned["message"]) > MAX_LINKS:
        logger.info("Discarded link-heavy enquiry from %s", ip)
        return JsonResponse({"ok": True, "reference": generate_reference()}, status=201)

    since = timezone.now() - timedelta(days=1)
    if Enquiry.objects.filter(email__iexact=cleaned["email"], created_at__gte=since).count() >= settings.ENQUIRIES_PER_EMAIL_PER_DAY:
        logger.warning("Per-address limit hit for an enquiry from %s", ip)
        return error(429, RATE_LIMIT_MESSAGE, Retry_After="86400")

    enquiry = Enquiry.objects.create(
        **cleaned,
        ip_address=ip,
        user_agent=request.headers.get("User-Agent", "")[:300],
    )
    logger.info("Saved enquiry %s from %s", enquiry.reference, ip)
    send_enquiry_emails(enquiry)
    return JsonResponse({"ok": True, "reference": enquiry.reference}, status=201)
