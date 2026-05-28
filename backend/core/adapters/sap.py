"""
SAP fuel & procurement adapter.

Format choice: SAP flat-file (CSV or XLSX) export from transaction SE16N or
a custom Z-report dump. Why this over IDoc/BAPI/OData:

- IDoc is XML, requires SAP middleware (PI/PO) we don't have, and is
  overkill for monthly batch.
- BAPI/OData need a live SAP connection, NetWeaver creds, and an SDK.
  No client gives an intern that on day one.
- A facilities or procurement analyst CAN export a Z-report or SE16N
  view to CSV or Excel and email it. That is genuinely how this data
  moves in the wild for mid-market clients. See SOURCES.md.

What the adapter handles:
  - Pipe-delimited CSV (default) or comma. Configurable via source.adapter_config.
  - Excel .xlsx via openpyxl (sniffed by filename or magic bytes).
  - German *or* English column headers (synonym map).
  - Decimal commas (1.247,500 → 1247.5) — DE locale.
  - Three date formats: DD.MM.YYYY, YYYYMMDD, YYYY-MM-DD.
  - Encoding fallback: utf-8 → latin-1 → chardet auto-detect.
  - SAP movement types 261/201 (goods issue → fuel consumption) and 101 (procurement).
  - Reversal pairs: a 262 row with a Storno-Belegnummer pointing to a 261's
    Belegnummer cancels both rows out (zero net emissions). Audit trail
    surfaces the reversal so analysts can see what was netted.
  - 'STK' / 'PCE' / 'EA' units rejected — pieces aren't a measurable quantity.
"""
import csv
import io
import hashlib
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime

from .base import BaseAdapter, AdapterResult, NormalizedRow


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
    "belegnummer": "doc_number",
    "document number": "doc_number",
    "storno-belegnummer": "reverses_doc",
    "reversal document": "reverses_doc",
}

MOVEMENT_TO_CATEGORY = {
    "261": (1, "Stationary Combustion", "Diesel"),
    "201": (1, "Stationary Combustion", "Diesel"),
    "101": (3, "Purchased Goods and Services", "Procurement"),
}
# 262 is the reversal of 261. We handle it specially — see _net_reversals.
REVERSAL_MOVEMENT_TYPES = {"262", "202"}

MATERIAL_TO_CATEGORY = {
    "FUEL-DSL": (1, "Stationary Combustion", "Diesel"),
    "FUEL-PET": (1, "Mobile Combustion", "Petrol"),
    "FUEL-NG": (1, "Stationary Combustion", "Natural Gas"),
}


def _parse_decimal_de(s: str) -> Decimal:
    # DE format: '1.247,500' = 1247.5
    # dot = thousands sep, comma = decimal. Galat parse kiya to 1000x bigger!
    # Yahi wali silent bug se sab darte hain — explicit parse karna zaroori hai.
    s = (s or "").strip().replace(".", "").replace(",", ".")
    return Decimal(s)


def _parse_date(s: str):
    """
    SAP wale ki marzi — user ka logon locale alag, transaction alag,
    date format alag. DD.MM.YYYY (DE), YYYYMMDD (technical), YYYY-MM-DD (S/4).
    Teeno try karte hain.
    """
    s = (s or "").strip()
    for fmt in ("%d.%m.%Y", "%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {s!r}")


def _decode(file_bytes: bytes) -> str:
    """
    SAP exports come in whatever encoding the exporter's machine was set to.
    Try utf-8 (with BOM), then latin-1, then chardet auto-detect as a last
    resort. Latin-1 never errors, so the chardet stage is only reached if
    we explicitly want to surface its detected name later.
    """
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    try:
        import chardet
        detected = chardet.detect(file_bytes[:65536])
        if detected and detected.get("encoding") and detected.get("confidence", 0) > 0.5:
            return file_bytes.decode(detected["encoding"], errors="replace")
    except Exception:
        pass
    return file_bytes.decode("latin-1", errors="replace")


def _is_xlsx(file_bytes: bytes, filename: str = "") -> bool:
    # xlsx is a zip; first bytes are PK\x03\x04
    return file_bytes[:4] == b"PK\x03\x04" or filename.lower().endswith(".xlsx")


