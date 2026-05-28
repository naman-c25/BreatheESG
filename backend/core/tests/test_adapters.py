"""
Adapter tests. The adapters are the boundary between messy external data
and our clean schema — they are where the prototype most needs guardrails.

Tests cover:
  - SAP happy path (German headers, decimal commas, dates)
  - SAP rejects 'STK' pieces unit with a clear error
  - SAP rejects unknown movement type rather than guessing
  - Travel emits per-segment rows and skips unknown airports correctly
  - Unit canonicalization (the bit most likely to silently corrupt numbers)
"""
from decimal import Decimal
from django.test import SimpleTestCase

import io

from core.adapters.sap import SAPFlatFileAdapter, _parse_date, _decode
from core.adapters.travel import TravelAPIAdapter
from core.adapters.utility import UtilityPDFAdapter
from core.services import canonicalize_unit


def _make_pdf(text_lines: list[str]) -> bytes:
    """Render the given lines into a single-page PDF and return the bytes."""
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 11)
    y = 720
    for line in text_lines:
        c.drawString(72, y, line)
        y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


SAP_HAPPY = b"""Buchungsdatum|Werk|Material|Materialkurztext|Menge|Basismengeneinheit|Bewegungsart
03.04.2025|US01|FUEL-DSL-001|Dieselkraftstoff|1.247,500|LTR|261
"""

SAP_PIECES = b"""Buchungsdatum|Werk|Material|Materialkurztext|Menge|Basismengeneinheit|Bewegungsart
03.04.2025|US01|FUEL-DSL-001|x|12,000|STK|261
"""

SAP_UNKNOWN_MVT = b"""Buchungsdatum|Werk|Material|Materialkurztext|Menge|Basismengeneinheit|Bewegungsart
03.04.2025|US01|UNKNOWN-XXX|x|10,000|LTR|999
"""

# Mixed date formats: line 2 uses DD.MM.YYYY, line 3 uses YYYYMMDD, line 4 uses ISO
SAP_MIXED_DATES = b"""Buchungsdatum|Werk|Material|Materialkurztext|Menge|Basismengeneinheit|Bewegungsart
03.04.2025|US01|FUEL-DSL-001|x|100,000|LTR|261
20250404|US01|FUEL-DSL-001|x|100,000|LTR|261
2025-04-05|US01|FUEL-DSL-001|x|100,000|LTR|261
"""

# Reversal: line 2 issues 500L, line 3 reverses it via 262. Expected output: zero rows.
SAP_REVERSAL = b"""Buchungsdatum|Werk|Material|Materialkurztext|Menge|Basismengeneinheit|Bewegungsart|Belegnummer|Storno-Belegnummer
03.04.2025|US01|FUEL-DSL-001|x|500,000|LTR|261|DOC100|
05.04.2025|US01|FUEL-DSL-001|reversal|500,000|LTR|262|DOC101|DOC100
"""

# Reversal pointing at a doc not in the file — should be reported, not silently dropped
SAP_REVERSAL_UNMATCHED = b"""Buchungsdatum|Werk|Material|Materialkurztext|Menge|Basignumengeneinheit|Bewegungsart|Belegnummer|Storno-Belegnummer
03.04.2025|US01|FUEL-DSL-001|reversal|500,000|LTR|262|DOC101|UNKNOWN-DOC
""".replace(b"Basignumengeneinheit", b"Basismengeneinheit")

# A latin-1-encoded CSV with the German "ü" in the description
SAP_LATIN1 = "Buchungsdatum|Werk|Material|Materialkurztext|Menge|Basismengeneinheit|Bewegungsart\n03.04.2025|US01|FUEL-DSL-001|Düsseldorf-Lieferung|100,000|LTR|261\n".encode("latin-1")


