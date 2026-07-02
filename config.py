import os


def _get_int(name, default=None):
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_COMMAND = os.getenv("ADMIN_COMMAND", "panel_secret").strip().lstrip("/")

_admin_ids_raw = os.getenv("ADMIN_ID", "")
ADMIN_IDS = {
    int(x)
    for x in _admin_ids_raw.replace(" ", "").split(",")
    if x.strip().isdigit()
}

REF_REWARD = _get_int("REF_REWARD", 30000)
MAX_TOTAL_REFERRALS = _get_int("MAX_TOTAL_REFERRALS", 50)
MAX_REFERRALS_PER_DAY = _get_int("MAX_REFERRALS_PER_DAY", 10)


def validate():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_IDS:
        missing.append("ADMIN_ID")
    if missing:
        raise SystemExit(
            "متغیر(های) محیطی الزامی تنظیم نشده: " + ", ".join(missing)
        )
