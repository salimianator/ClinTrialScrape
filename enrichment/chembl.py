# enrichment/chembl.py — ChEMBL API client

from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import (
    CHEMBL_BASE_URL,
    CHEMBL_RATE_LIMIT_DELAY,
    CHEMBL_REQUEST_TIMEOUT,
)
from models.drug import Drug

logger = logging.getLogger(__name__)

# max_phase integer → human-readable approval status
_PHASE_TO_STATUS = {
    4:  "Approved",
    3:  "Phase 3",
    2:  "Phase 2",
    1:  "Phase 1",
    0:  "Preclinical",
    -1: "Withdrawn",
}

# How many mechanism targets to resolve by name (each costs one API call)
_MAX_TARGET_LOOKUPS = 5


class ChEMBLError(Exception):
    """Raised on unrecoverable ChEMBL API errors."""


class ChEMBLClient:
    """Client for the ChEMBL REST API.

    Populates: chembl_id, moa, drug_class (via ATC), molecular_targets,
    target_chembl_ids, molecule_type, approval_status, brand_names,
    approved_indications.
    """

    def __init__(self) -> None:
        self.session = self._build_session()

    # ── Public API ────────────────────────────────────────────────────────────

    def enrich(self, drug: Drug) -> Drug:
        """Look up *drug* by normalised name and return an enriched copy.

        Returns the original Drug (with chembl_found=False) if nothing is
        found or if the API is unreachable.
        """
        name = drug.normalized_name or drug.input_name.strip().lower()

        molecule = self._find_molecule(name)
        if not molecule:
            logger.debug("ChEMBL: no molecule found for %r", name)
            return drug

        chembl_id = molecule.get("molecule_chembl_id", "")
        logger.info("ChEMBL: matched %r → %s", name, chembl_id)

        # Brand names from synonyms
        brand_names = [
            s["molecule_synonym"]
            for s in molecule.get("molecule_synonyms", [])
            if s.get("syn_type") == "TRADE_NAME"
        ]

        # Molecule type & approval status
        molecule_type   = molecule.get("molecule_type", "")
        max_phase_raw   = molecule.get("max_phase")
        approval_status = _PHASE_TO_STATUS.get(
            int(float(max_phase_raw)) if max_phase_raw is not None else -99, ""
        )

        # ATC-based drug class (first code, level-3 description)
        drug_class = self._get_atc_drug_class(
            molecule.get("atc_classifications", [])
        )

        # Mechanisms → MoA, target IDs
        mechanisms      = self._get_mechanisms(chembl_id)
        moa             = self._extract_moa(mechanisms)
        target_ids      = self._extract_target_ids(mechanisms)

        # Resolve target names (batch where possible)
        molecular_targets = self._get_target_names(target_ids)

        # Approved indications
        approved_indications = self._get_indications(chembl_id)

        result = Drug(
            input_name=drug.input_name,
            normalized_name=name,
            chembl_id=chembl_id,
            brand_names=brand_names,
            moa=moa,
            drug_class=drug_class,
            molecular_targets=molecular_targets,
            target_chembl_ids=target_ids,
            molecule_type=molecule_type,
            approval_status=approval_status,
            approved_indications=approved_indications,
            lookup_timestamp=drug.lookup_timestamp,
            chembl_found=True,
        )
        return result

    # ── Molecule lookup ───────────────────────────────────────────────────────

    def _find_molecule(self, name: str) -> Optional[dict]:
        """Try exact pref_name first, then synonym search."""
        # 1. Exact preferred name
        data = self._get(
            "/molecule",
            {"pref_name__iexact": name, "limit": 1},
        )
        molecules = (data or {}).get("molecules", [])
        if molecules:
            return molecules[0]

        # 2. Synonym / free-text search
        self._sleep()
        data = self._get("/molecule/search", {"q": name, "limit": 5})
        molecules = (data or {}).get("molecules", [])
        if not molecules:
            return None

        # Prefer exact pref_name match in results, else take first
        name_lower = name.lower()
        for mol in molecules:
            if (mol.get("pref_name") or "").lower() == name_lower:
                return mol
        return molecules[0]

    # ── Mechanisms ────────────────────────────────────────────────────────────

    def _get_mechanisms(self, chembl_id: str) -> list[dict]:
        """Fetch mechanisms — tries parent_molecule_chembl_id first (broader),
        falls back to molecule_chembl_id (exact match)."""
        self._sleep()
        data = self._get(
            "/mechanism",
            {"parent_molecule_chembl_id": chembl_id, "limit": 50},
        )
        mechs = (data or {}).get("mechanisms", [])
        if mechs:
            return mechs
        # Fallback: exact molecule match (catches salt forms returned directly)
        self._sleep()
        data = self._get("/mechanism", {"molecule_chembl_id": chembl_id, "limit": 50})
        return (data or {}).get("mechanisms", [])

    @staticmethod
    def _extract_moa(mechanisms: list[dict]) -> str:
        """Return the most descriptive MoA string from mechanism records."""
        seen: list[str] = []
        for m in mechanisms:
            moa = (m.get("mechanism_of_action") or "").strip()
            if moa and moa not in seen:
                seen.append(moa)
        return "; ".join(seen)

    @staticmethod
    def _extract_target_ids(mechanisms: list[dict]) -> list[str]:
        seen: list[str] = []
        for m in mechanisms:
            tid = m.get("target_chembl_id", "")
            if tid and tid not in seen:
                seen.append(tid)
        return seen

    # ── Target name resolution ────────────────────────────────────────────────

    def _get_target_names(self, target_ids: list[str]) -> list[str]:
        if not target_ids:
            return []

        ids_to_fetch = target_ids[:_MAX_TARGET_LOOKUPS]

        # Batch request using __in filter
        self._sleep()
        data = self._get(
            "/target",
            {
                "target_chembl_id__in": ",".join(ids_to_fetch),
                "limit": _MAX_TARGET_LOOKUPS,
            },
        )
        targets = (data or {}).get("targets", [])

        # Build id→name map then return in original order
        id_to_name = {
            t["target_chembl_id"]: t.get("pref_name", t["target_chembl_id"])
            for t in targets
            if "target_chembl_id" in t
        }
        return [id_to_name.get(tid, tid) for tid in ids_to_fetch]

    # ── Indications ───────────────────────────────────────────────────────────

    def _get_indications(self, chembl_id: str) -> list[str]:
        self._sleep()
        data = self._get(
            "/drug_indication",
            {"molecule_chembl_id": chembl_id, "limit": 50},
        )
        indications = (data or {}).get("drug_indications", [])
        seen: list[str] = []
        for ind in indications:
            mesh = (ind.get("mesh_heading") or "").strip()
            if mesh and mesh not in seen:
                seen.append(mesh)
        return seen

    # ── ATC drug class ────────────────────────────────────────────────────────

    def _get_atc_drug_class(self, atc_codes: list[str]) -> str:
        """Return the level-3 ATC description for the first ATC code."""
        if not atc_codes:
            return ""
        code = atc_codes[0]
        self._sleep()
        data = self._get(f"/atc_class/{code}", {})
        if not data:
            return ""
        # Single object returned (not a list)
        return data.get("level3_description", "")

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict) -> Optional[dict]:
        """GET *path* with *params*; return parsed JSON or None on error."""
        url = f"{CHEMBL_BASE_URL}{path}"
        params = {**params, "format": "json"}
        try:
            resp = self.session.get(url, params=params, timeout=CHEMBL_REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            logger.warning("ChEMBL request failed (%s): %s", url, exc)
            return None

        if resp.status_code == 404:
            return None
        if not resp.ok:
            logger.warning("ChEMBL HTTP %d for %s", resp.status_code, url)
            return None

        try:
            return resp.json()
        except ValueError:
            logger.warning("ChEMBL non-JSON response from %s", url)
            return None

    def _sleep(self) -> None:
        time.sleep(CHEMBL_RATE_LIMIT_DELAY)

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "Accept": "application/json",
            "User-Agent": "ClinTrialScrape/1.0 (research tool)",
        })
        retry = Retry(
            total=3,
            backoff_factor=2.0,
            status_forcelist={500, 502, 503, 504},
            allowed_methods={"GET"},
            raise_on_status=False,
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session