class SAPAdapterTests(SimpleTestCase):
    def test_happy_path_parses_german_headers_and_decimal_commas(self):
        r = SAPFlatFileAdapter().parse(SAP_HAPPY, {})
        self.assertEqual(r.errors, [])
        self.assertEqual(len(r.rows), 1)
        row = r.rows[0]
        # 1.247,500 (DE) must become 1247.5 — getting this wrong silently 1000x's the value
        self.assertEqual(row.quantity_original, Decimal("1247.500"))
        self.assertEqual(row.unit_original, "LTR")
        self.assertEqual(row.facility_source_code, "US01")
        self.assertEqual(row.category_hint, (1, "Stationary Combustion", "Diesel"))
        self.assertEqual(row.activity_date.isoformat(), "2025-04-03")

    def test_pieces_unit_is_rejected_loudly(self):
        # STK means 'pieces' in SAP. A count is not an emission quantity.
        # Silently accepting it would attribute random CO2e to widget counts.
        r = SAPFlatFileAdapter().parse(SAP_PIECES, {})
        self.assertEqual(r.rows, [])
        self.assertEqual(len(r.errors), 1)
        self.assertIn("not measurable", r.errors[0]["message"])

    def test_unknown_movement_type_is_refused_not_guessed(self):
        # If we can't infer category, better to error than to silently
        # park the row under the wrong Scope.
        r = SAPFlatFileAdapter().parse(SAP_UNKNOWN_MVT, {})
        self.assertEqual(r.rows, [])
        self.assertEqual(len(r.errors), 1)
        self.assertIn("Cannot infer category", r.errors[0]["message"])

    def test_accepts_three_date_formats(self):
        # SAP exports use the user's logon-locale date format. We accept all three.
        r = SAPFlatFileAdapter().parse(SAP_MIXED_DATES, {})
        self.assertEqual(len(r.rows), 3)
        self.assertEqual(r.rows[0].activity_date.isoformat(), "2025-04-03")
        self.assertEqual(r.rows[1].activity_date.isoformat(), "2025-04-04")
        self.assertEqual(r.rows[2].activity_date.isoformat(), "2025-04-05")

    def test_parse_date_helper_rejects_garbage(self):
        with self.assertRaises(ValueError):
            _parse_date("not a date")

    def test_reversal_pair_cancels_both_rows(self):
        # 262 with Storno-Belegnummer = the 261's Belegnummer must net to zero.
        # Surfacing the reversal in the error log is REQUIRED for audit.
        r = SAPFlatFileAdapter().parse(SAP_REVERSAL, {})
        self.assertEqual(len(r.rows), 0, "Reversal pair must produce no emission rows")
        self.assertEqual(len(r.errors), 1)
        msg = r.errors[0]["message"]
        self.assertIn("REVERSED", msg)
        self.assertIn("DOC100", msg)
        self.assertIn("DOC101", msg)

    def test_unmatched_reversal_is_surfaced_not_silently_dropped(self):
        r = SAPFlatFileAdapter().parse(SAP_REVERSAL_UNMATCHED, {})
        self.assertEqual(len(r.rows), 0)
        self.assertEqual(len(r.errors), 1)
        self.assertIn("unknown issue doc", r.errors[0]["message"])

    def test_latin1_encoded_file_decodes_correctly(self):
        r = SAPFlatFileAdapter().parse(SAP_LATIN1, {})
        self.assertEqual(len(r.rows), 1)
        # The 'ü' in 'Düsseldorf' must round-trip without corruption
        self.assertIn("Düsseldorf", r.rows[0].notes)

    def test_xlsx_path_parses_same_as_csv(self):
        # Build an xlsx in memory with the same data as SAP_HAPPY and parse it.
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Buchungsdatum", "Werk", "Material", "Materialkurztext",
                   "Menge", "Basismengeneinheit", "Bewegungsart"])
        ws.append(["03.04.2025", "US01", "FUEL-DSL-001", "Diesel", "1247,5", "LTR", "261"])
        buf = io.BytesIO()
        wb.save(buf)
        r = SAPFlatFileAdapter().parse(buf.getvalue(), {}, filename="x.xlsx")
        self.assertEqual(len(r.rows), 1)
        self.assertEqual(r.rows[0].unit_original, "LTR")
        self.assertEqual(r.rows[0].facility_source_code, "US01")


TRAVEL_DOC = b"""{
  "bookings": [{
    "id": "B1",
    "status": "TICKETED",
    "traveler": {"email": "a@b.com"},
    "airSegments": [
      {"from": "JFK", "to": "LHR", "cabin": "economy", "departureDate": "2025-04-08"},
      {"from": "JFK", "to": "ZZZ", "cabin": "economy", "departureDate": "2025-04-09"}
    ],
    "hotelStays": [{"propertyName": "H", "checkIn": "2025-04-08", "checkOut": "2025-04-10", "nights": 2}],
    "carRentals": []
  }]
}"""


