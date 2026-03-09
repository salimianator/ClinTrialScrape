# scraper/clinicaltrials.py — ClinicalTrials.gov API v2 client

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import (
    CTGOV_BASE_URL,
    CTGOV_PAGE_SIZE,
    CTGOV_REQUEST_TIMEOUT,
)
from models.trial import Trial

logger = logging.getLogger(__name__)

# CT.gov v2 valid values (used for parameter validation)
VALID_STATUSES = {
    "RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING", "COMPLETED", "SUSPENDED",
    "TERMINATED", "WITHDRAWN", "UNKNOWN",
}
VALID_PHASES = {
    "NA", "EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4",
}

# Hard cap so a single search can never run forever
MAX_RESULTS_LIMIT = 10_000


class ClinicalTrialsError(Exception):
    """Raised when the CT.gov API returns an unrecoverable error."""


class ClinicalTrialsClient:
    """Client for the ClinicalTrials.gov REST API v2.

    Usage::

        client = ClinicalTrialsClient()
        trials = client.search(
            condition="breast cancer",
            intervention="pembrolizumab",
            status=["RECRUITING"],
            phase=["PHASE2", "PHASE3"],
            max_results=200,
        )
    """

    def __init__(self) -> None:
        self.session = self._build_session()

    # ── Public API ────────────────────────────────────────────────────────────

    def search(
        self,
        *,
        query_term: Optional[str] = None,
        condition: Optional[str] = None,
        intervention: Optional[str] = None,
        sponsor: Optional[str] = None,
        status: Optional[list[str]] = None,
        phase: Optional[list[str]] = None,
        max_results: int = 500,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> list[Trial]:
        """Search ClinicalTrials.gov and return a list of Trial objects.

        Args:
            query_term:        Free-text search across all fields.
            condition:         Disease / condition (maps to query.cond).
            intervention:      Drug / intervention name (maps to query.intr).
            sponsor:           Sponsor name (maps to query.spons).
            status:            List of overall statuses to include.
            phase:             List of trial phases to include.
            max_results:       Maximum number of trials to return (≤ 10 000).
            progress_callback: Optional callable(fetched, total) for GUI updates.

        Returns:
            List of Trial objects populated from CT.gov data.
        """
        max_results = min(max_results, MAX_RESULTS_LIMIT)

        params = self._build_params(
            query_term=query_term,
            condition=condition,
            intervention=intervention,
            sponsor=sponsor,
            status=status,
            phase=phase,
        )

        trials: list[Trial] = []
        page_token: Optional[str] = None
        total_count: Optional[int] = None

        while True:
            remaining = max_results - len(trials)
            if remaining <= 0:
                break

            page_params = {
                **params,
                "pageSize": min(CTGOV_PAGE_SIZE, remaining),
            }
            if page_token:
                page_params["pageToken"] = page_token

            data = self._get_page(page_params)

            if total_count is None:
                total_count = data.get("totalCount", 0)
                logger.info("CT.gov reports %d total matching studies", total_count)

            studies = data.get("studies", [])
            for raw in studies:
                trials.append(Trial.from_ctgov(raw))

            if progress_callback and total_count:
                progress_callback(len(trials), min(total_count, max_results))

            page_token = data.get("nextPageToken")
            if not page_token or not studies:
                break

        logger.info("Fetched %d trials", len(trials))
        return trials

    def get_trial(self, nct_id: str) -> Optional[Trial]:
        """Fetch a single trial by NCT ID.

        Returns None if the trial is not found.
        """
        url = f"{CTGOV_BASE_URL}/studies/{nct_id}"
        try:
            resp = self._request(url, {})
            return Trial.from_ctgov(resp.json())
        except ClinicalTrialsError as exc:
            if "404" in str(exc):
                logger.warning("Trial %s not found", nct_id)
                return None
            raise

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_params(
        query_term, condition, intervention, sponsor,
        status, phase,
    ) -> dict:
        """Assemble the base query-string parameters for the studies endpoint."""
        params: dict = {}

        if query_term:
            params["query.term"] = query_term
        if condition:
            params["query.cond"] = condition
        if intervention:
            params["query.intr"] = intervention
        if sponsor:
            params["query.spons"] = sponsor

        if status:
            invalid = set(status) - VALID_STATUSES
            if invalid:
                raise ValueError(f"Invalid status value(s): {invalid}")
            params["filter.overallStatus"] = ",".join(status)

        if phase:
            invalid = set(phase) - VALID_PHASES
            if invalid:
                raise ValueError(f"Invalid phase value(s): {invalid}")
            params["filter.phase"] = ",".join(phase)

        return params

    def _get_page(self, params: dict) -> dict:
        """Fetch one page from the /studies endpoint and return parsed JSON."""
        url = f"{CTGOV_BASE_URL}/studies"
        resp = self._request(url, params)
        return resp.json()

    def _request(self, url: str, params: dict) -> requests.Response:
        """Execute a GET request with retry/backoff and error handling."""
        try:
            resp = self.session.get(
                url,
                params=params,
                timeout=CTGOV_REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            raise ClinicalTrialsError(
                f"Request timed out after {CTGOV_REQUEST_TIMEOUT}s: {url}"
            )
        except requests.exceptions.ConnectionError as exc:
            raise ClinicalTrialsError(f"Connection error: {exc}") from exc

        if resp.status_code == 404:
            raise ClinicalTrialsError(f"404 Not Found: {url}")
        if resp.status_code == 429:
            # Shouldn't happen with urllib3 Retry, but handle just in case
            retry_after = int(resp.headers.get("Retry-After", 60))
            logger.warning("Rate limited — waiting %ds", retry_after)
            time.sleep(retry_after)
            return self._request(url, params)
        if not resp.ok:
            raise ClinicalTrialsError(
                f"HTTP {resp.status_code} from CT.gov: {resp.text[:200]}"
            )

        return resp

    @staticmethod
    def _build_session() -> requests.Session:
        """Build a requests Session with automatic retry on transient errors."""
        session = requests.Session()
        session.headers.update({
            "Accept": "application/json",
            "User-Agent": "ClinTrialScrape/1.0 (research tool)",
        })

        retry = Retry(
            total=4,
            backoff_factor=1.5,       # waits: 0s, 1.5s, 3s, 6s
            status_forcelist={500, 502, 503, 504},
            allowed_methods={"GET"},
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
