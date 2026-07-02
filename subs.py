"""
مدیریت استخر ساب‌لینک‌ها: افزودن، تخصیص، و موجودی.
هر لینک فقط یکبار تخصیص داده میشه (used=1, owner=user_id) و دیگه
هیچ‌وقت به کسی دیگه داده نمیشه. assigned_at هم تاریخچه مالکیت رو نگه می‌داره.

هر لینک، همون لحظه‌ای که اضافه میشه، یه اسم اکانت رندوم و یکتا به فرمت
Berserk-XXXXX می‌گیره؛ این اسم برای مدیریت راحت‌تر حساب‌ها (مثلا سمت
تامین‌کننده) استفاده میشه و بعد از خرید هم به مشتری نشون داده میشه.
"""

import random

from db import bump_daily, conn, cur
import db


def _generate_account_name():
    """یه اسم Berserk-XXXXX یکتا می‌سازه (تلاش مجدد در صورت برخورد نادر)."""
    for _ in range(20):
        candidate = f"Berserk-{random.randint(10000, 99999)}"
        cur.execute("SELECT 1 FROM subs WHERE account_name=?", (candidate,))
        if cur.fetchone() is None:
            return candidate
    # اگه بعد از ۲۰ تلاش هم برخورد داشتیم (خیلی بعیده)، یه رنج بزرگتر امتحان کن
    return f"Berserk-{random.randint(100000, 999999)}"


def get_sub():
    """قدیمی‌ترین لینک استفاده‌نشده رو برمی‌گردونه (بدون رزرو کردنش)."""
    cur.execute("SELECT id, link, account_name FROM subs WHERE used=0 ORDER BY id LIMIT 1")
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
    account_name = _generate_account_name()
    cur.execute(
        "INSERT INTO subs(link, account_name) VALUES (?, ?)", (link, account_name)
    )
    conn.commit()
    db.set_low_stock_alerted(False)
    return cur.lastrowid


def add_subs_bulk(links):
    clean = [l.strip() for l in links if l.strip()]
    for link in clean:
        account_name = _generate_account_name()
        cur.execute(
            "INSERT INTO subs(link, account_name) VALUES (?, ?)", (link, account_name)
        )
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
    """تاریخچه ساب‌لینک‌هایی که این کاربر مالکشونه (برای دکمه «اشتراک‌های من»)."""
    cur.execute(
        "SELECT id, link, account_name, assigned_at FROM subs WHERE owner=? "
        "ORDER BY assigned_at DESC",
        (str(user_id),),
    )
    return cur.fetchall()