class TravelAdapterTests(SimpleTestCase):
    def test_unknown_airport_skips_segment_not_booking(self):
        r = TravelAPIAdapter().parse(TRAVEL_DOC, {})
        # 1 valid air, 1 invalid air, 1 hotel = 2 rows + 1 error
        self.assertEqual(len(r.rows), 2)
        self.assertEqual(len(r.errors), 1)
        self.assertIn("ZZZ", r.errors[0]["message"])
        self.assertNotIn("JFK", r.errors[0]["message"])  # JFK is known, must not be listed

    def test_air_distance_classified_long_haul(self):
        r = TravelAPIAdapter().parse(TRAVEL_DOC, {})
        air = [row for row in r.rows if "Air" in row.category_hint[2]][0]
        # JFK-LHR is ~5500 km — must classify as long-haul (>= 3700)
        self.assertIn("Air – Long-haul", air.category_hint[2])
        self.assertGreater(air.quantity_original, Decimal("5000"))
        self.assertLess(air.quantity_original, Decimal("6000"))

    def test_cabin_class_in_subcategory(self):
        doc = b"""{
          "bookings": [{
            "id": "B1", "status": "TICKETED", "traveler": {"email": "a@b"},
            "airSegments": [
              {"from":"JFK","to":"LHR","cabin":"business","departureDate":"2025-04-08"},
              {"from":"JFK","to":"LHR","cabin":"economy","departureDate":"2025-04-09"},
              {"from":"JFK","to":"LHR","cabin":"first","departureDate":"2025-04-10"}
            ]
          }]
        }"""
        r = TravelAPIAdapter().parse(doc, {})
        subcats = sorted([row.category_hint[2] for row in r.rows])
        self.assertEqual(subcats, [
            "Air – Long-haul Business",
            "Air – Long-haul Economy",
            "Air – Long-haul First",
        ])

    def test_cancelled_booking_skipped_entirely(self):
        doc = b"""{"bookings":[
          {"id":"OK","status":"TICKETED","traveler":{"email":"a@b"},
            "airSegments":[{"from":"JFK","to":"LHR","cabin":"economy","departureDate":"2025-04-08"}]},
          {"id":"CXL","status":"CANCELLED","traveler":{"email":"a@b"},
            "airSegments":[{"from":"JFK","to":"LHR","cabin":"business","departureDate":"2025-04-09"}],
            "hotelStays":[{"propertyName":"X","checkIn":"2025-04-09","nights":3}]}
        ]}"""
        r = TravelAPIAdapter().parse(doc, {})
        # Only the TICKETED booking's air segment should produce a row.
        self.assertEqual(len(r.rows), 1)
        self.assertIn("cancelled", r.errors[0]["message"].lower())

    def test_cancelled_segment_within_live_booking_is_skipped(self):
        doc = b"""{"bookings":[{
          "id":"B1","status":"TICKETED","traveler":{"email":"a@b"},
          "railSegments":[
            {"from":"BER","to":"FRA","distance":545,"distanceUnit":"km","departureDate":"2025-04-24"},
            {"from":"FRA","to":"BER","distance":545,"distanceUnit":"km","departureDate":"2025-04-26","status":"CANCELLED"}
          ]
        }]}"""
        r = TravelAPIAdapter().parse(doc, {})
        rail_rows = [row for row in r.rows if "Rail" in row.category_hint[2]]
        self.assertEqual(len(rail_rows), 1)  # outbound only — return was cancelled

    def test_rail_segment_produces_ground_rail_row(self):
        doc = b"""{"bookings":[{
          "id":"B1","status":"TICKETED","traveler":{"email":"a@b"},
          "railSegments":[{"from":"BER","to":"FRA","distance":545,"distanceUnit":"km","departureDate":"2025-04-24"}]
        }]}"""
        r = TravelAPIAdapter().parse(doc, {})
        self.assertEqual(len(r.rows), 1)
        self.assertEqual(r.rows[0].category_hint, (3, "Business Travel", "Ground – Rail"))
        self.assertEqual(r.rows[0].quantity_original, Decimal("545"))

    def test_hotel_country_sets_region_override(self):
        doc = b"""{"bookings":[{
          "id":"B1","status":"TICKETED","traveler":{"email":"a@b"},
          "hotelStays":[{"propertyName":"X","country":"GB","checkIn":"2025-04-08","nights":3}]
        }]}"""
        r = TravelAPIAdapter().parse(doc, {})
        self.assertEqual(len(r.rows), 1)
        self.assertEqual(r.rows[0].region_override, "GB")


