"""
مدیریت استخر ساب‌لینک‌ها.

قانون اصلی:
- هر لینک فقط یک بار به یک کاربر اختصاص داده می‌شود.
- شناسه سرویس Berserk برای هر لینک نگهداری می‌شود.
"""

import db
from db import conn, cur


def _generate_account_name():
    return db.generate_service_code()


def get_sub():
    cur.execute(
        """
        SELECT id, link, account_name, status, price_paid, assigned_at, owner, purchase_id
        FROM subs
        WHERE used=0
        ORDER BY id
        LIMIT 1
        """
    )
    return cur.fetchone()


def get_sub_detail(sub_id):
    cur.execute(
        """
        SELECT id, link, account_name, status, price_paid, assigned_at, owner, purchase_id, used
        FROM subs
        WHERE id=?
        """,
        (int(sub_id),),
    )
    return cur.fetchone()


def assign_sub(sub_id, user_id, price_paid=None):
    account_name = _generate_account_name()
    cur.execute(
        """
        UPDATE subs
        SET used=1,
            owner=?,
            assigned_at=datetime('now'),
            price_paid=?,
            account_name=COALESCE(NULLIF(account_name, ''), ?),
            status='delivered'
        WHERE id=? AND used=0
        """,
        (str(user_id), price_paid, account_name, int(sub_id)),
    )
    conn.commit()

    if cur.rowcount:
        db.bump_daily("sales")
        return True
    return False


def add_sub(link):
    link = (link or "").strip()
    if not link:
        return None

    cur.execute("SELECT id FROM subs WHERE link=?", (link,))
    if cur.fetchone():
        return None

    account_name = _generate_account_name()
    cur.execute(
        """
        INSERT INTO subs(link, account_name, status)
        VALUES (?, ?, 'available')
        """,
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


def user_subs(user_id, limit=None):
    sql = """
        SELECT id, link, account_name, assigned_at, price_paid, status, purchase_id, used
        FROM subs
        WHERE owner=?
        ORDER BY assigned_at DESC, id DESC
    """
    params = [str(user_id)]

    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    cur.execute(sql, params)
    return cur.fetchall()


def short_link(link, size=34):
    link = link or ""
    if len(link) <= size:
        return link
    return link[:size] + "..."
