from django.urls import re_path

from . import views

# Mounted at /api/. Trailing slashes optional, like the enquiries API.
urlpatterns = [
    re_path(r"^collect/?$", views.collect, name="collect"),
    re_path(r"^admin/session/?$", views.session, name="dash-session"),
    re_path(r"^admin/login/?$", views.login_view, name="dash-login"),
    re_path(r"^admin/logout/?$", views.logout_view, name="dash-logout"),
    re_path(r"^admin/overview/?$", views.overview, name="dash-overview"),
    re_path(r"^admin/enquiries/?$", views.enquiries, name="dash-enquiries"),
    re_path(r"^admin/enquiries/export/?$", views.enquiries_export, name="dash-enquiries-export"),
    re_path(r"^admin/enquiries/(?P<reference>DW-[A-Z0-9]{6})/?$", views.enquiry_detail, name="dash-enquiry"),
    re_path(r"^admin/settings/?$", views.settings_view, name="dash-settings"),
    re_path(r"^admin/password/?$", views.change_password, name="dash-password"),
    re_path(r"^admin/password-reset/?$", views.password_reset_request, name="dash-password-reset"),
    re_path(r"^admin/password-reset/confirm/?$", views.password_reset_confirm, name="dash-password-reset-confirm"),
]
