"""
Orchestration layer between adapters and the database.

Lives outside the views so it can be invoked from a Celery task, a
management command, or a test, identically. For the prototype, the API
calls this synchronously. The brief asked for honest tradeoffs, not
overdone infrastructure — see TRADEOFFS.md.
"""
from decimal import Decimal
from datetime import date, timedelta
import hashlib
import json

from django.db import transaction
from django.utils import timezone

from .models import (
    Tenant, Source, IngestionRun, RawRecord, EmissionActivity,
    EmissionCategory, EmissionFactor, Facility, ValidationFlag,
    AuditLogEntry, User,
)
from .adapters import REGISTRY
from .adapters.base import NormalizedRow


# Canonical units per category + the conversion table.
# Kept tiny on purpose. UnitAlias as a DB table would be the next move;
# for a prototype a Python dict is honest.
UNIT_ALIASES = {
    "L": "L", "LTR": "L", "LITRE": "L", "LITER": "L",
    "GAL": "L",  # gallons US → L
    "M3": "L",   # m³ → L (× 1000)
    "KWH": "kWh", "MWH": "kWh",  # MWh → kWh × 1000
    "KM": "km", "MI": "km", "MILES": "km",
    "NIGHTS": "nights",
    "KG": "kg", "T": "kg",
}
UNIT_FACTORS = {
    ("GAL", "L"): Decimal("3.78541"),
    ("M3", "L"): Decimal("1000"),
    ("MWH", "kWh"): Decimal("1000"),
    ("MI", "km"): Decimal("1.60934"),
    ("MILES", "km"): Decimal("1.60934"),
    ("T", "kg"): Decimal("1000"),
}


def canonicalize_unit(raw_unit: str) -> tuple[str | None, Decimal]:
    """Return (canonical_unit, multiplicative_factor) or (None, 1) if unknown."""
    if not raw_unit:
        return None, Decimal("1")
    key = raw_unit.strip().upper()
    canon = UNIT_ALIASES.get(key)
    if canon is None:
        return None, Decimal("1")
    factor = UNIT_FACTORS.get((key, canon), Decimal("1"))
    return canon, factor


def resolve_facility(tenant: Tenant, source_kind: str, source_code: str | None) -> Facility | None:
    if not source_code:
        return None
    field_key = {
        "sap_flatfile": "sap_plant",
        "utility_pdf": "utility_meter",
        "travel_api": None,
    }.get(source_kind)
    if not field_key:
        return None
    # JSONField __contains is Postgres-only. Tenants have few facilities, so
    # iterate in Python — works on SQLite (local dev) and Postgres alike.
    for f in Facility.objects.filter(tenant=tenant):
        if f.source_codes.get(field_key) == source_code:
            return f
    return None


def resolve_factor(category: EmissionCategory, unit: str, region: str, on_date: date) -> EmissionFactor | None:
    qs = EmissionFactor.objects.filter(
        category=category, unit=unit, valid_from__lte=on_date,
    ).filter(
        models_q_valid_to(on_date),
    )
    # Prefer exact region match, then GLOBAL.
    factor = qs.filter(region=region).order_by("-valid_from").first()
    if factor:
        return factor
    return qs.filter(region="GLOBAL").order_by("-valid_from").first()


def models_q_valid_to(on_date):
    from django.db.models import Q
    return Q(valid_to__isnull=True) | Q(valid_to__gte=on_date)


