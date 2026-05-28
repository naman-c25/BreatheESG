from rest_framework import viewsets, status, decorators, response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from .models import (
    Tenant, Source, IngestionRun, EmissionActivity, EmissionCategory,
    ValidationFlag, AuditLogEntry, Facility,
)
from .serializers import (
    TenantSerializer, SourceSerializer, IngestionRunSerializer,
    ActivitySerializer, CategorySerializer, AuditLogSerializer,
    FacilitySerializer, FlagSerializer,
)
from . import services


def _require_tenant(request):
    if not request.tenant:
        return response.Response({"detail": "X-Tenant-Id header required"}, status=400)
    return None


class TenantListView(APIView):
    """Unscoped — for the demo's tenant switcher."""
    def get(self, request):
        return response.Response(TenantSerializer(Tenant.objects.all(), many=True).data)


class TenantScopedViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return self.queryset_class.objects.filter(tenant=self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)


class SourceViewSet(TenantScopedViewSet):
    serializer_class = SourceSerializer
    queryset_class = Source


class FacilityViewSet(TenantScopedViewSet):
    serializer_class = FacilitySerializer
    queryset_class = Facility


class IngestionRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = IngestionRunSerializer

    def get_queryset(self):
        qs = IngestionRun.objects.filter(tenant=self.request.tenant).order_by("-started_at")
        source = self.request.query_params.get("source")
        if source:
            qs = qs.filter(source_id=source)
        return qs


class IngestView(APIView):
    """
    Teen ingestion modes, ek endpoint:
      1. mode=upload (default) — multipart file.
      2. mode=paste            — content body + file_name (mostly travel JSON).
      3. mode=pull             — configured fixture se padhta hai (demo ke liye —
                                 prod mein real Concur/SAP API call yahan aayega).

    Teeno run_ingestion(...) pe converge karte hain. Adapter ko pata bhi nahi
    chalta ki bytes kahan se aaye — yahi point hai: mechanism interchange detail
    hai, data model ka part nahi.
    """
    def post(self, request):
        if err := _require_tenant(request): return err
        source_id = request.data.get("source")
        if not source_id:
            return response.Response({"detail": "source is required"}, status=400)
        source = get_object_or_404(Source, pk=source_id, tenant=request.tenant)
        mode = request.data.get("mode", "upload")

        if mode == "upload":
            file = request.FILES.get("file")
            if not file:
                return response.Response({"detail": "file is required for mode=upload"}, status=400)
            file_bytes, file_name = file.read(), file.name

        elif mode == "paste":
            content = request.data.get("content", "")
            if not content:
                return response.Response({"detail": "content is required for mode=paste"}, status=400)
            file_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            file_name = request.data.get("file_name", "pasted-content")

        elif mode == "pull":
            from pathlib import Path
            fixture = (source.adapter_config or {}).get("pull_fixture")
            if not fixture:
                return response.Response(
                    {"detail": "This source is not configured for API pull. "
                               "Set adapter_config.pull_fixture to a sample file path. "
                               "In production, this endpoint would call the source's API directly."},
                    status=400,
                )
            path = Path(__file__).resolve().parent.parent / "sample_data" / fixture
            if not path.exists():
                return response.Response({"detail": f"Pull fixture {fixture!r} not found"}, status=400)
            file_bytes = path.read_bytes()
            file_name = f"pull/{fixture}"

        else:
            return response.Response({"detail": f"Unknown mode: {mode!r}"}, status=400)

        run = services.run_ingestion(
            tenant=request.tenant, source=source,
            file_bytes=file_bytes, file_name=file_name,
            actor=request.user_obj,
        )
        return response.Response(IngestionRunSerializer(run).data, status=201)


