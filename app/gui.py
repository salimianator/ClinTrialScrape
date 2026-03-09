# app/gui.py — Gradio desktop GUI

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime

import gradio as gr
import pandas as pd

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scraper.clinicaltrials import ClinicalTrialsClient, ClinicalTrialsError
from enrichment.pipeline import EnrichmentPipeline
from models.trial import EXCEL_FIELDS as TRIAL_EXCEL_FIELDS
from models.drug import EXCEL_FIELDS as DRUG_EXCEL_FIELDS

logger = logging.getLogger(__name__)

# ── Filter options ────────────────────────────────────────────────────────────

STATUS_OPTIONS = [
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "ENROLLING_BY_INVITATION",
    "COMPLETED",
    "TERMINATED",
    "WITHDRAWN",
    "SUSPENDED",
]

PHASE_OPTIONS = [
    "EARLY_PHASE1",
    "PHASE1",
    "PHASE2",
    "PHASE3",
    "PHASE4",
    "NA",
]

# Columns shown in the on-screen preview table (subset of all Excel fields)
PREVIEW_COLUMNS = [
    "nct_id",
    "brief_title",
    "status",
    "phase",
    "conditions",
    "intervention_name",
    "drug_name_normalized",
    "moa",
    "drug_class",
    "enrollment",
    "sponsor",
    "start_date",
    "completion_date",
]


# ── Core search + enrich logic ────────────────────────────────────────────────

def run_search(
    condition: str,
    intervention: str,
    sponsor: str,
    query_term: str,
    status: list[str],
    phase: list[str],
    max_results: int,
    enrich: bool,
    progress=gr.Progress(),
) -> tuple:
    """Execute search and optional enrichment; return updated UI components."""

    # Validate — at least one search field required
    if not any([condition, intervention, sponsor, query_term]):
        return (
            _empty_df(),
            [],       # drugs state
            [],       # trials state
            gr.update(value="Please fill in at least one search field.", visible=True),
            gr.update(interactive=False),
        )

    trials_state = []
    drugs_state  = []
    status_msg   = ""

    try:
        # ── Phase 1: Scrape CT.gov ────────────────────────────────────────────
        progress(0.0, desc="Connecting to ClinicalTrials.gov…")
        client = ClinicalTrialsClient()

        fetched_so_far = [0]
        total_expected = [1]

        def ct_progress(fetched: int, total: int) -> None:
            fetched_so_far[0] = fetched
            total_expected[0] = total
            frac = min(fetched / max(total, 1), 1.0) * 0.45
            progress(frac, desc=f"Fetching trials… {fetched} / {total}")

        trials = client.search(
            condition=condition or None,
            intervention=intervention or None,
            sponsor=sponsor or None,
            query_term=query_term or None,
            status=status or None,
            phase=phase or None,
            max_results=int(max_results),
            progress_callback=ct_progress,
        )

        if not trials:
            return (
                _empty_df(),
                [],
                [],
                gr.update(value="No trials found. Try broadening your search.", visible=True),
                gr.update(interactive=False),
            )

        # ── Phase 2: Enrich (optional) ────────────────────────────────────────
        if enrich:
            progress(0.45, desc=f"Enriching drug data for {len(trials)} trial(s)…")
            pipeline = EnrichmentPipeline()
            enriched_count = [0]

            unique_names = list({
                t.intervention_name.strip().lower()
                for t in trials
                if t.intervention_name.strip()
            })
            total_unique = max(len(unique_names), 1)

            def enrich_progress(done: int, total: int) -> None:
                enriched_count[0] = done
                frac = 0.45 + (done / max(total, 1)) * 0.50
                progress(frac, desc=f"Enriching drugs… {done} / {total}")

            trials, drugs = pipeline.enrich_trials(
                trials, progress_callback=enrich_progress
            )
            drugs_state = [d.__dict__ for d in drugs]
        else:
            drugs_state = []

        progress(1.0, desc="Done!")
        trials_state = [t.__dict__ for t in trials]

        n   = len(trials)
        src = "ClinicalTrials.gov"
        enr = f" · {len(drugs_state)} drug(s) enriched" if enrich else " · Enrichment skipped"
        status_msg = f"Found **{n}** trial(s) from {src}{enr}"

        return (
            _trials_to_preview_df(trials),
            drugs_state,
            trials_state,
            gr.update(value=status_msg, visible=True),
            gr.update(interactive=True),
        )

    except ClinicalTrialsError as exc:
        logger.error("CT.gov error: %s", exc)
        return (
            _empty_df(),
            [],
            [],
            gr.update(value=f"ClinicalTrials.gov error: {exc}", visible=True),
            gr.update(interactive=False),
        )
    except Exception as exc:
        logger.exception("Unexpected error during search")
        return (
            _empty_df(),
            [],
            [],
            gr.update(value=f"Unexpected error: {exc}", visible=True),
            gr.update(interactive=False),
        )


