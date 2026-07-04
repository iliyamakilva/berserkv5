import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import db
from config import DB_PATH

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups")).expanduser()
REQUIRED_SCHEMA = {
    "users": {"id", "balance", "purchased", "banned"},
    "subs": {"id", "link", "used", "owner"},
    "settings": {"key", "value"},
    "topups": {"id", "user_id", "amount", "status"},
    "ledger": {"id", "user_id", "action", "amount"},
}


def _safe_int(value):
    try:
        return int(value or 0)
    except Exception:
        return 0


def _query_count(cur, sql):
    try:
        cur.execute(sql)
        row = cur.fetchone()
        return _safe_int(row[0] if row else 0)
    except Exception:
        return 0


def backup_file_name(prefix="backup", info=None):
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    suffix = ""
    if info:
        suffix = f"_users-{info.get('users', 0)}_subs-{info.get('subs', 0)}"
    return f"berserk_{prefix}_{ts}{suffix}.db"


def inspect_sqlite_file(path):
    result = {
        "path": path,
        "file_name": os.path.basename(path),
        "file_size": os.path.getsize(path) if os.path.exists(path) else 0,
        "ok": False,
        "integrity_ok": False,
        "errors": [],
        "warnings": [],
        "tables": [],
        "counts": {},
    }

    if not os.path.exists(path):
        result["errors"].append("فایل وجود ندارد.")
        return result

    if result["file_size"] <= 0:
        result["errors"].append("فایل خالی است.")
        return result

    conn = None
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()

        cur.execute("PRAGMA integrity_check")
        integrity = cur.fetchone()
        if integrity and str(integrity[0]).lower() == "ok":
            result["integrity_ok"] = True
        else:
            result["errors"].append(f"integrity_check ناموفق: {integrity[0] if integrity else '-'}")

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        result["tables"] = sorted(tables)

        for table, required_columns in REQUIRED_SCHEMA.items():
            if table not in tables:
                result["errors"].append(f"جدول ضروری {table} وجود ندارد.")
                continue
            cur.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in cur.fetchall()}
            missing = required_columns - columns
            if missing:
                result["errors"].append(f"ستون‌های ضروری جدول {table} ناقص است: {', '.join(sorted(missing))}")

        result["counts"] = {
            "users": _query_count(cur, "SELECT COUNT(*) FROM users"),
            "subs": _query_count(cur, "SELECT COUNT(*) FROM subs"),
            "subs_available": _query_count(cur, "SELECT COUNT(*) FROM subs WHERE used=0"),
            "subs_sold": _query_count(cur, "SELECT COUNT(*) FROM subs WHERE used=1"),
            "topups": _query_count(cur, "SELECT COUNT(*) FROM topups"),
            "purchases": _query_count(cur, "SELECT COUNT(*) FROM purchases"),
            "ledger": _query_count(cur, "SELECT COUNT(*) FROM ledger"),
            "custom_buttons": _query_count(cur, "SELECT COUNT(*) FROM custom_buttons"),
        }

        result["ok"] = result["integrity_ok"] and not result["errors"]
        return result
    except sqlite3.DatabaseError as exc:
        result["errors"].append(f"فایل SQLite معتبر نیست: {exc}")
        return result
    except Exception as exc:
        result["errors"].append(f"خطا در بررسی بک‌آپ: {exc}")
        return result
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def format_backup_info(info):
    status = "✅ سالم" if info.get("ok") else "❌ ناسالم / مشکوک"
    counts = info.get("counts") or {}
    lines = [
        "📦 اطلاعات فایل بک‌آپ",
        "",
        f"نام فایل: {info.get('file_name', '-')}",
        f"حجم فایل: {int(info.get('file_size') or 0):,} بایت",
        f"وضعیت سلامت: {status}",
        f"integrity_check: {'OK' if info.get('integrity_ok') else 'Failed'}",
        "",
        "📊 آمار داخل فایل:",
        f"کاربران: {counts.get('users', 0)}",
        f"کل سرویس‌ها: {counts.get('subs', 0)}",
        f"سرویس‌های آزاد: {counts.get('subs_available', 0)}",
        f"سرویس‌های فروخته‌شده: {counts.get('subs_sold', 0)}",
        f"شارژها: {counts.get('topups', 0)}",
        f"خریدها: {counts.get('purchases', 0)}",
        f"تراکنش‌های کیف پول: {counts.get('ledger', 0)}",
        f"دکمه‌های اختصاصی: {counts.get('custom_buttons', 0)}",
    ]

    if info.get("errors"):
        lines.append("\n❌ خطاها:")
        lines.extend(f"• {err}" for err in info["errors"])

    if info.get("warnings"):
        lines.append("\n⚠️ هشدارها:")
        lines.extend(f"• {w}" for w in info["warnings"])

    return "\n".join(lines)


