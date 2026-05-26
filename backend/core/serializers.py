from rest_framework import serializers
from .models import (
    Tenant, Source, IngestionRun, EmissionActivity, EmissionCategory,
    ValidationFlag, AuditLogEntry, Facility, RawRecord, User,
)


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "default_region", "default_currency"]


class SourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Source
        fields = ["id", "name", "kind", "adapter_config", "created_at"]


class FacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = ["id", "name", "kind", "region", "source_codes"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = EmissionCategory
        fields = ["id", "scope", "category", "subcategory", "canonical_unit"]


class IngestionRunSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source="source.name", read_only=True)

    class Meta:
        model = IngestionRun
        fields = [
            "id", "source", "source_name", "file_name", "status",
            "started_at", "finished_at",
            "row_count_received", "row_count_normalized", "row_count_failed",
            "error_log",
        ]


class FlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValidationFlag
        fields = ["id", "code", "severity", "message", "created_at",
                  "dismissed_at", "dismissal_reason"]


class RawRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = RawRecord
        fields = ["id", "source_row_ref", "payload", "received_at"]


class ActivitySerializer(serializers.ModelSerializer):
    flags = FlagSerializer(many=True, read_only=True)
    source_name = serializers.CharField(source="source.name", read_only=True)
    source_kind = serializers.CharField(source="source.kind", read_only=True)
    facility_name = serializers.CharField(source="facility.name", read_only=True, default=None)
    category_label = serializers.SerializerMethodField()
    scope = serializers.IntegerField(source="category.scope", read_only=True)
    raw_record = RawRecordSerializer(read_only=True)

    class Meta:
        model = EmissionActivity
        fields = [
            "id", "status", "scope",
            "source", "source_name", "source_kind",
            "facility", "facility_name",
            "category", "category_label",
            "activity_date", "period_start", "period_end",
            "quantity_original", "unit_original",
            "quantity_normalized", "unit_normalized", "conversion_factor",
            "factor_value_snapshot", "factor_source_snapshot",
            "emissions_kgco2e",
            "notes", "created_at", "updated_at",
            "approved_at", "locked_at",
            "flags", "raw_record",
        ]
        read_only_fields = [
            "status", "scope", "source_name", "source_kind", "facility_name",
            "category_label", "quantity_normalized", "unit_normalized",
            "conversion_factor", "factor_value_snapshot", "factor_source_snapshot",
            "emissions_kgco2e", "created_at", "updated_at",
            "approved_at", "locked_at", "flags", "raw_record",
        ]

    def get_category_label(self, obj):
        return f"S{obj.category.scope} · {obj.category.category} · {obj.category.subcategory}".strip(" ·")


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = AuditLogEntry
        fields = ["id", "actor_email", "entity_type", "entity_id", "action",
                  "before", "after", "reason", "ts"]
