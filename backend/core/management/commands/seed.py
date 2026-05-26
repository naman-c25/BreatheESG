"""
Seeds a demo tenant with: facilities, the GHG category taxonomy we use,
a small DEFRA-2023-style emission factor table, two source configs,
and one analyst user.

Idempotent. Safe to re-run.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from core.models import (
    Tenant, User, Facility, EmissionCategory, EmissionFactor, Source,
)


CATEGORIES = [
    # (scope, category, subcategory, canonical_unit)
    (1, "Stationary Combustion", "Diesel", "L"),
    (1, "Stationary Combustion", "Natural Gas", "kWh"),
    (1, "Mobile Combustion", "Petrol", "L"),
    (2, "Purchased Electricity", "Grid Mix", "kWh"),
    (3, "Business Travel", "Air – Short-haul", "km"),
    (3, "Business Travel", "Air – Long-haul", "km"),
    (3, "Business Travel", "Hotel", "nights"),
    (3, "Business Travel", "Ground – Car", "km"),
    (3, "Purchased Goods and Services", "Procurement", "kg"),
]

# DEFRA 2023 / EPA eGRID 2022 approximate factors. Real-world values; the
# point is shape, not 6-decimal precision.
FACTORS = [
    # (scope, cat, subcat, region, unit, value, source)
    (1, "Stationary Combustion", "Diesel", "GLOBAL", "L", "2.68779", "DEFRA 2023"),
    (1, "Stationary Combustion", "Natural Gas", "GLOBAL", "kWh", "0.18293", "DEFRA 2023"),
    (1, "Mobile Combustion", "Petrol", "GLOBAL", "L", "2.31495", "DEFRA 2023"),
    (2, "Purchased Electricity", "Grid Mix", "US", "kWh", "0.38554", "EPA eGRID 2022"),
    (2, "Purchased Electricity", "Grid Mix", "DE", "kWh", "0.43800", "Umweltbundesamt 2023"),
    (2, "Purchased Electricity", "Grid Mix", "GLOBAL", "kWh", "0.47500", "IEA 2022"),
    (3, "Business Travel", "Air – Short-haul", "GLOBAL", "km", "0.15102", "DEFRA 2023"),
    (3, "Business Travel", "Air – Long-haul", "GLOBAL", "km", "0.14787", "DEFRA 2023"),
    (3, "Business Travel", "Hotel", "GLOBAL", "nights", "10.40000", "Cornell Hotel Index 2022"),
    (3, "Business Travel", "Ground – Car", "GLOBAL", "km", "0.17012", "DEFRA 2023"),
]


class Command(BaseCommand):
    help = "Seed demo data."

    def handle(self, *args, **opts):
        tenant, _ = Tenant.objects.get_or_create(
            name="Acme Manufacturing Inc",
            defaults={"default_region": "US", "default_currency": "USD"},
        )
        self.stdout.write(self.style.SUCCESS(f"Tenant: {tenant.id} {tenant.name}"))

        User.objects.get_or_create(
            tenant=tenant, email="analyst@acme.example",
            defaults={"display_name": "Sam Analyst"},
        )

        # Optional: a second tenant proves multi-tenancy works
        Tenant.objects.get_or_create(
            name="Globex DE GmbH",
            defaults={"default_region": "DE", "default_currency": "EUR"},
        )

        for scope, cat, sub, unit in CATEGORIES:
            EmissionCategory.objects.get_or_create(
                scope=scope, category=cat, subcategory=sub,
                defaults={"canonical_unit": unit},
            )

        for scope, cat, sub, region, unit, val, source in FACTORS:
            c = EmissionCategory.objects.get(scope=scope, category=cat, subcategory=sub)
            EmissionFactor.objects.get_or_create(
                category=c, region=region, unit=unit, source=source,
                defaults={
                    "value_kgco2e_per_unit": Decimal(val),
                    "valid_from": date(2023, 1, 1),
                    "version": "1",
                },
            )

        Facility.objects.get_or_create(
            tenant=tenant, name="Newark Plant 01",
            defaults={
                "kind": "plant", "region": "US",
                "source_codes": {"sap_plant": "US01", "utility_meter": "MTR-NWK-0042"},
            },
        )
        Facility.objects.get_or_create(
            tenant=tenant, name="Frankfurt Plant 02",
            defaults={
                "kind": "plant", "region": "DE",
                "source_codes": {"sap_plant": "DE01"},
            },
        )

        Source.objects.get_or_create(
            tenant=tenant, name="SAP ECC — Fuel & Procurement",
            defaults={"kind": "sap_flatfile", "adapter_config": {"delimiter": "|"}},
        )
        Source.objects.get_or_create(
            tenant=tenant, name="ConEd — HQ Electricity",
            defaults={"kind": "utility_pdf", "adapter_config": {}},
        )
        Source.objects.get_or_create(
            tenant=tenant, name="Concur — Business Travel",
            defaults={"kind": "travel_api", "adapter_config": {}},
        )
        self.stdout.write(self.style.SUCCESS("Seed complete."))
