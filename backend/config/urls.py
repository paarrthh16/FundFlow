# The Django admin is intentionally not routed (PRD D-27).
from django.urls import include, path

urlpatterns = [
    path("api/", include("reconciliation.api.urls")),
]
