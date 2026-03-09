# Clinical Trials Scraper

A Python desktop application for scraping, enriching, and exporting clinical trials data from ClinicalTrials.gov.

## Overview

Designed for consultants with no coding background. Provides a simple GUI to search ClinicalTrials.gov, enrich drug data via ChEMBL and OpenFDA, and export results to Excel.

## Data Sources

| Source | Purpose |
|---|---|
| [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api) | Primary trial data |
| [ChEMBL API](https://www.ebi.ac.uk/chembl/api/data/docs) | MoA, drug class, molecular targets |
| [OpenFDA API](https://open.fda.gov/apis/) | Indications, brand/generic names, fills ChEMBL gaps |

## Project Structure

```
clinical-trials-scraper/
├── app/
│   └── gui.py              # Gradio desktop GUI
├── scraper/
│   └── clinicaltrials.py   # CT.gov API client
├── enrichment/
│   ├── chembl.py           # ChEMBL API client
│   └── openfda.py          # OpenFDA API client
├── models/
│   ├── trial.py            # Trial data model
│   └── drug.py             # Drug enrichment model
├── output/
│   └── exporter.py         # Excel/CSV export
├── main.py                 # Entry point
├── config.py               # Configuration
└── requirements.txt
```

## Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate   # macOS/Linux
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

## Output

The exported Excel file contains two tabs:
- **Trial Results** — one row per clinical trial
- **Drug Enrichment** — one row per unique drug with enrichment data

## Configuration

Edit `config.py` to:
- Add an optional OpenFDA API key (increases rate limit from 240 to 1000 req/min)
- Adjust timeouts and rate limiting delays
