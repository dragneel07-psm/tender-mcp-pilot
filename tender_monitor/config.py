"""Paths, environment loading, and constants shared across the package."""
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "tenders.db"
SOURCES = DATA_DIR / "sources.json"
if not SOURCES.exists() and (ROOT / "sources.json").exists():
    SOURCES.write_text((ROOT / "sources.json").read_text())
WATCHLISTS = DATA_DIR / "watchlists.json"
if not WATCHLISTS.exists() and (ROOT / "watchlists.json").exists():
    WATCHLISTS.write_text((ROOT / "watchlists.json").read_text())
# Milestone 5: company profiles, same bootstrap-copy-once pattern as sources/watchlists.
COMPANY_PROFILES = DATA_DIR / "company_profiles.json"
if not COMPANY_PROFILES.exists() and (ROOT / "company_profiles.json").exists():
    COMPANY_PROFILES.write_text((ROOT / "company_profiles.json").read_text())
# Milestone 13: which provinces the dashboard's "Local governments" section shows by default --
# same bootstrap-copy-once pattern. Lets a province be dropped from view without deleting its
# sources, and brought back later, instead of bulk add/remove across dozens of source records.
DASHBOARD_SETTINGS = DATA_DIR / "dashboard_settings.json"
if not DASHBOARD_SETTINGS.exists() and (ROOT / "dashboard_settings.json").exists():
    DASHBOARD_SETTINGS.write_text((ROOT / "dashboard_settings.json").read_text())

USER_AGENT = "SudurpashchimTenderMonitor/0.1 (company pilot; contact: admin@example.com)"
TENDER_WORDS = ("tender", "bid", "bidding", "procurement", "bolpatra", "बोलपत्र", "दरभाउ", "खरिद", "आशय")
PROVINCES = {"1": "Koshi", "2": "Madhesh", "3": "Bagmati", "4": "Gandaki", "5": "Lumbini", "6": "Karnali", "7": "Sudurpashchim"}
# The full set of province labels a source can carry, including the non-geographic bucket for
# national-level sources (e.g. Jobs Nepal, Bolpatra) -- matches dashboard.html's own PROVINCES
# list and storage.validate_source's default. Not every one of these has a source today; this is
# the vocabulary dashboard_settings.enabled_provinces is validated against.
ALL_PROVINCES = tuple(PROVINCES.values()) + ("National / other",)


def load_dotenv():
    """Milestone 12: hardened slightly against audit §13's ".env parser has no quoting support"
    gap -- a value wrapped in matching quotes (as many providers' dashboards paste API keys, e.g.
    `ANTHROPIC_API_KEY="sk-..."`) now has the quotes stripped rather than becoming part of the
    value. Splitting on the first "=" only (already the case) already handled a value containing
    "=" correctly. Still no backslash-escape or multi-line-value support -- a genuine limitation,
    not fabricated as fixed; values needing that stay out of scope for this hand-rolled parser
    rather than growing it into a full dotenv reimplementation."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


load_dotenv()
