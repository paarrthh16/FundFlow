from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from reconciliation.exceptions import DomainError


def exception_handler(exc, context):
    """Map domain exceptions to `{"detail": ...}` responses; everything else uses DRF's default."""
    if isinstance(exc, DomainError):
        return Response({"detail": exc.detail}, status=exc.status_code)
    return drf_exception_handler(exc, context)
