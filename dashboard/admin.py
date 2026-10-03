from django.contrib import admin

from .models import DashboardSettings, LoginAttempt, VisitEvent


@admin.register(VisitEvent)
class VisitEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "name", "path", "channel", "source", "country", "city", "device")
    list_filter = ("kind", "channel", "device", "country")
    search_fields = ("path", "source", "referrer_host", "utm_campaign", "city")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("created_at", "area", "outcome", "username", "ip_address", "country", "city")
    list_filter = ("area", "outcome")
    search_fields = ("username", "ip_address")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DashboardSettings)
class DashboardSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "notify_emails", "analytics_retention_days", "updated_at")

    def has_add_permission(self, request):
        return not DashboardSettings.objects.exists()