def row_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@transaction.atomic
def run_ingestion(tenant: Tenant, source: Source, file_bytes: bytes, file_name: str, actor: User | None) -> IngestionRun:
    adapter_cls = REGISTRY.get(source.kind)
    if adapter_cls is None:
        raise ValueError(f"No adapter for source kind {source.kind!r}")

    run = IngestionRun.objects.create(
        tenant=tenant, source=source, triggered_by=actor,
        file_name=file_name, status="running",
    )

    parsed = adapter_cls().parse(file_bytes, source.adapter_config or {})
    run.row_count_received = len(parsed.rows) + len(parsed.errors)
    run.error_log = parsed.errors

    normalized = 0
    failed = len(parsed.errors)

    for nrow in parsed.rows:
        try:
            raw = RawRecord.objects.create(
                tenant=tenant,
                ingestion_run=run,
                source_row_ref=nrow.source_row_ref,
                payload=nrow.raw_payload,
                row_hash=row_hash(nrow.raw_payload),
            )
            activity = _build_activity(tenant, source, run, raw, nrow)
            _run_validators(activity, nrow)
            normalized += 1
        except Exception as e:
            run.error_log.append({"row_ref": nrow.source_row_ref, "message": f"Persistence error: {e}"})
            failed += 1

    run.row_count_normalized = normalized
    run.row_count_failed = failed
    run.status = "succeeded" if failed == 0 else ("partial" if normalized > 0 else "failed")
    run.finished_at = timezone.now()
    run.save()

    AuditLogEntry.objects.create(
        tenant=tenant, actor=actor, entity_type="IngestionRun", entity_id=run.id,
        action="re_ingested" if IngestionRun.objects.filter(tenant=tenant, source=source).count() > 1 else "created",
        after={"file_name": file_name, "normalized": normalized, "failed": failed},
    )
    return run


def _build_activity(tenant, source, run, raw, nrow: NormalizedRow) -> EmissionActivity:
    scope, cat_name, subcat = nrow.category_hint
    category, _ = EmissionCategory.objects.get_or_create(
        scope=scope, category=cat_name, subcategory=subcat,
        defaults={"canonical_unit": _default_unit_for(scope, cat_name)},
    )
    facility = resolve_facility(tenant, source.kind, nrow.facility_source_code)

    canon_unit, conv = canonicalize_unit(nrow.unit_original)
    quantity_norm = (nrow.quantity_original * conv) if canon_unit else None

    factor = None
    if canon_unit:
        region = facility.region if facility else tenant.default_region
        factor = resolve_factor(category, canon_unit, region, nrow.activity_date)

    emissions = None
    factor_snapshot = None
    factor_source = ""
    if factor and quantity_norm is not None:
        factor_snapshot = factor.value_kgco2e_per_unit
        factor_source = f"{factor.source} v{factor.version}"
        emissions = (quantity_norm * factor_snapshot).quantize(Decimal("0.0001"))

    activity = EmissionActivity.objects.create(
        tenant=tenant,
        raw_record=raw,
        source=source,
        ingestion_run=run,
        facility=facility,
        category=category,
        activity_date=nrow.activity_date,
        period_start=nrow.period_start,
        period_end=nrow.period_end,
        quantity_original=nrow.quantity_original,
        unit_original=nrow.unit_original,
        quantity_normalized=quantity_norm,
        unit_normalized=canon_unit or "",
        conversion_factor=conv,
        factor=factor,
        factor_value_snapshot=factor_snapshot,
        factor_source_snapshot=factor_source,
        emissions_kgco2e=emissions,
        notes=nrow.notes,
    )
    return activity


def _default_unit_for(scope, category):
    if "Electricity" in category: return "kWh"
    if "Combustion" in category: return "L"
    if "Travel" in category: return "km"
    if "Goods" in category: return "kg"
    return ""