def _read_xlsx(file_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    """Read first sheet as list-of-strings rows."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    headers = ["" if c is None else str(c) for c in rows[0]]
    body = []
    for r in rows[1:]:
        # Preserve the original SAP DE number/date formatting if the export
        # was string-typed. If the user pre-formatted to Excel numbers,
        # str(Decimal-ish) round-trips fine for our parser.
        body.append(["" if c is None else (
            f"{c:.3f}".replace(".", ",") if isinstance(c, float) else
            c.strftime("%d.%m.%Y") if hasattr(c, "strftime") else
            str(c)
        ) for c in r])
    return headers, body


class SAPFlatFileAdapter(BaseAdapter):
    kind = "sap_flatfile"

    def parse(self, file_bytes: bytes, config: dict, filename: str = "") -> AdapterResult:
        result = AdapterResult()

        if _is_xlsx(file_bytes, filename):
            raw_headers, body = _read_xlsx(file_bytes)
        else:
            text = _decode(file_bytes)
            delim = config.get("delimiter", "|")
            reader = csv.reader(io.StringIO(text), delimiter=delim)
            try:
                raw_headers = next(reader)
            except StopIteration:
                result.errors.append({"row_ref": "header", "message": "Empty file"})
                return result
            body = [list(r) for r in reader]

        if not raw_headers:
            result.errors.append({"row_ref": "header", "message": "Empty file"})
            return result

        headers = []
        for h in raw_headers:
            key = (h or "").strip().lower().lstrip("﻿")
            headers.append(HEADER_SYNONYMS.get(key, key))

        required = {"doc_date", "plant", "quantity", "unit"}
        missing = required - set(headers)
        if missing:
            result.errors.append({"row_ref": "header", "message": f"Missing required columns: {sorted(missing)}"})
            return result

        # First pass: parse each row into either an "issue" (261/201/101) or a
        # "reversal" (262/202). Reversals are netted in a second pass.
        parsed_issues: list[tuple[str, dict, NormalizedRow]] = []  # (doc_number, payload, row)
        parsed_reversals: list[tuple[str, str, dict]] = []  # (doc_number, reverses_doc, payload)

        for idx, row in enumerate(body, start=2):
            if not any(str(c).strip() for c in row):
                continue
            row_ref = f"line {idx}"
            try:
                payload = dict(zip(headers, [str(c).strip() for c in row]))
                quantity = _parse_decimal_de(payload["quantity"])
                doc_date = _parse_date(payload["doc_date"])
                unit = payload["unit"] or ""
                mvt = payload.get("movement_type", "")
                material = payload.get("material", "")
                doc_number = payload.get("doc_number", "")
                reverses_doc = payload.get("reverses_doc", "")

                if mvt in REVERSAL_MOVEMENT_TYPES:
                    if not reverses_doc:
                        result.errors.append({"row_ref": row_ref,
                            "message": f"Movement type {mvt} (reversal) has no Storno-Belegnummer reference."})
                        continue
                    parsed_reversals.append((doc_number, reverses_doc, payload))
                    continue

                cat = None
                for prefix, c in MATERIAL_TO_CATEGORY.items():
                    if material.upper().startswith(prefix):
                        cat = c
                        break
                if cat is None:
                    cat = MOVEMENT_TO_CATEGORY.get(mvt)
                if cat is None:
                    result.errors.append({"row_ref": row_ref,
                        "message": f"Cannot infer category from movement={mvt!r} material={material!r}"})
                    continue

                # STK = Stück = pieces. Yeh count hai, quantity nahi.
                # Silent accept karna matlab "12 widgets ka emission" calculate karna —
                # complete garbage. Reject karke analyst ko bolo.
                if unit.upper() in {"STK", "PCE", "EA"}:
                    result.errors.append({"row_ref": row_ref,
                        "message": f"Unit {unit!r} is not measurable for emissions (a count, not a quantity)"})
                    continue

                nrow = NormalizedRow(
                    source_row_ref=row_ref,
                    raw_payload=payload,
                    category_hint=cat,
                    activity_date=doc_date,
                    quantity_original=quantity,
                    unit_original=unit,
                    facility_source_code=payload["plant"],
                    notes=payload.get("material_desc", "") or "",
                )
                parsed_issues.append((doc_number, payload, nrow))
            except (InvalidOperation, ValueError, KeyError) as e:
                result.errors.append({"row_ref": row_ref, "message": f"Parse error: {e}"})

        # Second pass: reversal netting.
        # SAP mein 262 ka movement type 261 ko cancel karta hai (Storno-Belegnummer
        # se reference karta hai). Most adapters yeh miss karte hain aur double-count
        # ho jata hai. Pehle saare issues collect karo, phir 262 ke saath pair karke
        # dono drop kar do. Audit log mein REVERSED message daalna mandatory hai —
        # analyst ko dikhna chahiye ki row gaayab kyun hai.
        cancelled_docs: set[str] = set()
        for rev_doc, ref_doc, rev_payload in parsed_reversals:
            matched = next((d for (d, _, r) in parsed_issues if d == ref_doc), None)
            if matched:
                cancelled_docs.add(ref_doc)
                result.errors.append({
                    "row_ref": f"reversal {rev_doc}",
                    "message": f"REVERSED: issue doc {ref_doc} cancelled by reversal doc {rev_doc} (movement 262). Both rows omitted from emissions.",
                })
            else:
                result.errors.append({
                    "row_ref": f"reversal {rev_doc}",
                    "message": f"Reversal doc {rev_doc} references unknown issue doc {ref_doc!r} (not in this file). Reversal ignored.",
                })

        for doc_number, payload, nrow in parsed_issues:
            if doc_number in cancelled_docs:
                continue
            result.rows.append(nrow)

        return result


def row_hash(payload: dict) -> str:
    import json
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
