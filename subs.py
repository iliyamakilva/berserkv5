"""
مدیریت استخر ساب‌لینک‌ها.

Patch mode:
- فرمت فایل درست شد.
- اختصاص لینک همچنان یک‌بار مصرف است.
- duplicate link هنگام افزودن باعث crash نمی‌شود.
"""

import random

import db
from db import bump_daily, conn, cur


def _generate_account_name():
    """
    یک اسم Berserk-XXXXX یکتا می‌سازد.
    """
    for _ in range(20):
        candidate = f"Berserk-{random.randint(10000, 99999)}"
        cur.execute("SELECT 1 FROM subs WHERE account_name=?", (candidate,))
        if cur.fetchone() is None:
            return candidate

    return f"Berserk-{random.randint(100000, 999999)}"


def get_sub():
    """
    قدیمی‌ترین لینک استفاده‌نشده را برمی‌گرداند.
    """
    cur.execute("SELECT id, link, account_name FROM subs WHERE used=0 ORDER BY id LIMIT 1")
    return cur.fetchone()


def assign_sub(sub_id, user_id, price_paid=None):
    """
    یک ساب را به مالک مشخص اختصاص می‌دهد.
    """
    cur.execute(
        "UPDATE subs SET used=1, owner=?, assigned_at=datetime('now'), price_paid=? "
        "WHERE id=? AND used=0",
        (str(user_id), price_paid, sub_id),
    )
    conn.commit()

    if cur.rowcount:
        bump_daily("sales")
        return True

    return False


def add_sub(link):
    link = (link or "").strip()

    if not link:
        return None

    cur.execute("SELECT id FROM subs WHERE link=?", (link,))
    existing = cur.fetchone()

    if existing:
        return None

    account_name = _generate_account_name()

    cur.execute(
        "INSERT INTO subs(link, account_name) VALUES (?, ?)",
        (link, account_name),
    )
    conn.commit()
    db.set_low_stock_alerted(False)

    return cur.lastrowid


def add_subs_bulk(links):
    added = 0

    for raw_link in links:
        if add_sub(raw_link):
            added += 1

    return added


def stock_count():
    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=0")
    return cur.fetchone()["c"]


def sold_count():
    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=1")
    return cur.fetchone()["c"]


def user_subs(user_id):
    cur.execute(
        "SELECT id, link, account_name, assigned_at, price_paid FROM subs WHERE owner=? "
        "ORDER BY assigned_at DESC",
        (str(user_id),),
    )
    return cur.fetchall()
