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


# مدیریت کامل لینک‌ها برای پنل ادمین

def link_counts():
    cur.execute("SELECT COUNT(*) AS c FROM subs")
    total = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=0")
    available = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=1")
    delivered = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE status='disabled'")
    disabled = cur.fetchone()["c"]

    return {
        "total": total,
        "available": available,
        "delivered": delivered,
        "disabled": disabled,
    }


def list_links(kind="all", limit=15, offset=0):
    where = ""
    params = []

    if kind == "available":
        where = "WHERE used=0"
    elif kind == "delivered":
        where = "WHERE used=1"
    elif kind == "disabled":
        where = "WHERE status='disabled'"

    sql = f"""
        SELECT id, link, used, owner, added_at, assigned_at, price_paid, account_name, status, purchase_id
        FROM subs
        {where}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """

    params.extend([int(limit), int(offset)])
    cur.execute(sql, params)
    return cur.fetchall()


def search_links(query, limit=15):
    q = (query or "").strip()

    if not q:
        return []

    like = f"%{q}%"

    if q.isdigit():
        cur.execute(
            """
            SELECT id, link, used, owner, added_at, assigned_at, price_paid, account_name, status, purchase_id
            FROM subs
            WHERE id=? OR owner=? OR link LIKE ? OR account_name LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(q), q, like, like, int(limit)),
        )
    else:
        cur.execute(
            """
            SELECT id, link, used, owner, added_at, assigned_at, price_paid, account_name, status, purchase_id
            FROM subs
            WHERE link LIKE ? OR account_name LIKE ? OR owner LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (like, like, like, int(limit)),
        )

    return cur.fetchall()


def delete_available_link(link_id):
    """
    فقط لینک آزاد حذف می‌شود.
    لینک تحویل‌شده حذف نمی‌شود چون سابقه خرید و دیتیل کاربر خراب می‌شود.
    """
    link_id = int(link_id)

    cur.execute("SELECT id, used FROM subs WHERE id=?", (link_id,))
    row = cur.fetchone()

    if not row:
        return False, "not_found"

    if int(row["used"] or 0) == 1:
        return False, "already_delivered"

    cur.execute("DELETE FROM subs WHERE id=? AND used=0", (link_id,))
    conn.commit()

    if cur.rowcount == 1:
        return True, "deleted"

    return False, "not_deleted"


def get_link_detail(link_id):
    cur.execute(
        """
        SELECT id, link, used, owner, added_at, assigned_at, price_paid, account_name, status, purchase_id
        FROM subs
        WHERE id=?
        """,
        (int(link_id),),
    )
    return cur.fetchone()
