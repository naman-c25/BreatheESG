from django.urls import path, include, re_path
from django.http import JsonResponse, HttpResponse, Http404
from django.conf import settings
from pathlib import Path


def health(_):
    return JsonResponse({"ok": True})


def spa(_request):
    """Serve the built React index.html. Anything not under /api/ falls here."""
    index = Path(settings.BASE_DIR).parent / "frontend" / "dist" / "index.html"
    if not index.exists():
        return HttpResponse(
            "Frontend not built. Run `cd frontend && npm run build`.",
            status=503, content_type="text/plain",
        )
    return HttpResponse(index.read_bytes(), content_type="text/html")


urlpatterns = [
    path("health/", health),
    path("api/", include("core.urls")),
    re_path(r"^.*$", spa),
]
