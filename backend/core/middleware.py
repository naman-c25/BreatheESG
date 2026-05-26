from django.http import JsonResponse
from .models import Tenant, User


class TenantMiddleware:
    """
    Reads X-Tenant-Id from the request header. Stashes tenant + a
    seeded analyst user on the request.

    This is intentionally a stub for authn. See DECISIONS.md §auth.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.user_obj = None
        if request.path.startswith("/api/"):
            tenant_id = request.headers.get("X-Tenant-Id")
            if tenant_id:
                try:
                    request.tenant = Tenant.objects.get(pk=tenant_id)
                    request.user_obj = User.objects.filter(tenant=request.tenant).first()
                except (Tenant.DoesNotExist, ValueError):
                    return JsonResponse({"detail": "Unknown tenant"}, status=400)
        return self.get_response(request)