def _run_validators(activity: EmissionActivity, nrow: NormalizedRow):
    flags = []
    if not activity.unit_normalized:
        flags.append(("UNIT_UNRESOLVED", "error", f"Unit {nrow.unit_original!r} did not match any known alias."))
        activity.status = "flagged"

    if activity.facility is None and nrow.facility_source_code:
        flags.append(("PLANT_CODE_UNMAPPED", "warn",
                      f"Source code {nrow.facility_source_code!r} is not mapped to a facility."))
        activity.status = "flagged"

    if activity.factor is None:
        flags.append(("MISSING_FACTOR", "error",
                      f"No emission factor for {activity.category} in {activity.unit_normalized or activity.unit_original}."))
        activity.status = "flagged"

    if activity.period_start and activity.period_end:
        if activity.period_start.month != activity.period_end.month:
            flags.append(("BILLING_PERIOD_MISALIGNED", "info",
                          f"Period {activity.period_start} to {activity.period_end} spans multiple calendar months."))

    # Duplicate detection: same source, same facility, same date, same quantity
    dup = EmissionActivity.objects.filter(
        tenant=activity.tenant, source=activity.source,
        facility=activity.facility, activity_date=activity.activity_date,
        quantity_original=activity.quantity_original,
    ).exclude(pk=activity.pk).exists()
    if dup:
        flags.append(("DUPLICATE_SUSPECTED", "warn", "Another row exists with the same source, facility, date and quantity."))
        if activity.status != "flagged":
            activity.status = "flagged"

    # Outlier check vs the prior 180 days for same (facility, category).
    if activity.facility and activity.quantity_normalized:
        window_end = activity.activity_date - timedelta(days=1)
        window_start = window_end - timedelta(days=180)
        prior = EmissionActivity.objects.filter(
            tenant=activity.tenant, facility=activity.facility, category=activity.category,
            activity_date__range=(window_start, window_end), status__in=["approved", "locked"],
        ).exclude(quantity_normalized__isnull=True)
        vals = [float(a.quantity_normalized) for a in prior]
        if len(vals) >= 3:
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            std = var ** 0.5
            if std > 0 and abs(float(activity.quantity_normalized) - mean) > 3 * std:
                flags.append(("OUTLIER_VS_PRIOR_PERIOD", "warn",
                              f"Quantity {activity.quantity_normalized} is >3σ from the recent average ({mean:.1f})."))
                if activity.status != "flagged":
                    activity.status = "flagged"

    activity.save()
    for code, sev, msg in flags:
        ValidationFlag.objects.create(
            tenant=activity.tenant, activity=activity,
            code=code, severity=sev, message=msg,
        )


def approve_activity(activity: EmissionActivity, actor: User, reason: str = "") -> EmissionActivity:
    if activity.status == "locked":
        raise ValueError("Locked rows cannot be re-approved.")
    if activity.flags.filter(severity="error", dismissed_at__isnull=True).exists():
        raise ValueError("Resolve or dismiss error-severity flags before approving.")
    before = {"status": activity.status}
    activity.status = "approved"
    activity.approved_at = timezone.now()
    activity.approved_by = actor
    activity.save()
    AuditLogEntry.objects.create(
        tenant=activity.tenant, actor=actor, entity_type="EmissionActivity",
        entity_id=activity.id, action="approved",
        before=before, after={"status": "approved"}, reason=reason,
    )
    return activity


def lock_activity(activity: EmissionActivity, actor: User, reason: str = "") -> EmissionActivity:
    if activity.status != "approved":
        raise ValueError("Only approved rows can be locked.")
    activity.status = "locked"
    activity.locked_at = timezone.now()
    activity.locked_by = actor
    activity.save()
    AuditLogEntry.objects.create(
        tenant=activity.tenant, actor=actor, entity_type="EmissionActivity",
        entity_id=activity.id, action="locked",
        before={"status": "approved"}, after={"status": "locked"}, reason=reason,
    )
    return activity


def dismiss_flag(flag: ValidationFlag, actor: User, reason: str) -> ValidationFlag:
    if not reason.strip():
        raise ValueError("A dismissal reason is required.")
    flag.dismissed_at = timezone.now()
    flag.dismissed_by = actor
    flag.dismissal_reason = reason
    flag.save()
    AuditLogEntry.objects.create(
        tenant=flag.tenant, actor=actor, entity_type="ValidationFlag",
        entity_id=flag.id, action="flag_dismissed",
        after={"code": flag.code, "reason": reason},
    )
    # If no remaining error flags, move row back from 'flagged' to 'pending'
    activity = flag.activity
    if activity.status == "flagged" and not activity.flags.filter(severity="error", dismissed_at__isnull=True).exists():
        activity.status = "pending"
        activity.save()
    return flag
