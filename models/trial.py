# models/trial.py — Trial data model

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# Fields written to Excel Tab 1 (in column order)
EXCEL_FIELDS = [
    "nct_id",
    "brief_title",
    "status",
    "phase",
    "start_date",
    "completion_date",
    "last_updated",
    "conditions",
    "intervention_name",
    "intervention_type",
    "drug_name_normalized",
    "moa",
    "drug_class",
    "molecular_targets",
    "approved_indications",
    "enrollment",
    "allocation",
    "intervention_model",
    "masking",
    "primary_outcomes",
    "eligibility_criteria",
    "age_range",
    "sponsor",
    "collaborators",
    "funder_type",
    "match_method",
]


@dataclass
class Trial:
    # ── Core identifiers ──────────────────────────────────────────────────────
    nct_id: str = ""
    brief_title: str = ""

    # ── Status & dates ────────────────────────────────────────────────────────
    status: str = ""
    phase: str = ""
    start_date: str = ""
    completion_date: str = ""
    last_updated: str = ""

    # ── Condition & intervention ──────────────────────────────────────────────
    conditions: list[str] = field(default_factory=list)
    intervention_name: str = ""
    intervention_type: str = ""

    # ── Drug enrichment fields (populated by enrichment pipeline) ─────────────
    drug_name_normalized: str = ""
    moa: str = ""
    drug_class: str = ""
    molecular_targets: list[str] = field(default_factory=list)
    approved_indications: list[str] = field(default_factory=list)

    # ── Design ────────────────────────────────────────────────────────────────
    enrollment: Optional[int] = None
    allocation: str = ""
    intervention_model: str = ""
    masking: str = ""

    # ── Outcomes & eligibility ────────────────────────────────────────────────
    primary_outcomes: list[str] = field(default_factory=list)
    eligibility_criteria: str = ""
    age_range: str = ""

    # ── Sponsor ───────────────────────────────────────────────────────────────
    sponsor: str = ""
    collaborators: list[str] = field(default_factory=list)
    funder_type: str = ""

    # ── Match metadata ────────────────────────────────────────────────────────
    match_method: str = ""

    # ── Model-only fields (not exported to Excel) ─────────────────────────────
    mesh_terms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    secondary_outcomes: list[str] = field(default_factory=list)
    accepts_healthy_volunteers: Optional[bool] = None
    locations: list[str] = field(default_factory=list)
    similarity_score: Optional[float] = None

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

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_ctgov(cls, raw: dict) -> Trial:
        """Construct a Trial from a raw CT.gov API v2 study object.

        Only fields available from CT.gov are populated here.  Drug-enrichment
        fields (moa, drug_class, etc.) are left at their defaults and filled
        later by the enrichment pipeline.
        """
        proto = raw.get("protocolSection", {})

        id_mod      = proto.get("identificationModule", {})
        status_mod  = proto.get("statusModule", {})
        cond_mod    = proto.get("conditionsModule", {})
        design_mod  = proto.get("designModule", {})
        arms_mod    = proto.get("armsInterventionsModule", {})
        outcomes_mod = proto.get("outcomesModule", {})
        elig_mod    = proto.get("eligibilityModule", {})
        contacts_mod = proto.get("contactsLocationsModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        derived     = raw.get("derivedSection", {})
        cond_browse = derived.get("conditionBrowseModule", {})

        # ── Identifiers / title ───────────────────────────────────────────────
        nct_id      = id_mod.get("nctId", "")
        brief_title = id_mod.get("briefTitle", "")

        # ── Status & dates ────────────────────────────────────────────────────
        status       = status_mod.get("overallStatus", "")
        start_date   = (status_mod.get("startDateStruct") or {}).get("date", "")
        completion_date = (
            status_mod.get("completionDateStruct") or {}
        ).get("date", "")
        last_updated = (
            status_mod.get("lastUpdatePostDateStruct") or {}
        ).get("date", "")

        # ── Phase ─────────────────────────────────────────────────────────────
        phases = design_mod.get("phases", [])
        phase  = "; ".join(phases) if phases else ""

        # ── Conditions / keywords ─────────────────────────────────────────────
        conditions = cond_mod.get("conditions", [])
        keywords   = cond_mod.get("keywords", [])

        # ── MeSH terms ────────────────────────────────────────────────────────
        mesh_terms = [
            m.get("term", "") for m in cond_browse.get("meshes", [])
        ]

        # ── Intervention — pick the first DRUG/BIOLOGICAL entry, else first ───
        interventions = arms_mod.get("interventions", [])
        chosen = next(
            (
                i for i in interventions
                if i.get("interventionType", "").upper()
                in ("DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT")
            ),
            interventions[0] if interventions else {},
        )
        intervention_name = chosen.get("name", "")
        intervention_type = chosen.get("interventionType", "")

        # ── Design ────────────────────────────────────────────────────────────
        enroll_info  = design_mod.get("enrollmentInfo") or {}
        enrollment   = enroll_info.get("count")          # int or None
        design_info  = design_mod.get("designInfo") or {}
        allocation   = design_info.get("allocation", "")
        intervention_model = design_info.get("interventionModel", "")
        masking_info = design_info.get("maskingInfo") or {}
        masking      = masking_info.get("masking", "")

        # ── Outcomes ─────────────────────────────────────────────────────────
        primary_outcomes = [
            o.get("measure", "") for o in outcomes_mod.get("primaryOutcomes", [])
        ]
        secondary_outcomes = [
            o.get("measure", "") for o in outcomes_mod.get("secondaryOutcomes", [])
        ]

        # ── Eligibility ───────────────────────────────────────────────────────
        eligibility_criteria     = elig_mod.get("eligibilityCriteria", "")
        accepts_healthy_volunteers = elig_mod.get("healthyVolunteers")
        min_age  = elig_mod.get("minimumAge", "")
        max_age  = elig_mod.get("maximumAge", "")
        if min_age or max_age:
            age_range = f"{min_age} – {max_age}".strip(" –")
        else:
            age_range = ""

        # ── Locations ─────────────────────────────────────────────────────────
        locations = [
            ", ".join(filter(None, [
                loc.get("city", ""),
                loc.get("state", ""),
                loc.get("country", ""),
            ]))
            for loc in contacts_mod.get("locations", [])
        ]

        # ── Sponsor / collaborators ───────────────────────────────────────────
        lead_sponsor  = sponsor_mod.get("leadSponsor") or {}
        sponsor       = lead_sponsor.get("name", "")
        funder_type   = lead_sponsor.get("class", "")
        collaborators = [
            c.get("name", "") for c in sponsor_mod.get("collaborators", [])
        ]

        return cls(
            nct_id=nct_id,
            brief_title=brief_title,
            status=status,
            phase=phase,
            start_date=start_date,
            completion_date=completion_date,
            last_updated=last_updated,
            conditions=conditions,
            intervention_name=intervention_name,
            intervention_type=intervention_type,
            enrollment=enrollment,
            allocation=allocation,
            intervention_model=intervention_model,
            masking=masking,
            primary_outcomes=primary_outcomes,
            secondary_outcomes=secondary_outcomes,
            eligibility_criteria=eligibility_criteria,
            age_range=age_range,
            accepts_healthy_volunteers=accepts_healthy_volunteers,
            sponsor=sponsor,
            collaborators=collaborators,
            funder_type=funder_type,
            mesh_terms=mesh_terms,
            keywords=keywords,
            locations=locations,
        )
