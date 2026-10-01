import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

from .client_ip import get_client_ip

logger = logging.getLogger(__name__)


class AdminLoginThrottleMiddleware:
    """Slow down password guessing on the Django admin login.

    After ADMIN_LOGIN_MAX_FAILURES failed logins from one IP, further login
    attempts from it are refused for ADMIN_LOGIN_LOCKOUT_SECONDS. A failed
    login is a POST that re-renders the form (200); success redirects (302).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.login_path = f"/{settings.ADMIN_URL}login/"

    def __call__(self, request):
        if request.method != "POST" or request.path != self.login_path:
            return self.get_response(request)

        ip = get_client_ip(request) or "unknown"
        key = f"admin-login-fail:{ip}"
        failures = cache.get(key, 0)
        if failures >= settings.ADMIN_LOGIN_MAX_FAILURES:
            logger.warning("Admin login blocked for %s after %s failures", ip, failures)
            return HttpResponse(
                "Too many failed login attempts. Try again in 15 minutes.",
                status=429,
                content_type="text/plain",
                headers={"Retry-After": str(settings.ADMIN_LOGIN_LOCKOUT_SECONDS)},
            )

        response = self.get_response(request)
        if response.status_code == 200:
            cache.set(key, failures + 1, settings.ADMIN_LOGIN_LOCKOUT_SECONDS)
            logger.warning("Failed admin login from %s (%s)", ip, failures + 1)
        elif response.status_code in (301, 302):
            cache.delete(key)
        return response
