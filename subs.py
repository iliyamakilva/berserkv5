"""
مدیریت استخر ساب‌لینک‌ها: افزودن، تخصیص، و موجودی.
هر لینک فقط یکبار تخصیص داده میشه (used=1, owner=user_id) و دیگه
هیچ‌وقت به کسی دیگه داده نمیشه. assigned_at هم تاریخچه مالکیت رو نگه می‌داره.
"""

from db import bump_daily, conn, cur
import db


def get_sub():
    """قدیمی‌ترین لینک استفاده‌نشده رو برمی‌گردونه (بدون رزرو کردنش)."""
    cur.execute("SELECT id, link FROM subs WHERE used=0 ORDER BY id LIMIT 1")
    return cur.fetchone()


def assign_sub(sub_id, user_id, price_paid=None):
    cur.execute(
        "UPDATE subs SET used=1, owner=?, assigned_at=datetime('now'), price_paid=? WHERE id=?",
        (str(user_id), price_paid, sub_id),
    )
    conn.commit()
    bump_daily("sales")


def add_sub(link):
    link = link.strip()
    cur.execute("INSERT INTO subs(link) VALUES (?)", (link,))
    conn.commit()
    db.set_low_stock_alerted(False)
    return cur.lastrowid


def add_subs_bulk(links):
    clean = [l.strip() for l in links if l.strip()]
    cur.executemany("INSERT INTO subs(link) VALUES (?)", [(l,) for l in clean])
    conn.commit()
    if clean:
        db.set_low_stock_alerted(False)
    return len(clean)


def stock_count():
    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=0")
    return cur.fetchone()["c"]


def sold_count():
    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=1")
    return cur.fetchone()["c"]


def user_subs(user_id):
    """تاریخچه ساب‌لینک‌هایی که این کاربر مالکشونه."""
    cur.execute(
        "SELECT id, link, assigned_at FROM subs WHERE owner=? ORDER BY assigned_at DESC",
        (str(user_id),),
    )
    return cur.fetchall()
