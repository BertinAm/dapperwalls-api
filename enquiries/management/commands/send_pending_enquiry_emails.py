from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from enquiries.emails import send_enquiry_emails
from enquiries.models import Enquiry


class Command(BaseCommand):
    help = (
        "Retry unsent notification/confirmation emails for enquiries from the "
        "last 7 days (skipping spam). Run from cPanel cron every 10 minutes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument(
            "--min-age-seconds",
            type=int,
            default=120,
            help="Skip very new enquiries whose request may still be sending.",
        )

    def handle(self, *args, days, min_age_seconds, **options):
        now = timezone.now()
        pending = (
            Enquiry.objects.filter(
                created_at__gte=now - timedelta(days=days),
                created_at__lte=now - timedelta(seconds=min_age_seconds),
            )
            .exclude(status=Enquiry.Status.SPAM)
            .filter(Q(notification_sent_at__isnull=True) | Q(confirmation_sent_at__isnull=True))
            .order_by("created_at")
        )
        total = still_pending = 0
        for enquiry in pending:
            total += 1
            send_enquiry_emails(enquiry)
            if not (enquiry.notification_sent_at and enquiry.confirmation_sent_at):
                still_pending += 1
        if (total and options["verbosity"] >= 1) or options["verbosity"] > 1:
            self.stdout.write(
                f"Processed {total} enquiries with unsent emails; {still_pending} still pending."
            )
