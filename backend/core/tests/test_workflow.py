"""
Tests for the analyst workflow primitives that aren't adapter-specific:
  - reject_activity (status transition, audit, reason required, locked-row guard)
  - ingest endpoint's three modes: upload / paste / pull
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, Client
from core.models import (
    Tenant, User, Source, EmissionCategory, EmissionActivity, AuditLogEntry,
)
from core.services import approve_activity, reject_activity


class RejectWorkflowTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="T")
        self.user = User.objects.create(tenant=self.tenant, email="u@e", display_name="U")
        self.source = Source.objects.create(tenant=self.tenant, name="S", kind="sap_flatfile")
        self.cat = EmissionCategory.objects.create(scope=1, category="X", subcategory="Y", canonical_unit="L")

    def _activity(self, status="pending"):
        return EmissionActivity.objects.create(
            tenant=self.tenant, source=self.source, category=self.cat,
            activity_date=date(2025, 4, 1),
            quantity_original=Decimal("100"), unit_original="L",
            status=status,
        )

    def test_reject_requires_reason(self):
        a = self._activity()
        with self.assertRaises(ValueError):
            reject_activity(a, self.user, "")
        with self.assertRaises(ValueError):
            reject_activity(a, self.user, "   ")

    def test_reject_writes_audit_entry(self):
        a = self._activity()
        reject_activity(a, self.user, "duplicate of another row")
        a.refresh_from_db()
        self.assertEqual(a.status, "rejected")
        log = AuditLogEntry.objects.get(entity_id=a.id, action="rejected")
        self.assertEqual(log.reason, "duplicate of another row")
        self.assertEqual(log.before, {"status": "pending"})
        self.assertEqual(log.after, {"status": "rejected"})

    def test_locked_row_cannot_be_rejected(self):
        # Locked is the only terminal state besides rejected itself.
        # Corrections to locked rows must be issued as adjustments, not rejections.
        a = self._activity(status="locked")
        with self.assertRaises(ValueError) as cx:
            reject_activity(a, self.user, "x")
        self.assertIn("Locked", str(cx.exception))

    def test_rejected_activities_excluded_from_summary_totals(self):
        # Approved rows count; rejected rows do not.
        a1 = self._activity()
        approve_activity(a1, self.user)
        a2 = self._activity()
        reject_activity(a2, self.user, "x")
        approved = EmissionActivity.objects.filter(tenant=self.tenant, status__in=["approved", "locked"]).count()
        self.assertEqual(approved, 1)


def _login(client: Client, user: User):
    """Bypass /api/auth/login by creating a session row directly."""
    import secrets
    from core.models import Session
    from core.middleware import SESSION_COOKIE
    sess = Session.objects.create(user=user, token=secrets.token_urlsafe(32))
    client.cookies[SESSION_COOKIE] = sess.token
    return client


class IngestModesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tenant = Tenant.objects.create(name="T")
        self.user = User.objects.create(tenant=self.tenant, email="u@e", display_name="U")
        self.user.set_password("x"); self.user.save()
        _login(self.client, self.user)

    def test_paste_mode_ingests_json(self):
        src = Source.objects.create(tenant=self.tenant, name="S", kind="travel_api")
        body = '{"bookings":[{"id":"B1","status":"TICKETED","traveler":{"email":"a@b"},"airSegments":[{"from":"JFK","to":"LHR","cabin":"economy","departureDate":"2025-04-08"}]}]}'
        resp = self.client.post("/api/ingest/", {
            "source": str(src.id), "mode": "paste",
            "content": body, "file_name": "pasted.json",
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        run = resp.json()
        self.assertEqual(run["row_count_normalized"], 1)
        self.assertEqual(run["file_name"], "pasted.json")

    def test_paste_mode_requires_content(self):
        src = Source.objects.create(tenant=self.tenant, name="S", kind="travel_api")
        resp = self.client.post("/api/ingest/", {"source": str(src.id), "mode": "paste"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("content", resp.json()["detail"])

    def test_pull_mode_requires_configured_fixture(self):
        src = Source.objects.create(tenant=self.tenant, name="S", kind="travel_api")
        # No pull_fixture in adapter_config → 400 with explanatory message
        resp = self.client.post("/api/ingest/", {"source": str(src.id), "mode": "pull"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not configured", resp.json()["detail"])

    def test_unknown_mode_rejected(self):
        src = Source.objects.create(tenant=self.tenant, name="S", kind="travel_api")
        resp = self.client.post("/api/ingest/", {"source": str(src.id), "mode": "telepathy"})
        self.assertEqual(resp.status_code, 400)
