"""JSON API for the owner's dashboard at dapperwalls.co.uk/admin/ and the
public analytics beacon. Everything is reached through the site's Cloudflare
Worker, which proxies /api/* to this server."""
import csv
import json
import logging
import re
import time
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from enquiries.client_ip import get_client_ip, has_valid_proxy_token
from enquiries.models import Enquiry
from enquiries.validation import clean_text

from . import analytics
from .emails import send_password_reset
from .models import DashboardSettings, LoginAttempt, VisitEvent
from .tracking import classify, get_location, is_bot, parse_user_agent, request_visitor, site_hosts, _host

logger = logging.getLogger("enquiries")

USERNAME_RE = re.compile(r"[\w.@+-]{1,150}")
EVENT_NAMES = {"quote_open", "email_click", "phone_click", "social_click", "checkatrade_click"}
COLLECT_PER_HOUR = 600


def error(status, message, **headers):
    response = JsonResponse({"ok": False, "error": message}, status=status)
    for name, value in headers.items():
        response[name.replace("_", "-")] = value
    return response


def no_store(response):
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _proxy_ok(request):
    return not settings.REQUIRE_PROXY_TOKEN or has_valid_proxy_token(request)


def _json_body(request):
    if request.content_type != "application/json":
        return None
    if len(request.body) > 16 * 1024:
        return None
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def staff_api(view):
    """Staff session required; proxied through the site's Worker in production."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not _proxy_ok(request):
            return no_store(error(403, "Not allowed."))
        user = request.user
        if not (user.is_authenticated and user.is_active and user.is_staff):
            return no_store(error(401, "Please sign in."))
        return no_store(view(request, *args, **kwargs))

    return wrapper


# --- Public analytics beacon ---------------------------------------------------


@csrf_exempt
def collect(request):
    """Record a page view or event. Always answers 204 so it can't be probed."""
    done = HttpResponse(status=204)
    done["Cache-Control"] = "no-store"
    if request.method != "POST":
        return error(405, "Method not allowed.", Allow="POST")
    if not _proxy_ok(request):
        return done
    data = _json_body(request)
    if data is None:
        return done
    visitor, ua = request_visitor(request)
    if is_bot(ua):
        return done

    ip = get_client_ip(request) or "unknown"
    key = f"collect-rl:{timezone.now():%Y%m%d%H}:{ip}"
    count = cache.get(key, 0)
    if count >= COLLECT_PER_HOUR:
        return done
    cache.set(key, count + 1, 3600)

    kind = data.get("type")
    path = clean_text(str(data.get("path") or "/"))[:200] or "/"
    if not path.startswith("/") or path.startswith("/admin"):
        return done
    country, region, city = get_location(request)
    device, browser, os_name = parse_user_agent(ua)
    event = VisitEvent(
        visitor=visitor,
        path=path,
        country=country,
        region=region,
        city=city,
        device=device,
        browser=browser,
        os=os_name,
    )

    if kind == "pageview":
        referrer = str(data.get("referrer") or "")[:500]
        utm = {k: clean_text(str(data.get(k) or ""))[:80] for k in ("utm_source", "utm_medium")}
        campaign = clean_text(str(data.get("utm_campaign") or ""))[:120]
        internal = _host(referrer) in site_hosts() and not any(utm.values()) and not campaign and not data.get("gclid")
        if not internal:
            channel, source, ref_host = classify(referrer, utm["utm_source"], utm["utm_medium"], bool(data.get("gclid")))
            event.channel, event.source, event.referrer_host = channel, source[:80], ref_host[:120]
            event.utm_source, event.utm_medium, event.utm_campaign = utm["utm_source"], utm["utm_medium"], campaign
        event.kind = VisitEvent.Kind.PAGEVIEW
    elif kind == "event" and data.get("name") in EVENT_NAMES:
        event.kind = VisitEvent.Kind.EVENT
        event.name = data["name"]
        event.label = clean_text(str(data.get("label") or ""))[:80]
    else:
        return done
    event.save()
    return done


# --- Auth ----------------------------------------------------------------------


def _user_payload(user):
    return {
        "username": user.get_username(),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "name": user.get_full_name() or user.get_username(),
    }


