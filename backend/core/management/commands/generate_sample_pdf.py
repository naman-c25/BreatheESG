"""
Generates a sample utility bill PDF that matches the layout
UtilityPDFAdapter knows how to parse. Real bills are far messier — see
SOURCES.md — but this one is enough to demonstrate the ingestion path.
"""
from pathlib import Path
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate sample_data/utility_bill_2025_04.pdf"

    def handle(self, *args, **opts):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        out = Path(__file__).resolve().parents[3] / "sample_data" / "utility_bill_2025_04.pdf"
        out.parent.mkdir(exist_ok=True)
        c = canvas.Canvas(str(out), pagesize=letter)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 720, "Consolidated Edison Company of New York, Inc.")
        c.setFont("Helvetica", 11)
        c.drawString(72, 700, "Commercial Account Statement")
        c.drawString(72, 670, "Account Number: 12-3456-7890-0001")
        c.drawString(72, 655, "Customer: Acme Manufacturing Inc — Newark Plant 01")
        c.drawString(72, 640, "Meter ID: MTR-NWK-0042")
        c.drawString(72, 615, "BILLING SUMMARY")
        c.drawString(72, 600, "Service Period: 04/01/2025 - 04/30/2025")
        c.drawString(72, 585, "Rate Class: SC-9 General Large")
        c.drawString(72, 560, "ENERGY USAGE")
        c.drawString(72, 545, "Total Energy Usage: 142,318 kWh")
        c.drawString(72, 530, "Demand Charge: 412 kW")
        c.drawString(72, 505, "AMOUNT DUE")
        c.drawString(72, 490, "Amount Due: $18,442.16")
        c.drawString(72, 460, "Questions? Call 1-800-XXX-XXXX")
        c.showPage()
        c.save()
        self.stdout.write(self.style.SUCCESS(f"Wrote {out}"))
