"""Minimal URLconf for Django Admin, served via WSGIMiddleware under /admin/."""
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("", admin.site.urls),
]
