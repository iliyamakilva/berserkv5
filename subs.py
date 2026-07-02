import db
from db import conn, cur


def _generate_account_name():
    return db.generate_service_code()


def get_sub():
    cur.execute(
        "SELECT id, link, account_name FROM subs WHERE used=0 ORDER BY id LIMIT 1"
    )
    return cur.fetchone()


def get_sub_by_id(sub_id):
    cur.execute(
        """
        SELECT id, link, used, owner, added_at, assigned_at, price_paid,
               account_name, status, purchase_id
        FROM subs WHERE id=?
        """,
        (int(sub_id),),
    )
    return cur.fetchone()


def assign_sub(sub_id, user_id, price_paid=None):
    cur.execute("SELECT account_name FROM subs WHERE id=?", (sub_id,))
    row = cur.fetchone()
    account_name = row["account_name"] if row and row["account_name"] else _generate_account_name()
    cur.execute(
        """
        UPDATE subs
        SET used=1,
            owner=?,
            assigned_at=datetime('now'),
            price_paid=?,
            account_name=?,
            status='delivered'
        WHERE id=? AND used=0
        """,
        (str(user_id), price_paid, account_name, sub_id),
    )
    conn.commit()
    db.bump_daily("sales")


def add_sub(link):
    link = link.strip()
    if not link:
        return None
    cur.execute("SELECT id FROM subs WHERE link=?", (link,))
    existing = cur.fetchone()
    if existing:
        return existing["id"]
    cur.execute(
        "INSERT INTO subs(link, account_name, status) VALUES (?, ?, 'available')",
        (link, _generate_account_name()),
    )
    conn.commit()
    db.set_low_stock_alerted(False)
    return cur.lastrowid


def add_subs_bulk(links):
    count = 0
    for link in links:
        link = link.strip()
        if not link:
            continue
        cur.execute("SELECT 1 FROM subs WHERE link=?", (link,))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO subs(link, account_name, status) VALUES (?, ?, 'available')",
            (link, _generate_account_name()),
        )
        count += 1
    conn.commit()
    if count:
        db.set_low_stock_alerted(False)
    return count


def stock_count():
    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=0")
    return cur.fetchone()["c"]


def sold_count():
    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=1")
    return cur.fetchone()["c"]


def user_subs(user_id, limit=50):
    cur.execute(
        """
        SELECT id, link, account_name, assigned_at, price_paid, status, purchase_id
        FROM subs
        WHERE owner=?
        ORDER BY assigned_at DESC, id DESC
        LIMIT ?
        """,
        (str(user_id), limit),
    )
    return cur.fetchall()


def short_link(link: str, limit=48):
    if not link:
        return "-"
    return link if len(link) <= limit else link[:limit] + "..."
