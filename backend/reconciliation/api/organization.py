"""Tenant resolution from the X-Organization header (brief §4, PRD §9, D-18)."""
from rest_framework.views import APIView

from reconciliation.exceptions import NotFoundError, OrganizationHeaderError
from reconciliation.models import Organization

ORGANIZATION_HEADER = "X-Organization"


def resolve_organization(request):
    slug = request.headers.get(ORGANIZATION_HEADER, "").strip()
    if not slug:
        raise OrganizationHeaderError(f"The {ORGANIZATION_HEADER} header is required.")
    try:
        return Organization.objects.get(slug=slug)  # exact, case-sensitive match
    except Organization.DoesNotExist:
        raise NotFoundError("Organization not found.") from None


class OrganizationScopedAPIView(APIView):
    """Resolves `self.organization` before any handler runs; every query must be scoped to it."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.organization = resolve_organization(request)
