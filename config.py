# config.py — Application-wide configuration

# ── CT.gov ───────────────────────────────────────────────────────────────────
CTGOV_BASE_URL = "https://clinicaltrials.gov/api/v2"
CTGOV_PAGE_SIZE = 100          # max records per page
CTGOV_REQUEST_TIMEOUT = 30     # seconds

# ── ChEMBL ───────────────────────────────────────────────────────────────────
CHEMBL_BASE_URL = "https://www.ebi.ac.uk/chembl/api/data"
CHEMBL_RATE_LIMIT_DELAY = 1.0  # seconds between requests (recommended 1 req/s)
CHEMBL_REQUEST_TIMEOUT = 30

# ── OpenFDA ──────────────────────────────────────────────────────────────────
OPENFDA_BASE_URL = "https://api.fda.gov/drug"
OPENFDA_API_KEY = ""           # optional — leave empty to use unauthenticated (240 req/min)
OPENFDA_RATE_LIMIT_DELAY = 0.25  # seconds between requests (safe for 240/min)
OPENFDA_REQUEST_TIMEOUT = 30

# ── Enrichment cache ─────────────────────────────────────────────────────────
ENRICHMENT_CACHE_ENABLED = True

# ── Export ───────────────────────────────────────────────────────────────────
DEFAULT_OUTPUT_DIR = "exports"
EXCEL_TAB_TRIALS = "Trial Results"
EXCEL_TAB_DRUGS = "Drug Enrichment"
