"""
MobyDik processing logic adapted from the original script.

Instead of reading CSV files, it operates on a list of document dicts
fetched from the Hypatos API. The field mapping translates Hypatos entity
keys to MobyDik column names so the sorting and classification logic stays
unchanged.
"""

from __future__ import annotations

import csv
import datetime
import io
from dataclasses import dataclass, field
from typing import Any


# The canonical MobyDik column names the original script used.
MOBYDIK_COLUMNS = [
    "Belegnummer",
    "Lieferant",
    "Sachbearbeiter",
    "Artikelnummer",
    "Artikelnummer (Lieferant)",
    "Menge Gebinde",
    "Gebinde",
    "Grundeinheit",
    "Kostenstelle",
    "Lagerort (Bezeichnung)",
    "Bemerkung",
    "Bestellnummer",
    "Artikelbezeichnung",
]


@dataclass
class ProcessingConfig:
    """User-supplied configuration that drives sorting and classification."""

    # Hypatos entity key -> MobyDik column name
    field_mapping: dict[str, str] = field(default_factory=dict)

    # Suppliers for which we sort by Menge (quantity) first
    menge_lieferanten: list[str] = field(default_factory=list)

    # Suppliers for which we keep original order (no sort)
    lieferanten_auftragsinfo: list[str] = field(default_factory=list)

    # Article numbers that trigger the "float quantity" flag
    float_article_patterns: list[str] = field(default_factory=list)

    # Article numbers to watch (info articles)
    info_articles: list[str] = field(default_factory=list)


@dataclass
class ProcessingResult:
    """Output produced by process_documents()."""

    rows: list[dict]              # Sorted, mapped rows ready for display / CSV export
    floats: list[dict]            # Rows with floating-point Menge Gebinde
    directs: list[dict]           # Rows flagged as direct deliveries (#DL / #Direkt)
    info_hits: list[dict]         # Rows matching info_articles
    log: list[str]                # Human-readable processing log
    run_at: str = ""

    def __post_init__(self):
        self.run_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _resolve_entity_value(entities: dict, hypatos_key: str) -> str:
    """
    Resolves a (possibly nested) entity key such as 'items.articleNumber'
    to a scalar string.  For list fields (line items) returns a
    semicolon-joined string of all values found.
    """
    if "." in hypatos_key:
        parent, child = hypatos_key.split(".", 1)
        parent_val = entities.get(parent, [])
        if isinstance(parent_val, list):
            values = [str(item.get(child, "")) for item in parent_val if isinstance(item, dict)]
            return "; ".join(v for v in values if v)
        return ""
    val = entities.get(hypatos_key, "")
    if val is None:
        return ""
    if isinstance(val, list):
        return "; ".join(str(v) for v in val)
    return str(val)


def _map_document(doc: dict, mapping: dict[str, str]) -> dict:
    """
    Converts a Hypatos document dict into a flat MobyDik-style row dict
    using the user-supplied field mapping.
    """
    entities = doc.get("entities") or {}
    row: dict[str, str] = {col: "" for col in MOBYDIK_COLUMNS}

    # Fallback: use document-level fields for common columns when not mapped
    row["Belegnummer"] = doc.get("id", "")[:10]
    row["_doc_id"] = doc.get("id", "")
    row["_doc_title"] = doc.get("title", "")
    row["_doc_state"] = doc.get("state", "")

    for hypatos_key, mobydik_col in mapping.items():
        if mobydik_col and mobydik_col in MOBYDIK_COLUMNS:
            row[mobydik_col] = _resolve_entity_value(entities, hypatos_key)

    return row


def _sort_key_ref(row: dict, max_len: int) -> str:
    ref = row.get("Artikelnummer (Lieferant)", "")
    return ref.lstrip("0").ljust(max_len, "0")


def _sort_key_menge(row: dict) -> float:
    raw = row.get("Grundeinheit", "")
    try:
        # Original script: float(x["Grundeinheit"][1: x["Grundeinheit"].find(" ")])
        if raw.startswith(" ") or (len(raw) > 1 and raw[0].isalpha()):
            segment = raw[1:raw.find(" ", 1)] if " " in raw[1:] else raw[1:]
        else:
            segment = raw[: raw.find(" ")] if " " in raw else raw
        return float(segment.replace(",", "."))
    except (ValueError, IndexError):
        return 0.0


