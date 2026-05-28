"""
Corporate travel adapter.

Format choice: JSON upload that mirrors the Concur / Navan booking-export
shape. Real Concur has a Travel Booking API (v4) that returns nested JSON
with airSegments, hotelStays, carRentals and railSegments arrays per
booking. Real clients almost never plug their Concur directly into a
third party on day one; they email a JSON or CSV export from their
travel manager. See SOURCES.md.

What this adapter handles:
  - airSegments — great-circle distance from IATA codes + cabin-class
    differentiation (Economy / Premium / Business / First). Per DEFRA,
    business class emits ~2.9× economy per km.
  - hotelStays — nights × country-specific factor. Country comes from a
    `country` field (ISO-2) on the stay; falls back to GLOBAL.
  - carRentals — distance in km or miles.
  - railSegments — distance in km. Concur's UK/EU customers use this
    heavily; ignoring it would create a large blind spot.
  - status=CANCELLED filtering at booking-level AND segment-level —
    Concur exports both ticketed and cancelled records, and silently
    processing cancellations would over-report.
  - Unknown airports skip the segment, not the booking.
"""
import json
import math
from decimal import Decimal
from datetime import date

from .base import BaseAdapter, AdapterResult, NormalizedRow


# Seeded airport coordinates. In a real deployment this is OpenFlights
# (~7,500 airports, free) or paid IATA data. Coords are (lat, lon).
AIRPORTS = {
    "JFK": (40.6413, -73.7781),
    "LHR": (51.4700, -0.4543),
    "FRA": (50.0379, 8.5622),
    "BOM": (19.0896, 72.8656),
    "DEL": (28.5562, 77.1000),
    "SFO": (37.6213, -122.3790),
    "SIN": (1.3644, 103.9915),
    "DXB": (25.2532, 55.3657),
}

# Cabin-class → canonical subcategory suffix. Anything we don't recognize
# falls back to Economy — that's a conservative default for our purposes
# (lower per-km factor; underestimation flagged in audit if relevant).
CABIN_MAP = {
    "economy": "Economy", "y": "Economy", "coach": "Economy",
    "premium": "Premium Economy", "premium_economy": "Premium Economy",
    "premium economy": "Premium Economy", "w": "Premium Economy",
    "business": "Business", "biz": "Business", "j": "Business", "c": "Business",
    "first": "First", "f": "First",
}


def great_circle_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def flight_subcategory(distance_km: float, cabin: str) -> str:
    """
    DEFRA ka rule: short-haul < 3700 km, long-haul >= 3700 km.
    Cabin ko subcategory mein daal dete hain (multiplier nahi rakhte) —
    DEFRA per-class published factors deta hai, multiplier approach mein
    snapshot ka concept toot jaata hai.
    Business class ~2.9x economy hota hai per km, isko ignore karna matlab
    premium-cabin emissions silently 3x underreport.
    """
    haul = "Air – Short-haul" if distance_km < 3700 else "Air – Long-haul"
    cabin_norm = CABIN_MAP.get((cabin or "").strip().lower(), "Economy")
    return f"{haul} {cabin_norm}"


def _is_cancelled(rec: dict) -> bool:
    """
    Concur export mein cancelled bookings bhi aate hain — ticketed ke saath.
    Silently process kar diya to over-reporting. Status CANCELLED/CXLD/VOID
    sab handle karte hain, plus top-level cancelled=true bhi.
    """
    status = (rec.get("status") or "").strip().upper()
    if status in {"CANCELLED", "CANCELED", "CXLD", "VOID", "VOIDED"}:
        return True
    return bool(rec.get("cancelled"))


