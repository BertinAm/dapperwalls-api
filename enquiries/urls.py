from django.urls import re_path

from . import views

# Trailing slash optional so a POST without one doesn't hit APPEND_SLASH.
urlpatterns = [
    re_path(r"^enquiries/?$", views.create_enquiry, name="create-enquiry"),
    re_path(r"^health/?$", views.health, name="health"),
]
