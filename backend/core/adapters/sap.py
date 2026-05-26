"""
SAP fuel & procurement adapter.

Format choice: SAP flat-file (CSV) export from transaction SE16N or a
custom Z-report dump. Why this over IDoc/BAPI/OData:

- IDoc is XML, requires SAP middleware (PI/PO) we don't have, and is
  overkill for monthly batch.
- BAPI/OData need a live SAP connection, NetWeaver creds, and an SDK.
  No client gives an intern that on day one.
- A facilities or procurement analyst CAN export a Z-report or SE16N
  view to CSV and email it. That is genuinely how this data moves in
  the wild for mid-market clients. See SOURCES.md.

The shape we handle: pipe-delimited UTF-8 CSV with German column headers
(common in DE-rooted SAP installs — clients have asked us about this),
plant codes, document dates in DD.MM.YYYY, decimal commas, and a unit
column that is sometimes blank or set to a non-ISO code ('LTR' for L,
'GAL' for gallons US, 'STK' for 'pieces' which we must reject).
"""
import csv
import io
import hashlib
from decimal import Decimal, InvalidOperation
from datetime import datetime

from .base import BaseAdapter, AdapterResult, NormalizedRow


# Maps the German SAP headers we expect → our internal field names.
# Real SAP exports use either German or English depending on user logon
# locale. We accept both via a synonym table.
HEADER_SYNONYMS = {
    "buchungsdatum": "doc_date",
    "posting date": "doc_date",
    "werk": "plant",
    "plant": "plant",
    "material": "material",
    "materialkurztext": "material_desc",
    "material description": "material_desc",
    "menge": "quantity",
    "quantity": "quantity",
    "basismengeneinheit": "unit",
    "base unit of measure": "unit",
    "bewegungsart": "movement_type",
    "movement type": "movement_type",
}

# SAP movement type → emission category. 261/262 are goods issue/reversal
# to a cost center, typical for fuel consumption. 101 is goods receipt
# from PO, typical for procurement.
MOVEMENT_TO_CATEGORY = {
    "261": (1, "Stationary Combustion", "Diesel"),
    "201": (1, "Stationary Combustion", "Diesel"),
    "101": (3, "Purchased Goods and Services", "Procurement"),
}

# Material code prefix → fuel kind. In real SAP these are client-specific;
# we encode a small lookup that mirrors what a real onboarding would gather.
MATERIAL_TO_CATEGORY = {
    "FUEL-DSL": (1, "Stationary Combustion", "Diesel"),
    "FUEL-PET": (1, "Mobile Combustion", "Petrol"),
    "FUEL-NG": (1, "Stationary Combustion", "Natural Gas"),
}


def _parse_decimal_de(s: str) -> Decimal:
    """SAP DE exports use '.' as thousands sep and ',' as decimal."""
    s = (s or "").strip().replace(".", "").replace(",", ".")
    return Decimal(s)


def _parse_date_de(s: str):
    return datetime.strptime(s.strip(), "%d.%m.%Y").date()


class SAPFlatFileAdapter(BaseAdapter):
    kind = "sap_flatfile"

    def parse(self, file_bytes: bytes, config: dict) -> AdapterResult:
        result = AdapterResult()
        # SAP exports are often latin-1 or cp1252 from a Windows export
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1")

        delim = config.get("delimiter", "|")
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        try:
            raw_headers = next(reader)
        except StopIteration:
            result.errors.append({"row_ref": "header", "message": "Empty file"})
            return result

        headers = []
        for h in raw_headers:
            key = h.strip().lower().lstrip("﻿")
            headers.append(HEADER_SYNONYMS.get(key, key))

        required = {"doc_date", "plant", "quantity", "unit"}
        missing = required - set(headers)
        if missing:
            result.errors.append({"row_ref": "header", "message": f"Missing required columns: {sorted(missing)}"})
            return result

        for idx, row in enumerate(reader, start=2):  # line 1 is header
            if not any(c.strip() for c in row):
                continue
            row_ref = f"line {idx}"
            try:
                payload = dict(zip(headers, [c.strip() for c in row]))
                quantity = _parse_decimal_de(payload["quantity"])
                doc_date = _parse_date_de(payload["doc_date"])
                unit = payload["unit"] or ""

                mvt = payload.get("movement_type", "")
                material = payload.get("material", "")
                cat = None
                for prefix, c in MATERIAL_TO_CATEGORY.items():
                    if material.upper().startswith(prefix):
                        cat = c
                        break
                if cat is None:
                    cat = MOVEMENT_TO_CATEGORY.get(mvt)
                if cat is None:
                    # We refuse to guess. Better to surface as error than
                    # silently assign Scope 3 to a Scope 1 fuel row.
                    result.errors.append({"row_ref": row_ref, "message": f"Cannot infer category from movement={mvt!r} material={material!r}"})
                    continue

                # 'STK' means 'pieces' in SAP-speak. That's an unmeasurable
                # unit for emissions. Reject loudly.
                if unit.upper() in {"STK", "PCE", "EA"}:
                    result.errors.append({"row_ref": row_ref, "message": f"Unit {unit!r} is not measurable for emissions (a count, not a quantity)"})
                    continue

                result.rows.append(NormalizedRow(
                    source_row_ref=row_ref,
                    raw_payload=payload,
                    category_hint=cat,
                    activity_date=doc_date,
                    quantity_original=quantity,
                    unit_original=unit,
                    facility_source_code=payload["plant"],
                    notes=payload.get("material_desc", "") or "",
                ))
            except (InvalidOperation, ValueError, KeyError) as e:
                result.errors.append({"row_ref": row_ref, "message": f"Parse error: {e}"})
        return result


def row_hash(payload: dict) -> str:
    import json
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