class UtilityAdapterTests(SimpleTestCase):
    def test_single_meter_happy_path(self):
        pdf = _make_pdf([
            "Service Period: 04/01/2025 - 04/30/2025",
            "Meter ID: MTR-001",
            "Total Energy Usage: 142,318 kWh",
        ])
        r = UtilityPDFAdapter().parse(pdf, {})
        self.assertEqual(r.errors, [])
        self.assertEqual(len(r.rows), 1)
        self.assertEqual(r.rows[0].quantity_original, Decimal("142318"))
        self.assertEqual(r.rows[0].unit_original, "kWh")
        self.assertEqual(r.rows[0].facility_source_code, "MTR-001")

    def test_multiple_meters_become_multiple_rows(self):
        pdf = _make_pdf([
            "Service Period: 04/01/2025 - 04/30/2025",
            "Meter ID: MTR-001",
            "Total Energy Usage: 100,000 kWh",
            "Meter ID: MTR-002",
            "Total Energy Usage: 25,000 kWh",
            "Meter ID: MTR-003",
            "Total Energy Usage: 8,500 kWh",
        ])
        r = UtilityPDFAdapter().parse(pdf, {})
        self.assertEqual(r.errors, [])
        self.assertEqual(len(r.rows), 3)
        ids = [row.facility_source_code for row in r.rows]
        self.assertEqual(ids, ["MTR-001", "MTR-002", "MTR-003"])

    def test_kvah_meter_is_rejected_not_treated_as_kwh(self):
        # kVAh is apparent power, not active energy. Treating it as kWh
        # would silently inflate the emission number by 10-25%.
        pdf = _make_pdf([
            "Service Period: 04/01/2025 - 04/30/2025",
            "Meter ID: MTR-REAC",
            "Total Energy Usage: 12,400 kVAh",
        ])
        r = UtilityPDFAdapter().parse(pdf, {})
        self.assertEqual(len(r.rows), 0)
        self.assertEqual(len(r.errors), 1)
        self.assertIn("kVAh", r.errors[0]["message"])
        self.assertIn("active energy", r.errors[0]["message"])

    def test_one_meter_can_fail_without_dropping_others(self):
        # The kVAh meter is rejected; the kWh meter on the same bill must
        # still produce a row.
        pdf = _make_pdf([
            "Service Period: 04/01/2025 - 04/30/2025",
            "Meter ID: MTR-GOOD",
            "Total Energy Usage: 100,000 kWh",
            "Meter ID: MTR-REAC",
            "Total Energy Usage: 12,400 kVAh",
        ])
        r = UtilityPDFAdapter().parse(pdf, {})
        self.assertEqual(len(r.rows), 1)
        self.assertEqual(r.rows[0].facility_source_code, "MTR-GOOD")
        self.assertEqual(len(r.errors), 1)

    def test_tariff_line_items_captured_in_payload(self):
        pdf = _make_pdf([
            "Service Period: 04/01/2025 - 04/30/2025",
            "Meter ID: MTR-001",
            "Peak Usage: 50,000 kWh",
            "Off-Peak Usage: 60,000 kWh",
            "Shoulder Usage: 32,318 kWh",
            "Total Energy Usage: 142,318 kWh",
            "Demand Charge: 412 kW",
        ])
        r = UtilityPDFAdapter().parse(pdf, {})
        self.assertEqual(len(r.rows), 1)
        payload = r.rows[0].raw_payload
        self.assertEqual(len(payload["tariff_breakdown"]), 3)
        labels = {item["period"] for item in payload["tariff_breakdown"]}
        self.assertEqual(labels, {"Peak", "Off-Peak", "Shoulder"})
        self.assertEqual(payload["demand_kw"], "412")

    def test_missing_service_period_produces_clear_error(self):
        pdf = _make_pdf([
            "Meter ID: MTR-001",
            "Total Energy Usage: 100,000 kWh",
        ])
        r = UtilityPDFAdapter().parse(pdf, {})
        self.assertEqual(len(r.rows), 0)
        self.assertIn("service period", r.errors[0]["message"].lower())


class UnitNormalizationTests(SimpleTestCase):
    def test_gallons_to_liters(self):
        unit, factor = canonicalize_unit("GAL")
        self.assertEqual(unit, "L")
        self.assertEqual(factor, Decimal("3.78541"))

    def test_mwh_to_kwh(self):
        unit, factor = canonicalize_unit("MWh")
        self.assertEqual(unit, "kWh")
        self.assertEqual(factor, Decimal("1000"))

    def test_miles_to_km(self):
        unit, factor = canonicalize_unit("mi")
        self.assertEqual(unit, "km")
        self.assertEqual(factor, Decimal("1.60934"))

    def test_unknown_unit_returns_none(self):
        # The adapter must surface UNIT_UNRESOLVED rather than silently
        # treating an unknown unit as canonical.
        unit, factor = canonicalize_unit("furlong")
        self.assertIsNone(unit)
