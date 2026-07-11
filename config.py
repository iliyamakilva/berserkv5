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
