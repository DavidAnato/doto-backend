"""Sonde santé — module séparé pour ne pas importer les vues depuis config.urls."""
from django.urls import path

from .views import health

urlpatterns = [
    path("health/", health, name="health"),
]
