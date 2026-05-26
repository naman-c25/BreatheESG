from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("sources", views.SourceViewSet, basename="source")
router.register("facilities", views.FacilityViewSet, basename="facility")
router.register("runs", views.IngestionRunViewSet, basename="run")
router.register("activities", views.ActivityViewSet, basename="activity")

urlpatterns = [
    path("tenants/", views.TenantListView.as_view()),
    path("categories/", views.CategoryListView.as_view()),
    path("ingest/", views.IngestView.as_view()),
    path("flags/<uuid:pk>/dismiss/", views.FlagDismissView.as_view()),
    path("audit/", views.AuditLogView.as_view()),
    path("dashboard/summary/", views.DashboardSummaryView.as_view()),
] + router.urls
