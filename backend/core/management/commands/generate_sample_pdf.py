"""
Generates a sample utility bill PDF that matches the layout
UtilityPDFAdapter knows how to parse. Real bills are far messier — see
SOURCES.md — but this one exercises:

  - Two separate meters on the same bill (sub-metering, common on
    commercial accounts: one for HVAC, one for lighting/general).
  - Peak / Off-Peak / Shoulder tariff line items on the first meter.
  - A demand charge in kW.
  - A third meter reported in kVAh (apparent power), which the adapter
    must reject — kVAh is not a valid emissions quantity.
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
        c.drawString(72, 750, "Consolidated Edison Company of New York, Inc.")
        c.setFont("Helvetica", 11)
        c.drawString(72, 730, "Commercial Account Statement")
        c.drawString(72, 715, "Account Number: 12-3456-7890-0001")
        c.drawString(72, 700, "Customer: Acme Manufacturing Inc — Newark Plant 01")

        c.drawString(72, 675, "BILLING SUMMARY")
        c.drawString(72, 660, "Service Period: 04/01/2025 - 04/30/2025")
        c.drawString(72, 645, "Rate Class: SC-9 General Large")

        # Meter 1: real meter with peak / off-peak / shoulder tariff breakdown
        c.setFont("Helvetica-Bold", 11)
        c.drawString(72, 615, "Meter ID: MTR-NWK-0042")
        c.setFont("Helvetica", 11)
        c.drawString(72, 600, "Service: General Power")
        c.drawString(72, 585, "Peak Usage: 58,420 kWh")
        c.drawString(72, 570, "Off-Peak Usage: 64,308 kWh")
        c.drawString(72, 555, "Shoulder Usage: 19,590 kWh")
        c.drawString(72, 540, "Total Energy Usage: 142,318 kWh")
        c.drawString(72, 525, "Demand Charge: 412 kW")

        # Meter 2: a second sub-meter (HVAC), simpler line
        c.setFont("Helvetica-Bold", 11)
        c.drawString(72, 495, "Meter ID: MTR-NWK-HVAC-01")
        c.setFont("Helvetica", 11)
        c.drawString(72, 480, "Service: HVAC Sub-meter")
        c.drawString(72, 465, "Total Energy Usage: 38,710 kWh")

        # Meter 3: erroneously reports kVAh — must be rejected, not silently
        # multiplied through. A reviewer will look for whether this becomes a
        # row (wrong) or surfaces as an error (right).
        c.setFont("Helvetica-Bold", 11)
        c.drawString(72, 435, "Meter ID: MTR-NWK-REAC-01")
        c.setFont("Helvetica", 11)
        c.drawString(72, 420, "Service: Reactive Power (informational)")
        c.drawString(72, 405, "Total Energy Usage: 12,400 kVAh")

        c.drawString(72, 370, "AMOUNT DUE")
        c.drawString(72, 355, "Amount Due: $24,891.43")
        c.drawString(72, 335, "Questions? Call 1-800-XXX-XXXX")

        c.showPage()
        c.save()
        self.stdout.write(self.style.SUCCESS(f"Wrote {out}"))
