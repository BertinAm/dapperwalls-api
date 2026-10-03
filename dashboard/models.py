from django.db import models


class VisitEvent(models.Model):
    """One page view or tracked action on dapperwalls.co.uk.

    Cookieless: nothing is stored on the visitor's device. `visitor` is a
    hash of the IP address and browser that changes every day (see
    tracking.visitor_hash), so we can count unique visitors per day without
    keeping IP addresses or following anyone across days.
    """

    class Kind(models.TextChoices):
        PAGEVIEW = "pageview", "Page view"
        EVENT = "event", "Event"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PAGEVIEW)
    # For events: quote_open, email_click, social_click, ...
    name = models.CharField(max_length=40, blank=True)
    label = models.CharField(max_length=80, blank=True)

    visitor = models.CharField(max_length=32, db_index=True)
    path = models.CharField(max_length=200, default="/")

    # Where the visit came from.
    channel = models.CharField(max_length=20, blank=True, db_index=True)
    source = models.CharField(max_length=80, blank=True)
    referrer_host = models.CharField(max_length=120, blank=True)
    utm_source = models.CharField(max_length=80, blank=True)
    utm_medium = models.CharField(max_length=80, blank=True)
    utm_campaign = models.CharField(max_length=120, blank=True)

    # Coarse location from Cloudflare (never the IP address itself).
    country = models.CharField(max_length=2, blank=True)
    region = models.CharField(max_length=80, blank=True)
    city = models.CharField(max_length=80, blank=True)

    device = models.CharField(max_length=10, blank=True)
    browser = models.CharField(max_length=20, blank=True)
    os = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} {self.name or self.path} {self.created_at:%Y-%m-%d %H:%M}"


class LoginAttempt(models.Model):
    class Area(models.TextChoices):
        DASHBOARD = "dashboard", "Dashboard"
        DJANGO_ADMIN = "django-admin", "Django admin"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Wrong username or password"
        BLOCKED = "blocked", "Blocked (too many attempts)"
        NOT_STAFF = "not-staff", "Not an admin account"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    area = models.CharField(max_length=15, choices=Area.choices)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    username = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    country = models.CharField(max_length=2, blank=True)
    city = models.CharField(max_length=80, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_outcome_display()} {self.username} {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def success(self):
        return self.outcome == self.Outcome.SUCCESS


class DashboardSettings(models.Model):
    """Single row of settings the owner can change from the dashboard."""

    notify_emails = models.TextField(
        blank=True,
        help_text="Comma-separated addresses that get new-enquiry emails. Empty uses ENQUIRY_NOTIFY_EMAIL.",
    )
    analytics_retention_days = models.PositiveIntegerField(default=395)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "dashboard settings"
        verbose_name_plural = "dashboard settings"

    def __str__(self):
        return "Dashboard settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def notify_list(self):
        return [e.strip() for e in self.notify_emails.split(",") if e.strip()]
