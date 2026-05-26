"""
Runs the three sample files through the real ingestion pipeline so a fresh
deploy isn't empty. Idempotent — skips if the tenant already has activities.
"""
from pathlib import Path
from django.core.management.base import BaseCommand
from core.models import Tenant, Source, EmissionActivity
from core.services import run_ingestion


FILES = {
    "sap_flatfile": "sap_fuel_2025_04.csv",
    "utility_pdf": "utility_bill_2025_04.pdf",
    "travel_api": "travel_concur_2025_04.json",
}


class Command(BaseCommand):
    help = "Ingest the sample data files for the Acme tenant."

    def handle(self, *args, **opts):
        tenant = Tenant.objects.filter(name="Acme Manufacturing Inc").first()
        if not tenant:
            self.stdout.write(self.style.ERROR("Run `seed` first."))
            return
        if EmissionActivity.objects.filter(tenant=tenant).exists():
            self.stdout.write("Activities already present — skipping demo ingestion.")
            return

        sample_dir = Path(__file__).resolve().parents[3] / "sample_data"
        for source in Source.objects.filter(tenant=tenant):
            fname = FILES.get(source.kind)
            if not fname:
                continue
            path = sample_dir / fname
            if not path.exists():
                self.stdout.write(self.style.WARNING(f"Missing sample file: {path}"))
                continue
            run = run_ingestion(tenant, source, path.read_bytes(), fname, None)
            self.stdout.write(
                f"  {source.name}: status={run.status} ok={run.row_count_normalized} fail={run.row_count_failed}"
            )
