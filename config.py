
from zoneinfo import ZoneInfo


UK = ZoneInfo("Europe/London")

SENDER_REPO = "lukeoreillykp/Kp-to-campeys-sscc-conversion"
SAVED_LOADS_API = (
    f"https://api.github.com/repos/{SENDER_REPO}/contents/saved_loads"
)

BLUE = "#2878c8"
GREEN = "#239653"
RED = "#d64545"
PURPLE = "#7b61a8"
AMBER = "#e0a126"
DHL_COLOUR = "#159eaa"

SOURCE_COLOURS = {
    "Campey": BLUE,
    "Bulk": AMBER,
    "Manual": PURPLE,
    "DHL": DHL_COLOUR,
}

SOURCE_LABELS = {
    "Campey": "Campey",
    "Bulk": "Bulk Inbound",
    "Manual": "Manual",
    "DHL": "DHL Collection",
}

STATUS_OPTIONS = ["scheduled", "complete", "cancelled"]
