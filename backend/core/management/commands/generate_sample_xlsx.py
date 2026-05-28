"""
Generate a second SAP sample, this time as Excel — demonstrates the .xlsx
upload path. Real SAP users often export to Excel rather than CSV because
SE16N's 'Local file → Spreadsheet' option is one click.
"""
from pathlib import Path
from datetime import date
from django.core.management.base import BaseCommand


ROWS = [
    # date (DE), plant, material, desc, qty, unit, mvt, doc#, reverses
    ("02.05.2025", "US01", "FUEL-DSL-001", "Dieselkraftstoff", 1180.5, "LTR", "261", "4900002001", ""),
    ("08.05.2025", "US01", "FUEL-DSL-001", "Dieselkraftstoff", 1325.0, "LTR", "261", "4900002002", ""),
    ("12.05.2025", "DE01", "FUEL-NG-100", "Erdgas Industriebezug", 22000.0, "M3", "261", "4900002003", ""),
]


class Command(BaseCommand):
    help = "Generate sample_data/sap_fuel_2025_05.xlsx"

    def handle(self, *args, **opts):
        import openpyxl
        out = Path(__file__).resolve().parents[3] / "sample_data" / "sap_fuel_2025_05.xlsx"
        out.parent.mkdir(exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "MSEG"
        ws.append([
            "Buchungsdatum", "Werk", "Material", "Materialkurztext",
            "Menge", "Basismengeneinheit", "Bewegungsart",
            "Belegnummer", "Storno-Belegnummer",
        ])
        for r in ROWS:
            ws.append(list(r))
        wb.save(str(out))
        self.stdout.write(self.style.SUCCESS(f"Wrote {out}"))
