"""
بک‌آپ و بازگردانی دیتابیس.

بک‌آپ: از SQLite Connection.backup() استفاده می‌کنیم که مخصوص همین کاره -
یه snapshot امن و سازگار از دیتابیس می‌گیره حتی وقتی کانکشن اصلی بازه و
داره ازش استفاده میشه (برخلاف کپی خام فایل که می‌تونه وسط یه write گیر
کنه و فایل خراب تحویل بده).

بازگردانی: چون sqlite3.Connection یه‌بار در db.py باز شده و همه ماژول‌های
دیگه (subs.py و غیره) مستقیم به همون شیء conn/cur وصلن، جایگزین کردن فایل
دیتابیس زیر پای یه کانکشن بازِ در حال استفاده ریسکیه. برای همین، به‌جای
hot-swap کردن کانکشن، این‌کارها رو می‌کنیم: قبلش یه نسخه امن از دیتابیس
فعلی نگه می‌داریم، کانکشن رو می‌بندیم، فایل رو جایگزین می‌کنیم، و بعد
عمدا با کد خروج غیرصفر از برنامه خارج میشیم تا Railway (یا هر process
manager دیگه با سیاست ری‌استارت خودکار) خودش از نو بالا بیاره - این یعنی
بعد از بازگردانی، ربات چند ثانیه‌ای ری‌استارت میشه، که طبیعیه.
"""

import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

import db


def create_backup_file():
    """یه فایل بک‌آپ امن و سازگار از دیتابیس زنده می‌سازه و مسیرش رو برمی‌گردونه."""
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
                caption=f"🗄 بک‌آپ دیتابیس Berserk VPN — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
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
    """چک می‌کنه فایل آپلودشده واقعا یه دیتابیس معتبر و سازگار با ساختار ماست."""
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
    """
    دیتابیس فعلی رو با فایل آپلودشده جایگزین می‌کنه.
    قبلش یه نسخه امن از دیتابیس فعلی نگه می‌داره (برای برگشت اضطراری اگه
    فایل اشتباهی آپلود شده باشه) و مسیر اون نسخه امن رو برمی‌گردونه.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safety_path = os.path.join(
        os.path.dirname(os.path.abspath(db.DB_PATH)), f"berserk_pre_restore_{ts}.db"
    )
    shutil.copy(db.DB_PATH, safety_path)

    db.conn.commit()
    db.conn.close()

    shutil.copy(uploaded_path, db.DB_PATH)
    return safety_path