class ActivityViewSet(viewsets.ModelViewSet):
    serializer_class = ActivitySerializer

    def get_queryset(self):
        qs = EmissionActivity.objects.filter(tenant=self.request.tenant).select_related(
            "category", "source", "facility", "raw_record",
        ).prefetch_related("flags").order_by("-activity_date", "-created_at")
        p = self.request.query_params
        if status_f := p.get("status"):
            qs = qs.filter(status__in=status_f.split(","))
        if source := p.get("source"):
            qs = qs.filter(source_id=source)
        if scope := p.get("scope"):
            qs = qs.filter(category__scope=scope)
        if facility := p.get("facility"):
            qs = qs.filter(facility_id=facility)
        if run := p.get("run"):
            qs = qs.filter(ingestion_run_id=run)
        if q := p.get("q"):
            qs = qs.filter(notes__icontains=q)
        return qs

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status == "locked":
            return response.Response({"detail": "Row is locked for audit."}, status=409)
        before = ActivitySerializer(instance).data
        resp = super().update(request, *args, **kwargs)
        AuditLogEntry.objects.create(
            tenant=request.tenant, actor=request.user_obj,
            entity_type="EmissionActivity", entity_id=instance.id,
            action="updated", before={k: before[k] for k in request.data.keys() if k in before},
            after={k: v for k, v in request.data.items()},
        )
        return resp

    @decorators.action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        a = self.get_object()
        try:
            services.approve_activity(a, request.user_obj, request.data.get("reason", ""))
        except ValueError as e:
            return response.Response({"detail": str(e)}, status=400)
        return response.Response(ActivitySerializer(a).data)

    @decorators.action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        a = self.get_object()
        try:
            services.lock_activity(a, request.user_obj, request.data.get("reason", ""))
        except ValueError as e:
            return response.Response({"detail": str(e)}, status=400)
        return response.Response(ActivitySerializer(a).data)

    @decorators.action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        a = self.get_object()
        try:
            services.reject_activity(a, request.user_obj, request.data.get("reason", ""))
        except ValueError as e:
            return response.Response({"detail": str(e)}, status=400)
        return response.Response(ActivitySerializer(a).data)

    @decorators.action(detail=False, methods=["post"])
    def bulk_approve(self, request):
        ids = request.data.get("ids", [])
        approved, errors = [], []
        for aid in ids:
            try:
                a = EmissionActivity.objects.get(pk=aid, tenant=request.tenant)
                services.approve_activity(a, request.user_obj)
                approved.append(aid)
            except (EmissionActivity.DoesNotExist, ValueError) as e:
                errors.append({"id": aid, "error": str(e)})
        return response.Response({"approved": approved, "errors": errors})


class FlagDismissView(APIView):
    def post(self, request, pk):
        if err := _require_tenant(request): return err
        flag = get_object_or_404(ValidationFlag, pk=pk, tenant=request.tenant)
        try:
            services.dismiss_flag(flag, request.user_obj, request.data.get("reason", ""))
        except ValueError as e:
            return response.Response({"detail": str(e)}, status=400)
        return response.Response(FlagSerializer(flag).data)


class AuditLogView(APIView):
    def get(self, request):
        if err := _require_tenant(request): return err
        qs = AuditLogEntry.objects.filter(tenant=request.tenant).order_by("-ts")
        if entity_id := request.query_params.get("entity_id"):
            qs = qs.filter(entity_id=entity_id)
        return response.Response(AuditLogSerializer(qs[:200], many=True).data)


class DashboardSummaryView(APIView):
    def get(self, request):
        if err := _require_tenant(request): return err
        from django.db.models import Count, Sum
        qs = EmissionActivity.objects.filter(tenant=request.tenant)
        by_status = {row["status"]: row["n"] for row in qs.values("status").annotate(n=Count("id"))}
        by_scope = {
            f"scope_{row['category__scope']}": float(row["total"] or 0)
            for row in qs.filter(status__in=["approved", "locked"])
                         .values("category__scope")
                         .annotate(total=Sum("emissions_kgco2e"))
        }
        return response.Response({
            "counts_by_status": by_status,
            "kgco2e_by_scope": by_scope,
        })


class CategoryListView(APIView):
    def get(self, request):
        return response.Response(CategorySerializer(EmissionCategory.objects.all(), many=True).data)
