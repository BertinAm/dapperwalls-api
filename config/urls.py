from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "DapperWalls enquiries"
admin.site.site_title = "DapperWalls admin"
admin.site.index_title = "Enquiries"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/", include("enquiries.urls")),
]
