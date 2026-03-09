# models/drug.py — Drug enrichment model

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# Fields written to Excel Tab 2 (in column order)
EXCEL_FIELDS = [
    "input_name",
    "normalized_name",
    "brand_names",
    "chembl_id",
    "openfda_id",
    "moa",
    "drug_class",
    "molecular_targets",
    "target_chembl_ids",
    "molecule_type",
    "approved_indications",
    "approval_status",
    "administration_route",
]


@dataclass
class Drug:
    # ── Input / identity ──────────────────────────────────────────────────────
    input_name: str = ""          # raw name as it came from CT.gov
    normalized_name: str = ""     # lowercased/stripped before lookup

    # ── Cross-references ──────────────────────────────────────────────────────
    brand_names: list[str] = field(default_factory=list)
    chembl_id: str = ""
    openfda_id: str = ""          # FDA application number or drug@drugsfda id

    # ── Pharmacology (ChEMBL primary, OpenFDA fills gaps) ─────────────────────
    moa: str = ""                 # mechanism of action
    drug_class: str = ""
    molecular_targets: list[str] = field(default_factory=list)   # target names
    target_chembl_ids: list[str] = field(default_factory=list)   # CHEMBL target IDs
    molecule_type: str = ""       # e.g. Small molecule, Antibody, Protein

    # ── Regulatory ────────────────────────────────────────────────────────────
    approved_indications: list[str] = field(default_factory=list)
    approval_status: str = ""     # e.g. Approved, Investigational
    administration_route: str = ""

    # ── Model-only fields (not exported to Excel) ─────────────────────────────
    lookup_timestamp: Optional[str] = None   # ISO-8601 UTC string
    chembl_found: bool = False
    openfda_found: bool = False

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_excel_dict(self) -> dict:
        """Return a flat dict of only the Excel-exportable fields.

        List values are joined as semicolon-separated strings so they render
        cleanly in a single Excel cell.
        """
        row = {}
        for f in EXCEL_FIELDS:
            val = getattr(self, f)
            if isinstance(val, list):
                val = "; ".join(str(v) for v in val)
            elif val is None:
                val = ""
            row[f] = val
        return row

    # ── Helpers ───────────────────────────────────────────────────────────────

    def merge(self, other: Drug) -> None:
        """Fill empty fields on *self* with non-empty values from *other*.

        Used by the enrichment pipeline to layer OpenFDA data on top of a
        partially-populated ChEMBL result (or vice-versa) without overwriting
        values that are already present.
        """
        for f in EXCEL_FIELDS:
            self_val  = getattr(self, f)
            other_val = getattr(other, f)
            if isinstance(self_val, list):
                if not self_val and other_val:
                    setattr(self, f, other_val)
            else:
                if not self_val and other_val:
                    setattr(self, f, other_val)

        # merge model-only flags
        self.chembl_found  = self.chembl_found  or other.chembl_found
        self.openfda_found = self.openfda_found or other.openfda_found

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_input_name(cls, name: str) -> Drug:
        """Create a minimal Drug from a raw intervention name.

        Sets input_name and normalized_name; all enrichment fields are left
        at defaults and populated later by the enrichment pipeline.
        """
        return cls(
            input_name=name,
            normalized_name=name.strip().lower(),
            lookup_timestamp=datetime.now(timezone.utc).isoformat(),
        )
