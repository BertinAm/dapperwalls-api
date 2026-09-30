import csv

from django.contrib import admin
from django.http import HttpResponse
from django.utils import timezone

from .models import Enquiry


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "full_name",
        "email",
        "services_display",
        "property_type",
        "status",
        "created_at",
    )
    list_display_links = ("reference", "full_name")
    list_filter = ("status", "property_type", "created_at")
    search_fields = ("reference", "first_name", "last_name", "email", "phone", "postcode", "message")
    date_hierarchy = "created_at"
    list_per_page = 50
    readonly_fields = (
        "reference",
        "created_at",
        "ip_address",
        "user_agent",
        "notification_sent_at",
        "confirmation_sent_at",
    )
    fieldsets = (
        (None, {"fields": ("reference", "status", "notes")}),
        ("Customer", {"fields": ("first_name", "last_name", "email", "phone", "postcode")}),
        ("Enquiry", {"fields": ("property_type", "services", "message")}),
        (
            "Technical",
            {
                "classes": ("collapse",),
                "fields": (
                    "created_at",
                    "ip_address",
                    "user_agent",
                    "notification_sent_at",
                    "confirmation_sent_at",
                ),
            },
        ),
    )
    actions = ("mark_contacted", "mark_spam", "export_csv")

    @admin.display(description="Name", ordering="last_name")
    def full_name(self, obj):
        return obj.full_name

    @admin.display(description="Services")
    def services_display(self, obj):
        return ", ".join(obj.services or []) or "-"

    @admin.action(description="Mark selected as contacted")
    def mark_contacted(self, request, queryset):
        updated = queryset.update(status=Enquiry.Status.CONTACTED)
        self.message_user(request, f"Marked {updated} enquiries as contacted.")

    @admin.action(description="Mark selected as spam")
    def mark_spam(self, request, queryset):
        updated = queryset.update(status=Enquiry.Status.SPAM)
        self.message_user(request, f"Marked {updated} enquiries as spam.")

    @admin.action(description="Export selected to CSV")
    def export_csv(self, request, queryset):
        filename = f"dapperwalls-enquiries-{timezone.localdate():%Y-%m-%d}.csv"
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("﻿")  # BOM so Excel reads UTF-8 correctly
        writer = csv.writer(response)
        columns = [
            "reference", "created_at", "status", "first_name", "last_name", "email",
            "phone", "postcode", "property_type", "services", "message", "notes",
        ]
        writer.writerow(columns)
        for e in queryset:
            row = [getattr(e, c) for c in columns]
            row[1] = timezone.localtime(e.created_at).strftime("%Y-%m-%d %H:%M")
            row[9] = "; ".join(e.services or [])
            writer.writerow([_csv_safe(v) for v in row])
        return response


def _csv_safe(value):
    """Stop spreadsheet apps treating customer text as a formula."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