class TravelAPIAdapter(BaseAdapter):
    kind = "travel_api"

    def parse(self, file_bytes: bytes, config: dict, filename: str = "") -> AdapterResult:
        result = AdapterResult()
        try:
            doc = json.loads(file_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            result.errors.append({"row_ref": "doc", "message": f"Invalid JSON: {e}"})
            return result

        bookings = doc.get("bookings", [])
        for b_idx, booking in enumerate(bookings):
            booking_id = booking.get("id", f"booking_{b_idx}")
            traveler = booking.get("traveler", {}).get("email", "")

            if _is_cancelled(booking):
                result.errors.append({"row_ref": booking_id,
                    "message": f"Booking {booking_id} is cancelled — all segments skipped."})
                continue

            # Air
            for s_idx, seg in enumerate(booking.get("airSegments", []) or []):
                row_ref = f"{booking_id}/air/{s_idx}"
                if _is_cancelled(seg):
                    result.errors.append({"row_ref": row_ref, "message": "Cancelled air segment — skipped."})
                    continue
                try:
                    origin = (seg.get("from") or "").upper()
                    dest = (seg.get("to") or "").upper()
                    unknown = [c for c in (origin, dest) if c not in AIRPORTS]
                    if unknown:
                        result.errors.append({"row_ref": row_ref, "message": f"Unknown airport code(s): {', '.join(unknown)}"})
                        continue
                    distance = great_circle_km(AIRPORTS[origin], AIRPORTS[dest])
                    cabin = seg.get("cabin", "economy")
                    flight_date = date.fromisoformat(seg["departureDate"][:10])
                    subcat = flight_subcategory(distance, cabin)
                    result.rows.append(NormalizedRow(
                        source_row_ref=row_ref,
                        raw_payload={**seg, "booking_id": booking_id, "traveler": traveler, "computed_distance_km": distance},
                        category_hint=(3, "Business Travel", subcat),
                        activity_date=flight_date,
                        quantity_original=Decimal(f"{distance:.3f}"),
                        unit_original="km",
                        notes=f"{origin}->{dest} {CABIN_MAP.get(cabin.lower(), 'Economy')} (great-circle)",
                    ))
                except (KeyError, ValueError) as e:
                    result.errors.append({"row_ref": row_ref, "message": f"Air segment parse error: {e}"})

            # Hotels — country-aware
            for h_idx, hot in enumerate(booking.get("hotelStays", []) or []):
                row_ref = f"{booking_id}/hotel/{h_idx}"
                if _is_cancelled(hot):
                    result.errors.append({"row_ref": row_ref, "message": "Cancelled hotel stay — skipped."})
                    continue
                try:
                    nights = int(hot["nights"])
                    check_in = date.fromisoformat(hot["checkIn"][:10])
                    # Hotel ka country code factor lookup drive karta hai.
                    # UK hotel ~10 kg/night, US ~30, IN ~40, SG ~66 — 6x variation!
                    # Sirf tenant region use karna matlab business trips ke hotel
                    # factors galat lag jayenge. Region override pattern saved us here.
                    country = (hot.get("country") or "").upper()
                    payload = {**hot, "booking_id": booking_id, "traveler": traveler}
                    result.rows.append(NormalizedRow(
                        source_row_ref=row_ref,
                        raw_payload=payload,
                        category_hint=(3, "Business Travel", "Hotel"),
                        activity_date=check_in,
                        quantity_original=Decimal(nights),
                        unit_original="nights",
                        region_override=country or None,
                        notes=f"{hot.get('propertyName', '')} ({country})" if country else hot.get("propertyName", ""),
                    ))
                except (KeyError, ValueError) as e:
                    result.errors.append({"row_ref": row_ref, "message": f"Hotel parse error: {e}"})

            # Ground — car rentals
            for c_idx, car in enumerate(booking.get("carRentals", []) or []):
                row_ref = f"{booking_id}/car/{c_idx}"
                if _is_cancelled(car):
                    result.errors.append({"row_ref": row_ref, "message": "Cancelled car rental — skipped."})
                    continue
                try:
                    distance = Decimal(str(car["distance"]))
                    unit = car.get("distanceUnit", "km")
                    rental_date = date.fromisoformat(car["pickupDate"][:10])
                    result.rows.append(NormalizedRow(
                        source_row_ref=row_ref,
                        raw_payload={**car, "booking_id": booking_id, "traveler": traveler},
                        category_hint=(3, "Business Travel", "Ground – Car"),
                        activity_date=rental_date,
                        quantity_original=distance,
                        unit_original=unit,
                        notes=car.get("vendor", ""),
                    ))
                except (KeyError, ValueError) as e:
                    result.errors.append({"row_ref": row_ref, "message": f"Car parse error: {e}"})

            # Rail — Concur's railSegments[]. Distance in km is required.
            for r_idx, rail in enumerate(booking.get("railSegments", []) or []):
                row_ref = f"{booking_id}/rail/{r_idx}"
                if _is_cancelled(rail):
                    result.errors.append({"row_ref": row_ref, "message": "Cancelled rail segment — skipped."})
                    continue
                try:
                    distance = Decimal(str(rail["distance"]))
                    unit = rail.get("distanceUnit", "km")
                    rail_date = date.fromisoformat(rail["departureDate"][:10])
                    origin = rail.get("from", "")
                    dest = rail.get("to", "")
                    result.rows.append(NormalizedRow(
                        source_row_ref=row_ref,
                        raw_payload={**rail, "booking_id": booking_id, "traveler": traveler},
                        category_hint=(3, "Business Travel", "Ground – Rail"),
                        activity_date=rail_date,
                        quantity_original=distance,
                        unit_original=unit,
                        notes=f"{origin} -> {dest}" if (origin or dest) else "",
                    ))
                except (KeyError, ValueError) as e:
                    result.errors.append({"row_ref": row_ref, "message": f"Rail segment parse error: {e}"})

        return result
