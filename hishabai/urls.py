from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("manage/", admin.site.urls),  # Django's built-in database admin; the day-to-day panel is /admin/
    path("", include("core.urls")),
]