def export_results(trials_state: list, drugs_state: list) -> str | None:
    """Write results to a timestamped Excel file and return the file path."""
    if not trials_state:
        return None

    os.makedirs("exports", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("exports", f"clinical_trials_{timestamp}.xlsx")

    trials_rows = [
        {f: _serialize(row.get(f)) for f in TRIAL_EXCEL_FIELDS}
        for row in trials_state
    ]
    drugs_rows = [
        {f: _serialize(row.get(f)) for f in DRUG_EXCEL_FIELDS}
        for row in drugs_state
    ] if drugs_state else []

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(trials_rows).to_excel(
            writer, sheet_name="Trial Results", index=False
        )
        if drugs_rows:
            pd.DataFrame(drugs_rows).to_excel(
                writer, sheet_name="Drug Enrichment", index=False
            )

    return path


# ── UI helpers ────────────────────────────────────────────────────────────────

def _serialize(val) -> str:
    """Flatten lists to semicolon strings for Excel cells."""
    if isinstance(val, list):
        return "; ".join(str(v) for v in val)
    if val is None:
        return ""
    return val


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=PREVIEW_COLUMNS)


def _trials_to_preview_df(trials) -> pd.DataFrame:
    rows = []
    for t in trials:
        row = {}
        for col in PREVIEW_COLUMNS:
            val = getattr(t, col, "")
            row[col] = _serialize(val)
        rows.append(row)
    return pd.DataFrame(rows, columns=PREVIEW_COLUMNS)


# ── Layout ────────────────────────────────────────────────────────────────────

def launch_app() -> None:
    with gr.Blocks(
        title="Clinical Trials Scraper",
        theme=gr.themes.Soft(),
        css=".status-box { font-size: 14px; }",
    ) as app:

        # ── Header ────────────────────────────────────────────────────────────
        gr.Markdown("# Clinical Trials Scraper")
        gr.Markdown(
            "Search ClinicalTrials.gov and enrich drug data via ChEMBL and OpenFDA. "
            "Fill in one or more fields below, then click **Search**."
        )

        # ── State ─────────────────────────────────────────────────────────────
        trials_state = gr.State([])
        drugs_state  = gr.State([])

        # ── Search panel ──────────────────────────────────────────────────────
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### Search Parameters")

                condition    = gr.Textbox(label="Condition / Disease",    placeholder="e.g. breast cancer")
                intervention = gr.Textbox(label="Intervention / Drug",    placeholder="e.g. pembrolizumab")
                sponsor      = gr.Textbox(label="Sponsor",                placeholder="e.g. Pfizer")
                query_term   = gr.Textbox(label="Free-text search",       placeholder="any keyword across all fields")

                with gr.Row():
                    status_filter = gr.CheckboxGroup(
                        label="Trial Status",
                        choices=STATUS_OPTIONS,
                        value=["RECRUITING"],
                    )
                    phase_filter = gr.CheckboxGroup(
                        label="Phase",
                        choices=PHASE_OPTIONS,
                        value=[],
                    )

                with gr.Row():
                    max_results = gr.Slider(
                        label="Max results",
                        minimum=10, maximum=2000, step=10, value=200,
                    )
                    enrich_toggle = gr.Checkbox(
                        label="Enrich drug data (ChEMBL + OpenFDA)",
                        value=True,
                    )

                search_btn = gr.Button("Search", variant="primary", size="lg")

            # ── Results panel ─────────────────────────────────────────────────
            with gr.Column(scale=3):
                gr.Markdown("### Results")
                status_box = gr.Markdown(value="", visible=False, elem_classes="status-box")
                results_table = gr.Dataframe(
                    value=_empty_df(),
                    headers=PREVIEW_COLUMNS,
                    datatype=["str"] * len(PREVIEW_COLUMNS),
                    interactive=False,
                    wrap=False,
                    label=None,
                )

        # ── Export panel ──────────────────────────────────────────────────────
        gr.Markdown("---")
        with gr.Row():
            export_btn = gr.Button(
                "Export to Excel", variant="secondary", interactive=False
            )
            export_file = gr.File(label="Download", visible=False)

        # ── Wire up events ────────────────────────────────────────────────────
        search_btn.click(
            fn=run_search,
            inputs=[
                condition, intervention, sponsor, query_term,
                status_filter, phase_filter,
                max_results, enrich_toggle,
            ],
            outputs=[
                results_table,
                drugs_state,
                trials_state,
                status_box,
                export_btn,
            ],
        )

        export_btn.click(
            fn=export_results,
            inputs=[trials_state, drugs_state],
            outputs=[export_file],
        ).then(
            fn=lambda path: gr.update(value=path, visible=True) if path else gr.update(visible=False),
            inputs=[export_file],
            outputs=[export_file],
        )

    app.launch(inbrowser=True)
