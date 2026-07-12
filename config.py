"""Environment configuration for the bot.

Only environment parsing belongs here. Runtime-editable settings are stored in
SQLite and exposed through :mod:`settings`.
"""

from __future__ import annotations

import os


def _get_int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Hidden command used instead of /admin.
ADMIN_COMMAND = os.getenv("ADMIN_COMMAND", "panel_secret").strip().lstrip("/")

# ADMIN_ID can contain one or more comma-separated Telegram IDs.
_admin_ids_raw = os.getenv("ADMIN_ID", "")
ADMIN_IDS = {
    int(part)
    for part in _admin_ids_raw.replace(" ", "").split(",")
    if part.strip().isdigit()
}

# Sensitive actions such as restore are restricted to OWNER_ID when provided.
# For backward compatibility, all admins are treated as owners when it is absent.
OWNER_ID = _get_int("OWNER_ID")
OWNER_IDS = {OWNER_ID} if OWNER_ID else set(ADMIN_IDS)

REF_REWARD = _get_int("REF_REWARD", 30_000) or 30_000

# Railway Volume example: DB_PATH=/data/berserk.db
DB_PATH = os.getenv("DB_PATH", "berserk.db").strip() or "berserk.db"

MAX_TOTAL_REFERRALS = max(0, _get_int("MAX_TOTAL_REFERRALS", 50) or 0)
MAX_REFERRALS_PER_DAY = max(0, _get_int("MAX_REFERRALS_PER_DAY", 10) or 0)

# Broadcast pacing and automatic backup controls.
BROADCAST_DELAY = max(0.0, _get_float("BROADCAST_DELAY", 0.08))
BACKUP_INTERVAL_SECONDS = max(3600, _get_int("BACKUP_INTERVAL_SECONDS", 24 * 60 * 60) or 0)
BACKUP_RETENTION_COUNT = max(1, _get_int("BACKUP_RETENTION_COUNT", 30) or 30)

# Optional YouPanel integration. Credentials must be configured only as
# environment variables; access tokens are acquired at runtime and never
# persisted in SQLite or log output.
YOUPANEL_BASE_URL = os.getenv("YOUPANEL_BASE_URL", "").strip().rstrip("/")
YOUPANEL_USERNAME = os.getenv("YOUPANEL_USERNAME", "").strip()
YOUPANEL_PASSWORD = os.getenv("YOUPANEL_PASSWORD", "")
YOUPANEL_TIMEOUT_SECONDS = max(5, _get_int("YOUPANEL_TIMEOUT_SECONDS", 20) or 20)
YOUPANEL_VERIFY_SSL = _get_bool("YOUPANEL_VERIFY_SSL", True)
YOUPANEL_INBOUNDS_JSON = os.getenv(
    "YOUPANEL_INBOUNDS_JSON",
    '{"vless":["RTL-1","VLESS + WS","tcp","TUN"]}',
).strip()
# Trial catalog item is provider-agnostic. Generic TRIAL_* variables take
# precedence; legacy YOUPANEL_TRIAL_* names remain valid for compatibility.
TRIAL_PROVIDER_KEY = os.getenv("TRIAL_PROVIDER_KEY", "youpanel").strip().lower() or "youpanel"
TRIAL_ENABLED = _get_bool("TRIAL_ENABLED", _get_bool("YOUPANEL_TRIAL_ENABLED", True))
TRIAL_SIZE_MB = max(1, _get_int("TRIAL_SIZE_MB", _get_int("YOUPANEL_TRIAL_SIZE_MB", 200)) or 200)
TRIAL_DAYS = max(1, _get_int("TRIAL_DAYS", _get_int("YOUPANEL_TRIAL_DAYS", 1)) or 1)
TRIAL_MAX_DEVICES = max(1, _get_int("TRIAL_MAX_DEVICES", _get_int("YOUPANEL_TRIAL_MAX_DEVICES", 1)) or 1)

# Backward-compatible aliases used by older deployments and modules.
YOUPANEL_TRIAL_ENABLED = TRIAL_ENABLED
YOUPANEL_TRIAL_SIZE_MB = TRIAL_SIZE_MB
YOUPANEL_TRIAL_DAYS = TRIAL_DAYS
YOUPANEL_TRIAL_MAX_DEVICES = TRIAL_MAX_DEVICES


def youpanel_configured() -> bool:
    return bool(YOUPANEL_BASE_URL and YOUPANEL_USERNAME and YOUPANEL_PASSWORD)


def validate() -> None:
    """Fail early with a clear error when required variables are missing."""
    missing: list[str] = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_IDS:
        missing.append("ADMIN_ID")

    if missing:
        raise SystemExit(
            "متغیر(های) محیطی الزامی تنظیم نشده: " + ", ".join(missing)
        )