@require_http_methods(["GET"])
def session(request):
    if not _proxy_ok(request):
        return no_store(error(403, "Not allowed."))
    user = request.user
    ok = user.is_authenticated and user.is_active and user.is_staff
    return no_store(
        JsonResponse(
            {
                "ok": True,
                "authenticated": bool(ok),
                "user": _user_payload(user) if ok else None,
                # The CSRF cookie is HttpOnly, so the page gets the token here.
                "csrfToken": get_token(request),
            }
        )
    )


def safe_username(value):
    """A username fit for the logs and login history: cleaned, or "(invalid)"."""
    value = clean_text(str(value or "")).strip()[:150]
    return value if not value or USERNAME_RE.fullmatch(value) else "(invalid)"


def record_login(request, area, outcome, username):
    username = safe_username(username)
    country, _region, city = get_location(request)
    try:
        LoginAttempt.objects.create(
            area=area,
            outcome=outcome,
            username=(username or "")[:150],
            ip_address=get_client_ip(request),
            country=country,
            city=city,
            user_agent=request.headers.get("User-Agent", "")[:300],
        )
    except Exception:  # never let logging break a login
        logger.exception("Couldn't record a login attempt")


@require_http_methods(["POST"])
def login_view(request):
    if not _proxy_ok(request):
        return no_store(error(403, "Not allowed."))
    data = _json_body(request)
    if data is None:
        return no_store(error(400, "Send your username and password as JSON."))
    raw_username, password = data.get("username"), data.get("password")
    remember = data.get("remember") is True
    if not isinstance(raw_username, str) or not isinstance(password, str):
        return no_store(error(400, "Enter your email or username and password."))
    # Usernames and emails only use letters, digits and @ . + - _. Anything
    # else can't be a real account, so it's refused before touching the
    # database, and control characters never reach the logs or login history.
    username = clean_text(raw_username).strip()[:150]
    bad_input = not (username and password and len(password) <= 256 and USERNAME_RE.fullmatch(username))
    username = safe_username(username)

    ip = get_client_ip(request) or "unknown"
    ip_key = f"dash-login-fail:ip:{ip}"
    user_key = f"dash-login-fail:user:{username.lower()}"
    lockout = settings.ADMIN_LOGIN_LOCKOUT_SECONDS
    max_ip = settings.ADMIN_LOGIN_MAX_FAILURES
    if cache.get(ip_key, 0) >= max_ip or cache.get(user_key, 0) >= max_ip * 2:
        record_login(request, LoginAttempt.Area.DASHBOARD, LoginAttempt.Outcome.BLOCKED, username)
        logger.warning("Dashboard login blocked for %s (%s)", ip, username)
        last = max(cache.get(f"{ip_key}:at", 0), cache.get(f"{user_key}:at", 0)) or time.time()
        wait = max(1, int(lockout - (time.time() - last)))
        response = JsonResponse(
            {"ok": False, "error": "Too many failed attempts. Sign-in is paused for a few minutes.", "retry_after": wait},
            status=429,
        )
        response["Retry-After"] = str(wait)
        return no_store(response)

    user = None if bad_input else authenticate(request, username=_login_name(username), password=password)
    if user is None or not user.is_active:
        now = time.time()
        for key in (ip_key, user_key):
            cache.set(key, cache.get(key, 0) + 1, lockout)
            cache.set(f"{key}:at", now, lockout)
        record_login(request, LoginAttempt.Area.DASHBOARD, LoginAttempt.Outcome.FAILED, username)
        logger.warning("Failed dashboard login from %s (%s)", ip, username)
        return no_store(error(400, "That username and password don't match."))
    if not user.is_staff:
        record_login(request, LoginAttempt.Area.DASHBOARD, LoginAttempt.Outcome.NOT_STAFF, username)
        return no_store(error(403, "This account can't use the dashboard."))

    cache.delete_many([ip_key, user_key, f"{ip_key}:at", f"{user_key}:at"])
    login(request, user)
    # "Keep me signed in" lasts REMEMBER_ME_DAYS; otherwise the session ends
    # when the browser closes (and after SESSION_COOKIE_AGE at most).
    request.session.set_expiry(settings.DASHBOARD_REMEMBER_DAYS * 86400 if remember else 0)
    record_login(request, LoginAttempt.Area.DASHBOARD, LoginAttempt.Outcome.SUCCESS, username)
    return no_store(
        JsonResponse({"ok": True, "user": _user_payload(user), "csrfToken": get_token(request)})
    )


