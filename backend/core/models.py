"""
Core data model. Mirrors MODEL.md. Read that document first if anything
here looks under-justified — the reasoning lives there, not in comments here.
"""
import uuid
from django.db import models


class TenantScopedModel(models.Model):
    tenant = models.ForeignKey("Tenant", on_delete=models.CASCADE, related_name="+")

    class Meta:
        abstract = True


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    default_region = models.CharField(max_length=8, default="GLOBAL")
    default_currency = models.CharField(max_length=3, default="USD")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class User(models.Model):
    """Single-role analyst. Hardcoded auth for the demo — see DECISIONS.md."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="users")
    email = models.EmailField()
    display_name = models.CharField(max_length=200)

    class Meta:
        unique_together = [("tenant", "email")]


class Facility(TenantScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=40, default="plant")
    region = models.CharField(max_length=8, default="GLOBAL")
    source_codes = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = [("tenant", "name")]


class EmissionCategory(models.Model):
    SCOPE_CHOICES = [(1, "Scope 1"), (2, "Scope 2"), (3, "Scope 3")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.IntegerField(choices=SCOPE_CHOICES)
    category = models.CharField(max_length=100)
    subcategory = models.CharField(max_length=100, blank=True)
    canonical_unit = models.CharField(max_length=20)
    ghg_protocol_code = models.CharField(max_length=20, blank=True)

    class Meta:
        unique_together = [("scope", "category", "subcategory")]

    def __str__(self):
        return f"S{self.scope} {self.category} / {self.subcategory}"


class EmissionFactor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(EmissionCategory, on_delete=models.PROTECT, related_name="factors")
    region = models.CharField(max_length=8, default="GLOBAL")
    unit = models.CharField(max_length=20, help_text="Unit the factor applies to, e.g. kWh, L, km")
    value_kgco2e_per_unit = models.DecimalField(max_digits=18, decimal_places=8)
    source = models.CharField(max_length=120, help_text="e.g. 'DEFRA 2023 v1.1'")
    version = models.CharField(max_length=20, default="1")
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["category", "region", "valid_from"])]


class Source(TenantScopedModel):
    KIND_CHOICES = [
        ("sap_flatfile", "SAP flat-file export"),
        ("utility_pdf", "Utility PDF bill"),
        ("travel_api", "Travel platform API export"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=40, choices=KIND_CHOICES)
    adapter_config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("tenant", "name")]


class IngestionRun(TenantScopedModel):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("succeeded", "Succeeded"),
        ("partial", "Partial — some rows failed"),
        ("failed", "Failed"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="runs")
    triggered_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    file_name = models.CharField(max_length=400, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    row_count_received = models.IntegerField(default=0)
    row_count_normalized = models.IntegerField(default=0)
    row_count_failed = models.IntegerField(default=0)
    error_log = models.JSONField(default=list, blank=True)


class RawRecord(TenantScopedModel):
    """Immutable. What arrived, exactly. Re-ingest writes new rows, never updates."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.CASCADE, related_name="raw_records")
    source_row_ref = models.CharField(max_length=200, help_text="Line number, record ID, etc — for analyst error messages")
    payload = models.JSONField()
    row_hash = models.CharField(max_length=64, db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)


class EmissionActivity(TenantScopedModel):
    STATUS_CHOICES = [
        ("pending", "Pending review"),
        ("flagged", "Flagged"),
        ("approved", "Approved"),
        ("locked", "Locked for audit"),
        ("superseded", "Superseded by re-ingestion"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Provenance
    raw_record = models.ForeignKey(RawRecord, on_delete=models.PROTECT, null=True, blank=True, related_name="activities")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="activities")
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="activities")
    supersedes = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by")
    adjusts = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="adjusted_by")

    # What it is
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, null=True, blank=True, related_name="activities")
    category = models.ForeignKey(EmissionCategory, on_delete=models.PROTECT, related_name="activities")

    # When
    activity_date = models.DateField()
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    # Quantity, original vs normalized — see MODEL.md §4
    quantity_original = models.DecimalField(max_digits=18, decimal_places=6)
    unit_original = models.CharField(max_length=40)
    quantity_normalized = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    unit_normalized = models.CharField(max_length=20, blank=True)
    conversion_factor = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)

    # Factor snapshot — see MODEL.md §5
    factor = models.ForeignKey(EmissionFactor, on_delete=models.SET_NULL, null=True, blank=True, related_name="activities")
    factor_value_snapshot = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    factor_source_snapshot = models.CharField(max_length=120, blank=True)
    emissions_kgco2e = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    # Workflow
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_activities")
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="locked_activities")

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "activity_date"]),
            models.Index(fields=["tenant", "source", "status"]),
        ]


class ValidationFlag(TenantScopedModel):
    """Soft signals raised by validators. Analysts dismiss or resolve."""
    SEVERITY = [("info", "Info"), ("warn", "Warning"), ("error", "Error")]
    CODE_CHOICES = [
        ("MISSING_FACTOR", "No emission factor matched"),
        ("UNIT_UNRESOLVED", "Unit not recognized"),
        ("OUTLIER_VS_PRIOR_PERIOD", "Quantity is far from the recent average"),
        ("DUPLICATE_SUSPECTED", "Looks like a duplicate of another row"),
        ("PLANT_CODE_UNMAPPED", "Source code did not match any facility"),
        ("BILLING_PERIOD_MISALIGNED", "Period spans multiple calendar months"),
        ("DUPLICATE_OF_LOCKED", "Re-ingested but original row is locked"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(EmissionActivity, on_delete=models.CASCADE, related_name="flags")
    code = models.CharField(max_length=40, choices=CODE_CHOICES)
    severity = models.CharField(max_length=10, choices=SEVERITY, default="warn")
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    dismissed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    dismissal_reason = models.TextField(blank=True)


class AuditLogEntry(TenantScopedModel):
    """Append-only at the application layer. See MODEL.md §8."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    entity_type = models.CharField(max_length=60)
    entity_id = models.UUIDField()
    action = models.CharField(max_length=40)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    ts = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "entity_type", "entity_id"]),
            models.Index(fields=["tenant", "ts"]),
        ]
