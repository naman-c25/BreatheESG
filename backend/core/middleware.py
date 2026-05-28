from django.http import JsonResponse
from .models import Session


SESSION_COOKIE = "esg_session"

# Endpoints that do not require an authenticated session.
PUBLIC_PATHS = {"/api/auth/login/", "/api/auth/logout/"}


class TenantMiddleware:
    """
    Session cookie se user resolve karte hain, phir user se tenant.
    One tenant per user model — agar koi consultant 3 clients ke saath kaam
    karta hai, woh 3 alag User rows banwayega (Linear/Notion ka same pattern).

    /api/ ke saare endpoints authenticated session maangte hain, sirf
    PUBLIC_PATHS exception hai. Frontend SPA route untouched.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.user_obj = None
        request.session_obj = None

        token = request.COOKIES.get(SESSION_COOKIE)
        if token:
            sess = (
                Session.objects.select_related("user", "user__tenant")
                .filter(token=token)
                .first()
            )
            if sess:
                request.session_obj = sess
                request.user_obj = sess.user
                request.tenant = sess.user.tenant
                sess.save(update_fields=["last_seen_at"])

        if request.path.startswith("/api/") and request.path not in PUBLIC_PATHS:
            if request.user_obj is None:
                return JsonResponse({"detail": "Authentication required"}, status=401)
        return self.get_response(request)
