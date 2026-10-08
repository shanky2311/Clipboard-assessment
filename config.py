"""Settings. You can change any of these with environment variables or a .env file."""
import os
from pathlib import Path

ROOT = Path(__file__).parent


def _load_dotenv():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

BASE_URL = os.environ.get("BH_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
API_URL = BASE_URL + "/api/v1"
TOKEN = os.environ.get("BH_TOKEN", "")
DB_PATH = os.environ.get("BH_DB", str(ROOT / "state" / "sync.db"))
SNAPSHOT_DIR = Path(os.environ.get("BH_SNAPSHOTS", str(ROOT / "state" / "snapshots")))

# Leave blank and it finds the Bellhaven parent account by name.
BELLHAVEN_PARENT_ID = os.environ.get("BELLHAVEN_PARENT_ID", "")
BELLHAVEN_PARENT_NAME = "Bellhaven Senior Living"

# The CHOW rule is for a facility moving from one owner to another.
# If an account has no parent at all, I fix it directly instead.
# Set this to true if you want the CHOW check there too.
CHOW_APPLIES_TO_UNPARENTED = os.environ.get("CHOW_APPLIES_TO_UNPARENTED", "false").lower() == "true"

# Optional. A Slack (or any) incoming webhook URL. The pipeline posts here when a run stops.
ALERT_WEBHOOK = os.environ.get("BH_ALERT_WEBHOOK", "")

# Field names in the CRM. If the API calls something by another name, change it here.
F = {
    "id": "account_id",
    "name": "name",
    "parent_id": "parent_id",
    "street": "billing_street",
    "city": "billing_city",
    "state": "billing_state",
    "zip": "billing_zip",
    "care_type": "care_type",
    "status": "status",
    "note": "note",
    "lifetime_revenue": "lifetime_revenue",
    "outstanding_ar": "outstanding_ar",
    "duplicate_of": "duplicate_of_account",
    "chow_current": "chow_current_account",
}

# website care offering words, and the CRM care type they mean
CARE_MAP = [
    ("memory", "Memory Care"),
    ("rehab", "Skilled Nursing"),
    ("nursing", "Skilled Nursing"),
    ("assisted", "Assisted Living"),
    ("independent", "Independent Living"),
]
