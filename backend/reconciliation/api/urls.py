from django.urls import path

from .views import (
    ApproveMatchView,
    HealthView,
    IgnoreTransactionView,
    ImportView,
    LineItemListView,
    OrganizationListView,
    RejectMatchView,
    TransactionListView,
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("organizations/", OrganizationListView.as_view(), name="organizations"),
    path("transactions/", TransactionListView.as_view(), name="transactions"),
    path("line-items/", LineItemListView.as_view(), name="line-items"),
    path("imports/", ImportView.as_view(), name="imports"),
    # <str:pk> (not <int:pk>) so malformed IDs reach the view and get a JSON 404 (D-26) instead of
    # Django's HTML 404; the view validates the ID.
    path("matches/<str:pk>/approve/", ApproveMatchView.as_view(), name="match-approve"),
    path("matches/<str:pk>/reject/", RejectMatchView.as_view(), name="match-reject"),
    path("transactions/<str:pk>/ignore/", IgnoreTransactionView.as_view(), name="transaction-ignore"),
]
