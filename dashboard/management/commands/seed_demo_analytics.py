"""Fill a LOCAL database with made-up visits, enquiries and login attempts so
the dashboard's charts can be checked. Refuses to run unless DEBUG is on."""
import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from dashboard.models import LoginAttempt, VisitEvent
from enquiries.models import Enquiry

CHANNELS = [
    ("Google Ads", "google", "", 26),
    ("Search", "google", "google.com", 30),
    ("Social", "Instagram", "instagram.com", 12),
    ("Social", "Facebook", "facebook.com", 6),
    ("Social", "TikTok", "tiktok.com", 4),
    ("Referral", "checkatrade.com", "checkatrade.com", 8),
    ("Direct", "", "", 18),
]
PLACES = [("GB", "England", "Chester", 30), ("GB", "Wales", "Wrexham", 8), ("GB", "England", "Liverpool", 10),
          ("GB", "England", "Manchester", 9), ("GB", "England", "London", 6), ("GB", "Wales", "Mold", 4),
          ("IE", "Leinster", "Dublin", 2), ("NG", "Lagos", "Lagos", 2), ("US", "California", "San Jose", 1)]
DEVICES = [("Mobile", "Safari", "iOS", 45), ("Mobile", "Chrome", "Android", 22), ("Desktop", "Chrome", "Windows", 18),
           ("Desktop", "Safari", "macOS", 10), ("Tablet", "Safari", "iOS", 5)]
NAMES = [("Sarah", "Hughes"), ("James", "Roberts"), ("Priya", "Patel"), ("Tom", "Davies"), ("Emma", "Wilson"),
         ("Chris", "Evans"), ("Olivia", "Jones"), ("Mark", "Taylor"), ("Hannah", "Morgan"), ("David", "Clarke")]
MESSAGES = [
    "Looking for Venetian plaster on a feature wall in our living room, roughly 4m wide.",
    "Three bedrooms and a hallway need repainting before we move in next month.",
    "We'd like a limewash finish in the restaurant dining area. Can you send a quote?",
    "Wallpaper for a downstairs loo and a bedroom feature wall. We have the paper already.",
    "Polished plaster for a bathroom, please. Is it suitable around a shower?",
]


def pick(rows):
    return random.choices(rows, weights=[r[-1] for r in rows])[0]


class Command(BaseCommand):
    help = "LOCAL ONLY: add demo analytics data for checking the dashboard (needs DEBUG=True)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--clear", action="store_true", help="Delete existing analytics and demo enquiries first.")

    def handle(self, *args, days, clear, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to add demo data: DEBUG is off (this looks like a live server).")
        if clear:
            VisitEvent.objects.all().delete()
            LoginAttempt.objects.all().delete()
            Enquiry.objects.filter(email__endswith="@example.com").delete()

        now = timezone.now()
        events, logins, enquiries = [], [], 0
        for d in range(days, -1, -1):
            day = now - timedelta(days=d)
            weekday_boost = 1.3 if day.weekday() in (0, 1, 6) else 1.0
            for v in range(int(random.randint(8, 22) * weekday_boost * (1 + (days - d) / days))):
                at = day.replace(hour=random.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 3, 6, 8, 9, 9, 8, 9, 8, 7, 7, 8, 10, 12, 13, 12, 9, 5, 2])[0],
                                 minute=random.randint(0, 59))
                if at > now:
                    continue
                visitor = f"demo{d:03d}{v:03d}".ljust(32, "0")
                channel, source, host, _ = pick(CHANNELS)
                country, region, city, _ = pick(PLACES)
                device, browser, os_name, _ = pick(DEVICES)
                common = dict(visitor=visitor, country=country, region=region, city=city, device=device, browser=browser, os=os_name)
                campaign = random.choice(["chester-venetian-plaster", "chester-painters"]) if channel == "Google Ads" else ""
                events.append(VisitEvent(created_at=at, kind="pageview", path="/", channel=channel, source=source,
                                         referrer_host=host, utm_campaign=campaign, **common))
                if random.random() < 0.08:
                    events.append(VisitEvent(created_at=at + timedelta(minutes=2), kind="pageview", path="/privacy/", **common))
                if random.random() < 0.14:
                    events.append(VisitEvent(created_at=at + timedelta(minutes=1), kind="event", name="quote_open", path="/", **common))
                    if random.random() < 0.35:
                        first, last = random.choice(NAMES)
                        e = Enquiry.objects.create(
                            first_name=first, last_name=last, email=f"{first.lower()}.{last.lower()}{d}@example.com",
                            phone="07700 900" + str(random.randint(100, 999)), postcode=random.choice(["CH1 2AB", "CH4 7QP", "L18 3HG", "LL11 1AA", ""]),
                            property_type=random.choice(["Residential"] * 4 + ["Commercial"]),
                            services=random.sample(Enquiry.SERVICE_CHOICES[:3], k=random.randint(1, 2)),
                            message=random.choice(MESSAGES), channel=channel, source=source, utm_campaign=campaign,
                            country=country, city=city,
                            status=random.choices(["new", "contacted", "quoted", "won", "lost"], weights=[3, 3, 3, 2, 1])[0] if d > 2 else "new",
                        )
                        Enquiry.objects.filter(pk=e.pk).update(created_at=at + timedelta(minutes=4))
                        enquiries += 1
                if random.random() < 0.05:
                    events.append(VisitEvent(created_at=at, kind="event", name=random.choice(["email_click", "social_click", "checkatrade_click"]),
                                             label=random.choice(["Instagram", "TikTok", ""]), path="/", **common))
            if random.random() < 0.6:
                logins.append(LoginAttempt(created_at=day, area="dashboard", outcome="success", username="emmanuel", country="GB", city="Chester"))
            for _ in range(random.choices([0, 0, 0, 1, 3, 8], k=1)[0]):
                logins.append(LoginAttempt(created_at=day - timedelta(minutes=random.randint(0, 600)), area=random.choice(["dashboard", "django-admin"]),
                                           outcome=random.choice(["failed", "failed", "blocked"]), username=random.choice(["admin", "root", "test", "emmanuel"]),
                                           ip_address=f"185.{random.randint(1, 250)}.{random.randint(1, 250)}.{random.randint(1, 250)}",
                                           country=random.choice(["RU", "CN", "US", "NL", "GB"])))

        # auto_now_add ignores the values we set, so insert, then fix the times.
        for batch in (events, logins):
            if not batch:
                continue
            model = type(batch[0])
            times = [obj.created_at for obj in batch]
            created = model.objects.bulk_create(batch)
            for obj, at in zip(created, times):
                obj.created_at = at
            model.objects.bulk_update(created, ["created_at"], batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Added {len(events)} visit events, {enquiries} enquiries and {len(logins)} login attempts."))