def process_documents(
    documents: list[dict],
    config: ProcessingConfig,
) -> ProcessingResult:
    """
    Core MobyDik logic adapted for Hypatos documents.

    1. Maps each document's entities to MobyDik columns.
    2. Splits rows into direct-delivery (dl) and warehouse (la) lists.
    3. Sorts based on supplier rules.
    4. Flags floats, direct deliveries, and info articles.
    """
    log: list[str] = []
    all_rows: list[dict] = []
    floats: list[dict] = []
    directs: list[dict] = []
    info_hits: list[dict] = []

    if not documents:
        log.append("No documents to process.")
        return ProcessingResult([], floats, directs, info_hits, log)

    # Group documents by Belegnummer (first 10 chars of id, or mapped field)
    groups: dict[str, list[dict]] = {}
    for doc in documents:
        row = _map_document(doc, config.field_mapping)
        belegnr = row.get("Belegnummer", doc.get("id", "unknown"))[:10]
        row["Belegnummer"] = belegnr
        groups.setdefault(belegnr, []).append(row)

    for belegnr, rows in groups.items():
        log.append(f"\n--- Beleg: {belegnr} ---")

        lieferant = rows[0].get("Lieferant", "")
        employee = rows[0].get("Sachbearbeiter", "")
        if lieferant:
            log.append(f"Lieferant: {lieferant}")
        if employee:
            log.append(f"Sachbearbeiter: {employee}")

        # Split into warehouse (la) and direct delivery (dl) lists
        dl: list[dict] = []
        la: list[dict] = []
        for row in rows:
            if row.get("Lagerort (Bezeichnung)") and not row.get("Kostenstelle"):
                la.append(row)
            else:
                # Strip spaces from Bemerkung (barcode field)
                row["Bemerkung"] = row.get("Bemerkung", "").replace(" ", "")
                dl.append(row)

        # Calculate max lengths for display alignment
        max_ref = max((len(r.get("Artikelnummer (Lieferant)", "")) for r in dl + la), default=0)

        # Choose sort formula
        if lieferant in config.menge_lieferanten:
            sort_key = lambda r: (_sort_key_menge(r), _sort_key_ref(r, max_ref))
            log.append("Sort type: Menge")
        else:
            sort_key = lambda r: (_sort_key_ref(r, max_ref), _sort_key_menge(r))
            log.append("Sort type: REF")

        if lieferant not in config.lieferanten_auftragsinfo:
            dl.sort(key=sort_key)
            la.sort(key=sort_key)

        sorted_rows = dl + la

        for row in sorted_rows:
            # Float detection
            menge = row.get("Menge Gebinde", "")
            if "," in menge or ("." in menge and not menge.endswith(".0")):
                floats.append(row)
                log.append(f"  [FLOAT] {row.get('Artikelnummer')} Menge={menge}")

            # Direct delivery detection
            bemerkung = row.get("Bemerkung", "")
            bezeichnung = row.get("Artikelbezeichnung", "")
            if "#DL" in bemerkung or "#Direkt" in bezeichnung:
                directs.append(row)
                log.append(f"  [DIREKT] {row.get('Artikelnummer')}")

            # Info article detection
            art_nr = row.get("Artikelnummer", "")
            for pattern in config.info_articles:
                if pattern and pattern in art_nr:
                    info_hits.append(row)
                    log.append(f"  [INFO] {art_nr} matches pattern '{pattern}'")
                    break

            log.append(
                f"  Art: {row.get('Artikelnummer')} | "
                f"REF: {row.get('Artikelnummer (Lieferant)')} | "
                f"Menge: {row.get('Menge Gebinde')} {row.get('Gebinde')} | "
                f"KST: {row.get('Kostenstelle')} | "
                f"Lagerort: {row.get('Lagerort (Bezeichnung)')}"
            )

        all_rows.extend(sorted_rows)

    log.append(f"\nDone. {len(all_rows)} rows processed across {len(groups)} document group(s).")
    log.append(f"Floats: {len(floats)} | Directs: {len(directs)} | Info hits: {len(info_hits)}")

    return ProcessingResult(all_rows, floats, directs, info_hits, log)


def rows_to_csv(rows: list[dict]) -> str:
    """Serialises processed rows to a CSV string for download."""
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=MOBYDIK_COLUMNS,
        delimiter=";",
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