def create_backup_file(prefix="backup", admin_id=None, note=""):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # ابتدا یک بک‌آپ موقت می‌سازیم، بعد بر اساس آمار نام‌گذاری می‌کنیم.
    tmp_path = BACKUP_DIR / backup_file_name(prefix)
    dest = sqlite3.connect(str(tmp_path))
    try:
        with db.LOCK:
            db.conn.backup(dest)
    finally:
        dest.close()

    info = inspect_sqlite_file(str(tmp_path))
    final_path = BACKUP_DIR / backup_file_name(prefix, info.get("counts") or {})
    if final_path != tmp_path:
        try:
            tmp_path.rename(final_path)
        except OSError:
            final_path = tmp_path

    try:
        db.log_backup_operation(
            admin_id,
            f"create_{prefix}",
            final_path.name,
            os.path.getsize(final_path),
            "ok" if info.get("ok") else "warning",
            note or "; ".join(info.get("errors") or []),
        )
    except Exception:
        pass

    return str(final_path)


async def send_backup(bot, chat_id, admin_id=None):
    path = create_backup_file(admin_id=admin_id or chat_id)
    info = inspect_sqlite_file(path)
    try:
        with open(path, "rb") as f:
            await bot.send_document(
                chat_id,
                f,
                caption=(
                    f"💾 بک‌آپ دیتابیس Berserk VPN\n"
                    f"زمان: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                    f"وضعیت: {'سالم' if info.get('ok') else 'نیازمند بررسی'}\n"
                    f"کاربران: {info.get('counts', {}).get('users', 0)} | سرویس‌ها: {info.get('counts', {}).get('subs', 0)}"
                ),
            )
    finally:
        # فایل در پوشه backups هم نگهداری می‌شود؛ حذف نمی‌کنیم تا لیست بک‌آپ‌های محلی قابل استفاده باشد.
        pass
    return path


async def send_backup_to_all_admins(bot, admin_ids):
    for admin_id in admin_ids:
        try:
            await send_backup(bot, admin_id, admin_id=admin_id)
        except Exception:
            pass


async def daily_backup_loop(bot, admin_ids, interval_seconds=24 * 60 * 60):
    import asyncio

    while True:
        await asyncio.sleep(interval_seconds)
        await send_backup_to_all_admins(bot, admin_ids)


def validate_sqlite_file(path):
    return inspect_sqlite_file(path).get("ok", False)


def list_local_backups(limit=10):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[: int(limit)]


def perform_restore(uploaded_path, admin_id=None):
    info = inspect_sqlite_file(uploaded_path)
    if not info.get("ok"):
        raise ValueError("backup file failed validation")

    safety_path = create_backup_file(prefix="pre_restore", admin_id=admin_id, note="automatic safety backup before restore")

    with db.LOCK:
        db.conn.commit()
        db.conn.close()
        shutil.copy(uploaded_path, DB_PATH)

    try:
        db.log_backup_operation(
            admin_id,
            "restore",
            os.path.basename(uploaded_path),
            os.path.getsize(uploaded_path),
            "ok",
            f"safety_backup={os.path.basename(safety_path)}",
        )
    except Exception:
        pass

    return safety_path
