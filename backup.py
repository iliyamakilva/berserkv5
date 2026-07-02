import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
import db


def create_backup_file():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(tempfile.gettempdir(), f"berserk_backup_{ts}.db")
    dest = sqlite3.connect(path)
    try:
        db.conn.backup(dest)
    finally:
        dest.close()
    return path


async def send_backup(bot, chat_id):
    path = create_backup_file()
    try:
        with open(path, "rb") as f:
            await bot.send_document(
                chat_id,
                f,
                caption=f"💾 بک‌آپ دیتابیس Berserk VPN — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def send_backup_to_all_admins(bot, admin_ids):
    for admin_id in admin_ids:
        try:
            await send_backup(bot, admin_id)
        except Exception:
            pass


async def daily_backup_loop(bot, admin_ids, interval_seconds=24 * 60 * 60):
    import asyncio

    while True:
        await asyncio.sleep(interval_seconds)
        await send_backup_to_all_admins(bot, admin_ids)


def validate_sqlite_file(path):
    try:
        test_conn = sqlite3.connect(path)
        test_cur = test_conn.cursor()
        test_cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in test_cur.fetchall()}
        test_conn.close()
        required = {"users", "subs"}
        return required.issubset(tables)
    except Exception:
        return False


def perform_restore(uploaded_path):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safety_path = os.path.join(
        os.path.dirname(os.path.abspath(db.DB_PATH)),
        f"berserk_pre_restore_{ts}.db",
    )
    shutil.copy(db.DB_PATH, safety_path)
    db.conn.commit()
    db.conn.close()
    shutil.copy(uploaded_path, db.DB_PATH)
    return safety_path
