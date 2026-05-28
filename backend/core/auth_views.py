"""
Session cookie auth — minimal, honest implementation.
  - POST /api/auth/login/  { email, password } → HttpOnly cookie set, user+tenant return
  - POST /api/auth/logout/  → session row delete + cookie clear
  - GET  /api/auth/me/      → current user+tenant ya 401

JWT, refresh tokens, OAuth, password reset — kuch nahi ship kiya.
HttpOnly cookie XSS-safe hai, server-side session = one-click revocation.
"Stateless JWT scales" — bhai yeh internal B2B tool hai, scale problem nahi.
"""
import secrets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http
from .models import User, Session
from .middleware import SESSION_COOKIE


def _user_payload(u: User) -> dict:
    return {
        "user": {"id": str(u.id), "email": u.email, "display_name": u.display_name},
        "tenant": {
            "id": str(u.tenant.id),
            "name": u.tenant.name,
            "default_region": u.tenant.default_region,
            "default_currency": u.tenant.default_currency,
        },
    }


class LoginView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        # Bad email aur bad password pe same error — kaunsa galat hai
        # yeh enumerate karne ka chance attacker ko mat do.
        user = User.objects.filter(email__iexact=email).first()
        if not user or not user.password_hash or not user.check_password(password):
            return Response({"detail": "Invalid email or password"}, status=http.HTTP_401_UNAUTHORIZED)
        sess = Session.objects.create(user=user, token=secrets.token_urlsafe(32))
        resp = Response(_user_payload(user))
        resp.set_cookie(
            SESSION_COOKIE, sess.token,
            httponly=True, samesite="Lax", max_age=60 * 60 * 24 * 30,
            # secure=True in production; left off here so the demo works over both http/https.
        )
        return resp


class LogoutView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        if getattr(request, "session_obj", None):
            request.session_obj.delete()
        resp = Response({"ok": True})
        resp.delete_cookie(SESSION_COOKIE)
        return resp


class MeView(APIView):
    def get(self, request):
        return Response(_user_payload(request.user_obj))
