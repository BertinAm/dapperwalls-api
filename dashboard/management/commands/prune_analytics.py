from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from dashboard.models import DashboardSettings, LoginAttempt, VisitEvent

LOGIN_ATTEMPT_DAYS = 180


class Command(BaseCommand):
    help = "Delete site analytics older than the retention set in the dashboard, and old login attempts."

    def handle(self, *args, **options):
        days = DashboardSettings.load().analytics_retention_days
        now = timezone.now()
        visits, _ = VisitEvent.objects.filter(created_at__lt=now - timedelta(days=days)).delete()
        logins, _ = LoginAttempt.objects.filter(created_at__lt=now - timedelta(days=LOGIN_ATTEMPT_DAYS)).delete()
        self.stdout.write(f"Removed {visits} analytics rows older than {days} days and {logins} old login attempts.")