def _login_name(identifier):
    """Accept an email address as well as a username (staff accounts only)."""
    if "@" not in identifier:
        return identifier
    User = get_user_model()
    matches = list(User.objects.filter(email__iexact=identifier, is_active=True, is_staff=True)[:2])
    # Only an unambiguous match signs in by email; otherwise fall back to
    # treating it as a username (Django usernames may contain @).
    return matches[0].get_username() if len(matches) == 1 else identifier


@require_http_methods(["POST"])
def logout_view(request):
    if not _proxy_ok(request):
        return no_store(error(403, "Not allowed."))
    logout(request)
    return no_store(JsonResponse({"ok": True}))


# --- Overview (analytics) ----------------------------------------------------------


@require_http_methods(["GET"])
@staff_api
def overview(request):
    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        days = 30
    days = days if days in (7, 30, 90, 365) else 30
    return JsonResponse({"ok": True, **analytics.overview(days)})


# --- Members (enquiries) -----------------------------------------------------------

ORDERINGS = {
    "newest": ("-created_at",),
    "oldest": ("created_at",),
    "name": ("first_name", "last_name"),
    "status": ("status", "-created_at"),
}


def _enquiry_dict(e):
    return {
        "reference": e.reference,
        "first_name": e.first_name,
        "last_name": e.last_name,
        "name": e.full_name,
        "email": e.email,
        "phone": e.phone,
        "postcode": e.postcode,
        "property_type": e.property_type,
        "services": e.services,
        "message": e.message,
        "status": e.status,
        "status_label": e.get_status_display(),
        "notes": e.notes,
        "channel": e.channel,
        "source": e.source,
        "utm_campaign": e.utm_campaign,
        "country": e.country,
        "city": e.city,
        "created_at": e.created_at.isoformat(),
        "read": e.read_at is not None,
    }


