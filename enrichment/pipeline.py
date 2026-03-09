# enrichment/pipeline.py — Drug enrichment orchestration

from __future__ import annotations

import logging
from typing import Callable, Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from enrichment.chembl import ChEMBLClient
from enrichment.openfda import OpenFDAClient
from models.drug import Drug
from models.trial import Trial
from config import ENRICHMENT_CACHE_ENABLED

logger = logging.getLogger(__name__)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _split_names(intervention_name: str) -> list[str]:
    """Split a semicolon-joined intervention_name into individual drug names."""
    return [n.strip() for n in intervention_name.split(";") if n.strip()]


def _append_unique(lst: list[str], value: str) -> None:
    """Append *value* to *lst* only if it is non-empty and not already present."""
    if value and value not in lst:
        lst.append(value)


class EnrichmentPipeline:
    """Orchestrates ChEMBL → OpenFDA enrichment with an in-memory cache.

    Usage::

        pipeline = EnrichmentPipeline()
        trials, drugs = pipeline.enrich_trials(trials)
    """

    def __init__(self) -> None:
        self._chembl  = ChEMBLClient()
        self._openfda = OpenFDAClient()
        self._cache: dict[str, Drug] = {}   # keyed by normalized_name

    # ── Public API ────────────────────────────────────────────────────────────

    def enrich_drug(self, name: str) -> Drug:
        """Enrich a single drug name through the full pipeline.

        Pipeline order:
          1. Normalize name
          2. Return from cache if available
          3. ChEMBL lookup
          4. OpenFDA lookup (fills any gaps left by ChEMBL)
          5. Cache and return
        """
        normalized = name.strip().lower()

        if ENRICHMENT_CACHE_ENABLED and normalized in self._cache:
            logger.debug("Cache hit for %r", normalized)
            return self._cache[normalized]

        drug = Drug.from_input_name(name)

        # ── Step 1: ChEMBL ────────────────────────────────────────────────────
        try:
            chembl_result = self._chembl.enrich(drug)
        except Exception as exc:
            logger.warning("ChEMBL enrichment failed for %r: %s", name, exc)
            chembl_result = drug

        # ── Step 2: OpenFDA (always run to fill gaps) ─────────────────────────
        try:
            openfda_result = self._openfda.enrich(drug)
        except Exception as exc:
            logger.warning("OpenFDA enrichment failed for %r: %s", name, exc)
            openfda_result = drug

        # ── Step 3: Merge — ChEMBL is primary, OpenFDA fills empty fields ─────
        chembl_result.merge(openfda_result)
        result = chembl_result

        if ENRICHMENT_CACHE_ENABLED:
            self._cache[normalized] = result

        found_sources = []
        if result.chembl_found:
            found_sources.append("ChEMBL")
        if result.openfda_found:
            found_sources.append("OpenFDA")
        logger.info(
            "Enriched %r — sources: %s",
            name,
            ", ".join(found_sources) if found_sources else "none",
        )
        return result

    def enrich_trials(
        self,
        trials: list[Trial],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> tuple[list[Trial], list[Drug]]:
        """Enrich all trials in-place and return (updated_trials, drug_list).

        Deduplicates by normalized intervention name so each unique drug is
        only looked up once.  Updates the enrichment fields on each Trial.

        Args:
            trials:            List of Trial objects from the CT.gov scraper.
            progress_callback: Optional callable(enriched_count, total_unique)
                               called after each unique drug is processed.

        Returns:
            Tuple of (trials_with_enrichment, unique_drug_list).
        """
        # Collect unique individual drug names (intervention_name may be
        # semicolon-joined when a trial has multiple drugs)
        unique_names: list[str] = []
        seen_normalized: set[str] = set()
        for trial in trials:
            for name in _split_names(trial.intervention_name):
                norm = name.lower()
                if norm not in seen_normalized:
                    unique_names.append(name)
                    seen_normalized.add(norm)

        total = len(unique_names)
        logger.info("Enriching %d unique drug(s)", total)

        drug_map: dict[str, Drug] = {}   # normalized_name → Drug

        for i, name in enumerate(unique_names, start=1):
            drug = self.enrich_drug(name)
            drug_map[name.strip().lower()] = drug
            if progress_callback:
                progress_callback(i, total)

        # Update each Trial's enrichment fields, aggregating across all
        # drugs listed in intervention_name (which may be semicolon-joined).
        #
        # Positional scalar fields (normalized_name, moa, drug_class) always
        # emit exactly one entry per drug — even an empty string — so their
        # index always aligns with intervention_name after joining with "; ".
        #
        # True list fields (molecular_targets, approved_indications) collect
        # unique values from all drugs with no positional constraint.
        for trial in trials:
            names = _split_names(trial.intervention_name)
            if not names:
                continue

            agg_normalized:  list[str] = []  # positional — one per drug
            agg_moa:         list[str] = []  # positional — one per drug
            agg_class:       list[str] = []  # positional — one per drug
            agg_targets:     list[str] = []  # aggregated unique list
            agg_indications: list[str] = []  # aggregated unique list
            agg_sources:     list[str] = []  # aggregated unique list

            for name in names:
                drug = drug_map.get(name.lower())
                if drug:
                    # Positional: always append one value (empty string if missing)
                    agg_normalized.append(drug.normalized_name or name.lower())
                    agg_moa.append(drug.moa or "")
                    agg_class.append(drug.drug_class or "")
                    # Aggregated lists: deduplicate across all drugs in trial
                    for t in drug.molecular_targets:
                        _append_unique(agg_targets, t)
                    for ind in drug.approved_indications:
                        _append_unique(agg_indications, ind)
                    if drug.chembl_found:
                        _append_unique(agg_sources, "chembl")
                    if drug.openfda_found:
                        _append_unique(agg_sources, "openfda")
                else:
                    # Drug not enriched — keep position with empty placeholders
                    agg_normalized.append(name.lower())
                    agg_moa.append("")
                    agg_class.append("")

            trial.drug_name_normalized = "; ".join(agg_normalized)
            trial.moa                  = "; ".join(agg_moa)
            trial.drug_class           = "; ".join(agg_class)
            trial.molecular_targets    = agg_targets
            trial.approved_indications = agg_indications
            trial.match_method         = "+".join(agg_sources) if agg_sources else "none"

        return trials, list(drug_map.values())

    def clear_cache(self) -> None:
        """Discard all cached enrichment results."""
        self._cache.clear()
        logger.debug("Enrichment cache cleared")
