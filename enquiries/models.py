import secrets

from django.db import models

REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I


def generate_reference():
    return "DW-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))


class Enquiry(models.Model):
    class PropertyType(models.TextChoices):
        RESIDENTIAL = "Residential"
        COMMERCIAL = "Commercial"

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUOTED = "quoted", "Quoted"
        WON = "won", "Won"
        LOST = "lost", "Lost"
        SPAM = "spam", "Spam"

    SERVICE_CHOICES = [
        "Painting & decorating",
        "Venetian plaster or another decorative finish",
        "Wallpaper installation",
        "Not sure yet",
    ]

    reference = models.CharField(max_length=12, unique=True, editable=False)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField(max_length=254)
    phone = models.CharField(max_length=40, blank=True)
    postcode = models.CharField(max_length=12, blank=True)
    property_type = models.CharField(
        max_length=20, choices=PropertyType.choices, default=PropertyType.RESIDENTIAL
    )
    services = models.JSONField(default=list, blank=True)
    message = models.TextField()

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    notes = models.TextField(blank=True, help_text="Internal notes. Never shown to the customer.")

    # Where the customer came from (first page view that day) and roughly
    # where they are, from the cookieless site analytics.
    channel = models.CharField(max_length=20, blank=True)
    source = models.CharField(max_length=80, blank=True)
    utm_campaign = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=2, blank=True)
    city = models.CharField(max_length=80, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # When the owner first opened it in the dashboard's Messages inbox.
    read_at = models.DateTimeField(null=True, blank=True)
    notification_sent_at = models.DateTimeField(null=True, blank=True)
    confirmation_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "enquiries"

    def __str__(self):
        return f"{self.reference} {self.full_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_reference()
            while Enquiry.objects.filter(reference=self.reference).exists():
                self.reference = generate_reference()
        super().save(*args, **kwargs)