def _filtered_enquiries(request):
    qs = Enquiry.objects.all()
    q = request.GET.get("q", "").strip()[:100]
    if q:
        qs = qs.filter(
            Q(reference__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(postcode__icontains=q)
            | Q(message__icontains=q)
        )
    status = request.GET.get("status", "")
    if status in Enquiry.Status.values:
        qs = qs.filter(status=status)
    elif status != "all":
        qs = qs.exclude(status=Enquiry.Status.SPAM)
    service = request.GET.get("service", "")
    if service in Enquiry.SERVICE_CHOICES:
        qs = qs.filter(services__icontains=service)
    read = request.GET.get("read", "")
    if read == "unread":
        qs = qs.filter(read_at__isnull=True)
    elif read == "read":
        qs = qs.filter(read_at__isnull=False)
    property_type = request.GET.get("property", "")
    if property_type in Enquiry.PropertyType.values:
        qs = qs.filter(property_type=property_type)
    return qs.order_by(*ORDERINGS.get(request.GET.get("ordering", ""), ORDERINGS["newest"]))


@require_http_methods(["GET"])
@staff_api
def enquiries(request):
    qs = _filtered_enquiries(request)
    try:
        size = max(5, min(100, int(request.GET.get("page_size", 20))))
        page_number = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        size, page_number = 20, 1
    paginator = Paginator(qs, size)
    page = paginator.get_page(page_number)
    counts = dict(Enquiry.objects.values_list("status").annotate(n=Count("id")))
    unread = Enquiry.objects.filter(read_at__isnull=True).exclude(status=Enquiry.Status.SPAM).count()
    return JsonResponse(
        {
            "ok": True,
            "results": [_enquiry_dict(e) for e in page.object_list],
            "count": paginator.count,
            "page": page.number,
            "pages": paginator.num_pages,
            "page_size": size,
            "status_counts": {s: counts.get(s, 0) for s in Enquiry.Status.values},
            "unread": unread,
            "statuses": [{"value": v, "label": l} for v, l in Enquiry.Status.choices],
            "services": Enquiry.SERVICE_CHOICES,
        }
    )


@require_http_methods(["GET", "PATCH", "DELETE"])
@staff_api
def enquiry_detail(request, reference):
    try:
        enquiry = Enquiry.objects.get(reference=reference)
    except Enquiry.DoesNotExist:
        return error(404, "Enquiry not found.")
    if request.method == "DELETE":
        logger.info("Enquiry %s deleted by %s", enquiry.reference, request.user.get_username())
        enquiry.delete()
        return JsonResponse({"ok": True})
    if request.method == "PATCH":
        data = _json_body(request)
        if data is None:
            return error(400, "Send the changes as JSON.")
        if "status" in data:
            if data["status"] not in Enquiry.Status.values:
                return error(400, "Unknown status.")
            enquiry.status = data["status"]
        if "notes" in data:
            enquiry.notes = clean_text(str(data["notes"] or ""), multiline=True)[:5000]
        if "read" in data:
            if not isinstance(data["read"], bool):
                return error(400, "read must be true or false.")
            enquiry.read_at = (enquiry.read_at or timezone.now()) if data["read"] else None
        enquiry.save(update_fields=["status", "notes", "read_at"])
    return JsonResponse({"ok": True, "enquiry": _enquiry_dict(enquiry)})


CSV_FIELDS = [
    ("Reference", "reference"),
    ("Received", "created_at"),
    ("Status", "status_label"),
    ("First name", "first_name"),
    ("Last name", "last_name"),
    ("Email", "email"),
    ("Phone", "phone"),
    ("Postcode", "postcode"),
    ("Property", "property_type"),
    ("Services", "services"),
    ("Message", "message"),
    ("Notes", "notes"),
    ("Channel", "channel"),
    ("Source", "source"),
    ("Campaign", "utm_campaign"),
    ("Country", "country"),
    ("City", "city"),
]


def _csv_safe(value):
    # Stop spreadsheet apps running a customer's text as a formula.
    text = ", ".join(value) if isinstance(value, list) else str(value or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@require_http_methods(["GET"])
@staff_api
def enquiries_export(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localtime().strftime("%Y-%m-%d")
    response["Content-Disposition"] = f'attachment; filename="dapperwalls-enquiries-{stamp}.csv"'
    response.write("﻿")  # so Excel opens it as UTF-8
    writer = csv.writer(response)
    writer.writerow([label for label, _ in CSV_FIELDS])
    for e in _filtered_enquiries(request).iterator():
        row = _enquiry_dict(e)
        row["created_at"] = timezone.localtime(e.created_at).strftime("%Y-%m-%d %H:%M")
        writer.writerow([_csv_safe(row[key]) for _, key in CSV_FIELDS])
    return response


# --- Settings ----------------------------------------------------------------------


def _settings_payload(request):
    s = DashboardSettings.load()
    return {
        "ok": True,
        "user": _user_payload(request.user),
        "notify_emails": s.notify_list,
        "default_notify_emails": list(settings.ENQUIRY_NOTIFY_EMAIL),
        "analytics_retention_days": s.analytics_retention_days,
        "django_admin_url": f"{settings.API_PUBLIC_URL}/{settings.ADMIN_URL}",
    }


@require_http_methods(["GET", "PUT"])
@staff_api
def settings_view(request):
    if request.method == "PUT":
        data = _json_body(request)
        if data is None:
            return error(400, "Send the settings as JSON.")
        errors = {}
        s = DashboardSettings.load()
        if "notify_emails" in data:
            emails = data["notify_emails"]
            if not isinstance(emails, list) or len(emails) > 10:
                errors["notify_emails"] = "Add up to 10 email addresses."
            else:
                cleaned = []
                for e in emails:
                    e = str(e).strip().lower()[:254]
                    if not e:
                        continue
                    try:
                        validate_email(e)
                    except ValidationError:
                        errors["notify_emails"] = f"{e} isn't a valid email address."
                        break
                    if e not in cleaned:
                        cleaned.append(e)
                s.notify_emails = ", ".join(cleaned)
        if "analytics_retention_days" in data:
            try:
                days = int(data["analytics_retention_days"])
            except (TypeError, ValueError):
                days = 0
            if not 30 <= days <= 1095:
                errors["analytics_retention_days"] = "Choose between 30 and 1095 days."
            s.analytics_retention_days = days
        user = request.user
        profile = data.get("user") if isinstance(data.get("user"), dict) else {}
        if profile:
            user.first_name = clean_text(str(profile.get("first_name") or ""))[:150]
            user.last_name = clean_text(str(profile.get("last_name") or ""))[:150]
            email = str(profile.get("email") or "").strip()[:254]
            if email:
                try:
                    validate_email(email)
                    user.email = email
                except ValidationError:
                    errors["email"] = "That email address isn't valid."
            else:
                user.email = ""
        if errors:
            return JsonResponse({"ok": False, "errors": errors}, status=400)
        s.save()
        if profile:
            user.save(update_fields=["first_name", "last_name", "email"])
    return JsonResponse(_settings_payload(request))


@require_http_methods(["POST"])
@staff_api
def change_password(request):
    data = _json_body(request)
    if data is None:
        return error(400, "Send the passwords as JSON.")
    user = request.user
    if not user.check_password(str(data.get("current_password") or "")):
        return JsonResponse({"ok": False, "errors": {"current_password": "That isn't your current password."}}, status=400)
    new = str(data.get("new_password") or "")
    try:
        validate_password(new, user)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "errors": {"new_password": " ".join(exc.messages)}}, status=400)
    user.set_password(new)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    logger.info("Dashboard password changed for %s", user.get_username())
    return JsonResponse({"ok": True, "csrfToken": get_token(request)})


# --- Password reset ------------------------------------------------------------------

RESET_PER_HOUR = 5


def _hourly_limit(key, limit):
    bucket = f"{key}:{timezone.now():%Y%m%d%H}"
    count = cache.get(bucket, 0)
    if count >= limit:
        return False
    cache.set(bucket, count + 1, 3600)
    return True


@require_http_methods(["POST"])
def password_reset_request(request):
    """Email a reset link to a staff account. The answer is the same whether or
    not the address matches an account, so it can't be used to find admins."""
    if not _proxy_ok(request):
        return no_store(error(403, "Not allowed."))
    data = _json_body(request)
    email = str((data or {}).get("email") or "").strip()[:254]
    try:
        validate_email(email)
    except ValidationError:
        return no_store(JsonResponse({"ok": False, "errors": {"email": "Enter a valid email address."}}, status=400))

    ip = get_client_ip(request) or "unknown"
    done = no_store(JsonResponse({"ok": True}))
    if not _hourly_limit(f"pw-reset-ip:{ip}", RESET_PER_HOUR) or not _hourly_limit(f"pw-reset-email:{email.lower()}", 3):
        logger.warning("Password reset rate limited for %s (%s)", ip, email)
        return done

    User = get_user_model()
    for user in User.objects.filter(email__iexact=email, is_active=True, is_staff=True):
        if not user.has_usable_password():
            continue
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        link = f"{settings.SITE_URL}/admin/reset/?uid={uid}&token={token}"
        try:
            send_password_reset(user, link)
            logger.info("Password reset link sent for %s", user.get_username())
        except Exception:
            logger.exception("Couldn't send a password reset email")
    return done


@require_http_methods(["POST"])
def password_reset_confirm(request):
    if not _proxy_ok(request):
        return no_store(error(403, "Not allowed."))
    data = _json_body(request) or {}
    ip = get_client_ip(request) or "unknown"
    if not _hourly_limit(f"pw-reset-confirm:{ip}", 20):
        return no_store(error(429, "Too many attempts. Try again later.", Retry_After="3600"))

    uid, token, new = data.get("uid"), data.get("token"), data.get("new_password")
    if not all(isinstance(v, str) for v in (uid, token, new)) or len(uid) > 64 or len(token) > 128:
        return no_store(error(400, "This reset link isn't valid. Request a new one."))
    User = get_user_model()
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)), is_active=True, is_staff=True)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is None or not default_token_generator.check_token(user, token):
        return no_store(error(400, "This reset link has expired or was already used. Request a new one."))
    if len(new) > 256:
        return no_store(JsonResponse({"ok": False, "errors": {"new_password": "That password is too long."}}, status=400))
    try:
        validate_password(new, user)
    except ValidationError as exc:
        return no_store(JsonResponse({"ok": False, "errors": {"new_password": " ".join(exc.messages)}}, status=400))
    user.set_password(new)
    user.save(update_fields=["password"])  # also invalidates the token and other sessions
    logger.info("Password reset completed for %s", user.get_username())
    return no_store(JsonResponse({"ok": True}))
