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


# GHG Protocol categories. Scope 1 = direct (apni fuel/vehicles),
# Scope 2 = purchased electricity, Scope 3 = value chain (travel, procurement).
# Hardcoded list nahi rakha — categories table mein dalta hun kyunki
# auditor ko taxonomy versioning bhi chahiye hoti hai.
CATEGORIES = [
    # (scope, category, subcategory, canonical_unit)
    (1, "Stationary Combustion", "Diesel", "L"),
    (1, "Stationary Combustion", "Natural Gas", "kWh"),
    (1, "Mobile Combustion", "Petrol", "L"),
    (2, "Purchased Electricity", "Grid Mix", "kWh"),
    # Air travel — har (haul, cabin) combination ka apna factor.
    # DEFRA published values, multipliers nahi (warna snapshot ka concept toot jaata).
    # Business class economy se ~2.9x emit karti hai per km — ignore karna matlab
    # premium-cabin emissions silently 3x underreport.
    (3, "Business Travel", "Air – Short-haul Economy", "km"),
    (3, "Business Travel", "Air – Short-haul Premium Economy", "km"),
    (3, "Business Travel", "Air – Short-haul Business", "km"),
    (3, "Business Travel", "Air – Long-haul Economy", "km"),
    (3, "Business Travel", "Air – Long-haul Premium Economy", "km"),
    (3, "Business Travel", "Air – Long-haul Business", "km"),
    (3, "Business Travel", "Air – Long-haul First", "km"),
    (3, "Business Travel", "Hotel", "nights"),
    (3, "Business Travel", "Ground – Car", "km"),
    (3, "Business Travel", "Ground – Rail", "km"),
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
    # Air — DEFRA 2023 with cabin-class multipliers. Numbers below are DEFRA's
    # actual published values, not derived multipliers.
    (3, "Business Travel", "Air – Short-haul Economy", "GLOBAL", "km", "0.15102", "DEFRA 2023"),
    (3, "Business Travel", "Air – Short-haul Premium Economy", "GLOBAL", "km", "0.22653", "DEFRA 2023"),
    (3, "Business Travel", "Air – Short-haul Business", "GLOBAL", "km", "0.22653", "DEFRA 2023"),
    (3, "Business Travel", "Air – Long-haul Economy", "GLOBAL", "km", "0.14787", "DEFRA 2023"),
    (3, "Business Travel", "Air – Long-haul Premium Economy", "GLOBAL", "km", "0.23659", "DEFRA 2023"),
    (3, "Business Travel", "Air – Long-haul Business", "GLOBAL", "km", "0.42884", "DEFRA 2023"),
    (3, "Business Travel", "Air – Long-haul First", "GLOBAL", "km", "0.59151", "DEFRA 2023"),
    # Hotels — country-specific factors. Cornell HSB 2023.
    # GB hotel 10 kg/night, SG 66 kg/night — 6x variation!
    # Trip mein agar 3 alag countries ke hotels hain to 3 alag factors lagne chahiye.
    (3, "Business Travel", "Hotel", "US", "nights", "30.05000", "Cornell HSB 2023"),
    (3, "Business Travel", "Hotel", "GB", "nights", "10.40000", "Cornell HSB 2023"),
    (3, "Business Travel", "Hotel", "DE", "nights", "20.32000", "Cornell HSB 2023"),
    (3, "Business Travel", "Hotel", "IN", "nights", "40.50000", "Cornell HSB 2023"),
    (3, "Business Travel", "Hotel", "SG", "nights", "65.83000", "Cornell HSB 2023"),
    (3, "Business Travel", "Hotel", "GLOBAL", "nights", "20.10000", "Cornell HSB 2023"),
    # Ground
    (3, "Business Travel", "Ground – Car", "GLOBAL", "km", "0.17012", "DEFRA 2023"),
    (3, "Business Travel", "Ground – Rail", "GLOBAL", "km", "0.03548", "DEFRA 2023"),
]


class Command(BaseCommand):
    help = "Seed demo data."

    def handle(self, *args, **opts):
        tenant, _ = Tenant.objects.get_or_create(
            name="Acme Manufacturing Inc",
            defaults={"default_region": "US", "default_currency": "USD"},
        )
        self.stdout.write(self.style.SUCCESS(f"Tenant: {tenant.id} {tenant.name}"))

        u, _ = User.objects.get_or_create(
            tenant=tenant, email="analyst@acme.example",
            defaults={"display_name": "Sam Analyst"},
        )
        if not u.password_hash:
            u.set_password("demo1234")
            u.save()

        # A second tenant proves multi-tenancy. Login as analyst@globex.example / demo1234.
        globex, _ = Tenant.objects.get_or_create(
            name="Globex DE GmbH",
            defaults={"default_region": "DE", "default_currency": "EUR"},
        )
        gu, _ = User.objects.get_or_create(
            tenant=globex, email="analyst@globex.example",
            defaults={"display_name": "Lena Analyst"},
        )
        if not gu.password_hash:
            gu.set_password("demo1234")
            gu.save()

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
            defaults={
                "kind": "travel_api",
                # pull_fixture wires the demo's "Pull from API" button to a
                # bundled sample file. In production this is replaced by a
                # real Concur OAuth client (out of scope per SOURCES.md).
                "adapter_config": {"pull_fixture": "travel_concur_2025_04.json"},
            },
        )
        self.stdout.write(self.style.SUCCESS("Seed complete."))
