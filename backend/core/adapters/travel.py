"""
Corporate travel adapter.

Format choice: JSON upload that mirrors the Concur / Navan booking-export
shape. Real Concur has a Travel Booking API (v4) that returns nested JSON
with airSegments, hotelStays, and carRentals arrays per booking. Real
clients almost never plug their Concur directly into a third party on
day one; they email a JSON or CSV export from their travel manager.

So: we accept a JSON document whose top-level is { "bookings": [...] }
with the three categories. This matches what a sustainability analyst
gets when they ask the travel team for 'a Concur dump'.

Distances for flights are commonly missing — Concur often gives origin
and destination airport IATA codes but not nautical miles. We compute
great-circle distance from a small seeded airport coordinate lookup. If
either airport is unknown we flag MISSING_FACTOR rather than guessing.

Hotels: we receive nights, not energy. Factor is per night.

Ground: car rentals report distance in miles or km depending on region.
"""
import json
import math
import hashlib
from decimal import Decimal
from datetime import date

from .base import BaseAdapter, AdapterResult, NormalizedRow


# A tiny seeded airport table. In a real deployment this is OpenFlights
# or an IATA paid dataset. Coords are (lat, lon).
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


def great_circle_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def flight_subcategory(distance_km: float) -> str:
    # DEFRA convention: short-haul < 3700 km, long-haul >= 3700 km.
    # (Domestic is its own category but we don't try to detect country here.)
    return "Air – Short-haul" if distance_km < 3700 else "Air – Long-haul"


class TravelAPIAdapter(BaseAdapter):
    kind = "travel_api"

    def parse(self, file_bytes: bytes, config: dict) -> AdapterResult:
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

            for s_idx, seg in enumerate(booking.get("airSegments", []) or []):
                row_ref = f"{booking_id}/air/{s_idx}"
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
                    result.rows.append(NormalizedRow(
                        source_row_ref=row_ref,
                        raw_payload={**seg, "booking_id": booking_id, "traveler": traveler, "computed_distance_km": distance},
                        category_hint=(3, "Business Travel", flight_subcategory(distance)),
                        activity_date=flight_date,
                        quantity_original=Decimal(f"{distance:.3f}"),
                        unit_original="km",
                        notes=f"{origin}->{dest} {cabin} (computed great-circle)",
                    ))
                except (KeyError, ValueError) as e:
                    result.errors.append({"row_ref": row_ref, "message": f"Air segment parse error: {e}"})

            for h_idx, hot in enumerate(booking.get("hotelStays", []) or []):
                row_ref = f"{booking_id}/hotel/{h_idx}"
                try:
                    nights = int(hot["nights"])
                    check_in = date.fromisoformat(hot["checkIn"][:10])
                    result.rows.append(NormalizedRow(
                        source_row_ref=row_ref,
                        raw_payload={**hot, "booking_id": booking_id, "traveler": traveler},
                        category_hint=(3, "Business Travel", "Hotel"),
                        activity_date=check_in,
                        quantity_original=Decimal(nights),
                        unit_original="nights",
                        notes=hot.get("propertyName", ""),
                    ))
                except (KeyError, ValueError) as e:
                    result.errors.append({"row_ref": row_ref, "message": f"Hotel parse error: {e}"})

            for c_idx, car in enumerate(booking.get("carRentals", []) or []):
                row_ref = f"{booking_id}/car/{c_idx}"
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

        return result
