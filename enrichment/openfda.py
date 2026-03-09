# enrichment/openfda.py — OpenFDA API client

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
    OPENFDA_BASE_URL,
    OPENFDA_API_KEY,
    OPENFDA_RATE_LIMIT_DELAY,
    OPENFDA_REQUEST_TIMEOUT,
)
from models.drug import Drug

logger = logging.getLogger(__name__)

# Max characters to keep from free-text label sections
_MAX_TEXT_LEN = 500


class OpenFDAError(Exception):
    """Raised on unrecoverable OpenFDA API errors."""


class OpenFDAClient:
    """Client for the OpenFDA Drug Label and DrugsFDA APIs.

    Populates: openfda_id, brand_names, drug_class, approved_indications,
    administration_route, and fills moa if ChEMBL didn't provide it.
    """

    def __init__(self) -> None:
        self.session = self._build_session()

    # ── Public API ────────────────────────────────────────────────────────────

    def enrich(self, drug: Drug) -> Drug:
        """Look up *drug* by normalised name and return an enriched copy.

        Returns the original Drug (with openfda_found=False) if nothing is
        found or if the API is unreachable.
        """
        name = drug.normalized_name or drug.input_name.strip().lower()

        label = self._find_label(name)
        if not label:
            logger.debug("OpenFDA: no label found for %r", name)
            return drug

        openfda  = label.get("openfda", {})
        logger.info("OpenFDA: matched %r", name)

        # IDs
        app_numbers = openfda.get("application_number", [])
        openfda_id  = app_numbers[0] if app_numbers else ""

        # Names
        brand_names = list({
            b for b in openfda.get("brand_name", [])
        })

        # Drug class from pharmacological class
        pharm_classes = openfda.get("pharm_class_epc", [])   # Established Pharmacologic Class
        if not pharm_classes:
            pharm_classes = openfda.get("pharm_class_moa", [])
        drug_class = pharm_classes[0] if pharm_classes else ""

        # MoA from label section
        moa = self._extract_text(label, "mechanism_of_action")

        # Indications
        indications_raw = self._extract_text(label, "indications_and_usage")
        approved_indications = (
            [indications_raw] if indications_raw else []
        )

        # Route
        routes = openfda.get("route", [])
        administration_route = "; ".join(r.lower() for r in routes)

        result = Drug(
            input_name=drug.input_name,
            normalized_name=name,
            openfda_id=openfda_id,
            brand_names=brand_names,
            drug_class=drug_class,
            moa=moa,
            approved_indications=approved_indications,
            administration_route=administration_route,
            lookup_timestamp=drug.lookup_timestamp,
            openfda_found=True,
        )
        return result

    # ── Label lookup ──────────────────────────────────────────────────────────

    def _find_label(self, name: str) -> Optional[dict]:
        """Search drug labels — generic name first, then brand name."""
        self._sleep()

        # Try generic name
        result = self._label_search(f'openfda.generic_name:"{name}"')
        if result:
            return result

        self._sleep()
        # Try brand name
        result = self._label_search(f'openfda.brand_name:"{name}"')
        if result:
            return result

        self._sleep()
        # Broader substance name search
        result = self._label_search(f'openfda.substance_name:"{name}"')
        return result

    def _label_search(self, query: str) -> Optional[dict]:
        params: dict = {"search": query, "limit": 1}
        if OPENFDA_API_KEY:
            params["api_key"] = OPENFDA_API_KEY

        data = self._get(f"{OPENFDA_BASE_URL}/label.json", params)
        if not data:
            return None

        results = data.get("results", [])
        return results[0] if results else None

    # ── Text extraction ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(label: dict, field: str) -> str:
        """Return first non-empty entry of a label text array, truncated."""
        entries = label.get(field, [])
        for entry in entries:
            text = entry.strip()
            if text:
                return text[:_MAX_TEXT_LEN]
        return ""

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict) -> Optional[dict]:
        """GET *url* with *params*; return parsed JSON or None on error."""
        try:
            resp = self.session.get(url, params=params, timeout=OPENFDA_REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            logger.warning("OpenFDA request failed (%s): %s", url, exc)
            return None

        if resp.status_code == 404:
            # OpenFDA returns 404 when the search has no results
            return None
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            logger.warning("OpenFDA rate limited — waiting %ds", retry_after)
            time.sleep(retry_after)
            return self._get(url, params)
        if not resp.ok:
            logger.warning("OpenFDA HTTP %d for %s", resp.status_code, url)
            return None

        try:
            return resp.json()
        except ValueError:
            logger.warning("OpenFDA non-JSON response from %s", url)
            return None

    def _sleep(self) -> None:
        time.sleep(OPENFDA_RATE_LIMIT_DELAY)

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "Accept": "application/json",
            "User-Agent": "ClinTrialScrape/1.0 (research tool)",
        })
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist={500, 502, 503, 504},
            allowed_methods={"GET"},
            raise_on_status=False,
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session
