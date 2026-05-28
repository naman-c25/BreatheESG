"""
Demo data loader. Fresh deploy pe empty dashboard nahi dikhna chahiye reviewer ko.
Real ingestion pipeline use karta hai (alag fake path nahi banaya).
Idempotent — agar activities pehle se hain to skip.

Bonus: Jan-Mar 2025 ke diesel rows synthesize karta hai (already approved)
taaki April ke 45,000 L wala outlier row actually OUTLIER_VS_PRIOR_PERIOD
trip kare. Bina history ke outlier check silent no-op rehta — sahi behavior,
but demo mein woh code path exercise nahi hota. Threshold loosen karne se
better tha realistic baseline synthesize kar do.
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import (
    Tenant, Source, EmissionActivity, EmissionCategory, EmissionFactor,
    Facility, User,
)
from core.services import run_ingestion


FILES = {
    "sap_flatfile": "sap_fuel_2025_04.csv",
    "utility_pdf": "utility_bill_2025_04.pdf",
    "travel_api": "travel_concur_2025_04.json",
}


def _synth_diesel_history(tenant, source, facility, user):
    """
    Create ~1200 L of monthly diesel consumption for Jan, Feb, Mar 2025,
    already approved. Lets the April outlier check fire on the 45,000-L row.
    """
    category = EmissionCategory.objects.get(
        scope=1, category="Stationary Combustion", subcategory="Diesel"
    )
    factor = EmissionFactor.objects.filter(
        category=category, unit="L", region="GLOBAL"
    ).first()
    quantities = [Decimal("1180"), Decimal("1245"), Decimal("1310")]
    for month, qty in zip([1, 2, 3], quantities):
        for day in (10, 22):  # two postings per month, like the April file
            EmissionActivity.objects.create(
                tenant=tenant, source=source, facility=facility, category=category,
                activity_date=date(2025, month, day),
                quantity_original=qty / 2, unit_original="LTR",
                quantity_normalized=qty / 2, unit_normalized="L",
                conversion_factor=Decimal("1"),
                factor=factor,
                factor_value_snapshot=factor.value_kgco2e_per_unit,
                factor_source_snapshot=f"{factor.source} v{factor.version}",
                emissions_kgco2e=(qty / 2) * factor.value_kgco2e_per_unit,
                status="approved",
                approved_at=timezone.now(),
                approved_by=user,
                notes="Synthesized prior-period history (demo)",
            )


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

        # 1. Synthesize prior-period history so outlier detection has a baseline.
        sap_source = Source.objects.filter(tenant=tenant, kind="sap_flatfile").first()
        newark = Facility.objects.filter(tenant=tenant, name="Newark Plant 01").first()
        user = User.objects.filter(tenant=tenant).first()
        if sap_source and newark and user:
            _synth_diesel_history(tenant, sap_source, newark, user)
            self.stdout.write("  Synthesized Jan-Mar 2025 diesel history")

        # 2. Ingest the April 2025 sample files through the real pipeline.
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
