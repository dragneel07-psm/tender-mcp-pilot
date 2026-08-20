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

USER_AGENT = "SudurpashchimTenderMonitor/0.1 (company pilot; contact: admin@example.com)"
TENDER_WORDS = ("tender", "bid", "bidding", "procurement", "bolpatra", "बोलपत्र", "दरभाउ", "खरिद", "आशय")
PROVINCES = {"1": "Koshi", "2": "Madhesh", "3": "Bagmati", "4": "Gandaki", "5": "Lumbini", "6": "Karnali", "7": "Sudurpashchim"}


def load_dotenv():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_dotenv()
