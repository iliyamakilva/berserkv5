"""
لایه دیتابیس SQLite.
از sqlite3.Row استفاده می‌کنیم تا به جای ایندکس عددی (row[3])
با اسم ستون (row["balance"]) کار کنیم - خواناتر و کم‌خطاتر.
"""

import sqlite3
from datetime import date

DB_PATH = "berserk.db"

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()


def init():
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY,
            username TEXT DEFAULT '',
            ref TEXT,
            balance INTEGER DEFAULT 0,
            purchased INTEGER DEFAULT 0,
            rewarded INTEGER DEFAULT 0,
            rewarded_at TEXT,
            banned INTEGER DEFAULT 0,
            joined_at TEXT DEFAULT (datetime('now')),
            last_active TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            owner TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            assigned_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats(
            day TEXT PRIMARY KEY,
            new_users INTEGER DEFAULT 0,
            sales INTEGER DEFAULT 0,
            referral_rewards INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT 'awaiting_receipt',
            created_at TEXT DEFAULT (datetime('now')),
            reviewed_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receipts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_unique_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            topup_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticket_messages(
            admin_id TEXT,
            message_id INTEGER,
            ticket_id INTEGER,
            user_id TEXT,
            PRIMARY KEY (admin_id, message_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages(
            key TEXT PRIMARY KEY,
            text TEXT,
            photo_file_id TEXT
        )
    """)
    # ستون اضافه برای ثبت قیمتی که واقعا پرداخت شده (اگه بعدا قیمت پلن عوض
    # بشه، تاریخچه فروش‌های قدیمی درست می‌مونه)
    cur.execute("PRAGMA table_info(subs)")
    existing_cols = {row["name"] for row in cur.fetchall()}
    if "price_paid" not in existing_cols:
        cur.execute("ALTER TABLE subs ADD COLUMN price_paid INTEGER")
    if "account_name" not in existing_cols:
        cur.execute("ALTER TABLE subs ADD COLUMN account_name TEXT")
    conn.commit()


# ---------------------------------------------------------------------------
# کاربران
# ---------------------------------------------------------------------------

def get_user(user_id):
    cur.execute("SELECT * FROM users WHERE id=?", (str(user_id),))
    return cur.fetchone()


def get_or_create_user(user_id, username=None, ref=None):
    """اگه کاربر وجود نداشت می‌سازدش (با کد رفرال در صورت معتبر بودن)."""
    user_id = str(user_id)
    row = get_user(user_id)
    if row:
        return row, False

    if ref is not None:
        ref = str(ref)
        # خود-معرفی یا رفرر ناموجود رو نادیده می‌گیریم
        if ref == user_id or not get_user(ref):
            ref = None

    cur.execute(
        "INSERT INTO users(id, username, ref) VALUES (?, ?, ?)",
        (user_id, username or "", ref),
    )
    conn.commit()
    bump_daily("new_users")
    return get_user(user_id), True


def touch_active(user_id, username=None):
    if username is not None:
        cur.execute(
            "UPDATE users SET username=?, last_active=datetime('now') WHERE id=?",
            (username, str(user_id)),
        )
    else:
        cur.execute(
            "UPDATE users SET last_active=datetime('now') WHERE id=?", (str(user_id),)
        )
    conn.commit()


def add_balance(user_id, amount):
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, str(user_id)))
    conn.commit()


def set_ban(user_id, banned: bool):
    cur.execute("UPDATE users SET banned=? WHERE id=?", (1 if banned else 0, str(user_id)))
    conn.commit()


def mark_rewarded(referred_id):
    cur.execute(
        "UPDATE users SET rewarded=1, rewarded_at=datetime('now') WHERE id=?",
        (str(referred_id),),
    )
    conn.commit()


def increment_purchased(user_id):
    cur.execute("UPDATE users SET purchased = purchased + 1 WHERE id=?", (str(user_id),))
    conn.commit()


def search_users(query, limit=10):
    q = f"%{query.strip()}%"
    cur.execute(
        "SELECT * FROM users WHERE id LIKE ? OR username LIKE ? "
        "ORDER BY joined_at DESC LIMIT ?",
        (q, q, limit),
    )
    return cur.fetchall()


def list_users(offset=0, limit=10):
    cur.execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", (limit, offset)
    )
    return cur.fetchall()


def count_users():
    cur.execute("SELECT COUNT(*) AS c FROM users")
    return cur.fetchone()["c"]


def referral_count(user_id):
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE ref=?", (str(user_id),))
    return cur.fetchone()["c"]


def referrals_rewarded_today(ref_id):
    cur.execute(
        "SELECT COUNT(*) AS c FROM users "
        "WHERE ref=? AND rewarded=1 AND date(rewarded_at)=date('now')",
        (str(ref_id),),
    )
    return cur.fetchone()["c"]


def referred_users(user_id, limit=20):
    cur.execute(
        "SELECT id, username, purchased, rewarded FROM users WHERE ref=? LIMIT ?",
        (str(user_id), limit),
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# آمار روزانه
# ---------------------------------------------------------------------------

_VALID_STAT_FIELDS = {"new_users", "sales", "referral_rewards"}


def bump_daily(field, amount=1):
    if field not in _VALID_STAT_FIELDS:
        raise ValueError(f"unknown stat field: {field}")
    today = date.today().isoformat()
    cur.execute("INSERT OR IGNORE INTO daily_stats(day) VALUES (?)", (today,))
    cur.execute(
        f"UPDATE daily_stats SET {field} = {field} + ? WHERE day=?", (amount, today)
    )
    conn.commit()


def total_referral_rewards():
    cur.execute("SELECT SUM(referral_rewards) AS s FROM daily_stats")
    r = cur.fetchone()["s"]
    return r or 0


def recent_daily_stats(days=7):
    cur.execute("SELECT * FROM daily_stats ORDER BY day DESC LIMIT ?", (days,))
    return cur.fetchall()


def active_users_count(days=7):
    cur.execute(
        "SELECT COUNT(*) AS c FROM users WHERE last_active >= datetime('now', ?)",
        (f"-{days} days",),
    )
    return cur.fetchone()["c"]


def sum_all_balances():
    """جمع کل موجودی کیف‌پول همه کاربران (بدهی سیستم به کاربرا)."""
    cur.execute("SELECT SUM(balance) AS s FROM users")
    return cur.fetchone()["s"] or 0


# ---------------------------------------------------------------------------
# تنظیمات قابل ویرایش از پنل ادمین (بدون کدنویسی)
# ---------------------------------------------------------------------------

def get_setting(key, default=None):
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


def get_setting_int(key, default=0):
    val = get_setting(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def set_setting(key, value):
    cur.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# درخواست‌های شارژ کیف پول (topups)
# ---------------------------------------------------------------------------

def create_topup(user_id, amount):
    cur.execute(
        "INSERT INTO topups(user_id, amount) VALUES (?, ?)", (str(user_id), amount)
    )
    conn.commit()
    return cur.lastrowid


def get_topup(topup_id):
    cur.execute("SELECT * FROM topups WHERE id=?", (topup_id,))
    return cur.fetchone()


def get_pending_receipt_topup(user_id):
    """آخرین درخواست شارژی که منتظر عکس رسیده (برای این کاربر)."""
    cur.execute(
        "SELECT * FROM topups WHERE user_id=? AND status='awaiting_receipt' "
        "ORDER BY id DESC LIMIT 1",
        (str(user_id),),
    )
    return cur.fetchone()


def set_topup_status(topup_id, status):
    cur.execute(
        "UPDATE topups SET status=?, reviewed_at=datetime('now') WHERE id=?",
        (status, topup_id),
    )
    conn.commit()


def list_pending_topups(limit=15):
    cur.execute(
        "SELECT * FROM topups WHERE status='pending_review' ORDER BY id LIMIT ?",
        (limit,),
    )
    return cur.fetchall()


def count_pending_topups():
    cur.execute("SELECT COUNT(*) AS c FROM topups WHERE status='pending_review'")
    return cur.fetchone()["c"]


def sum_approved_topups():
    """درآمد واقعی سیستم: مجموع شارژهای تاییدشده (پولی که واقعا واریز شده)."""
    cur.execute("SELECT SUM(amount) AS s FROM topups WHERE status='approved'")
    return cur.fetchone()["s"] or 0


# ---------------------------------------------------------------------------
# جلوگیری از رسید تکراری
# ---------------------------------------------------------------------------

def find_receipt(file_unique_id):
    """اگه این عکس قبلا به‌عنوان رسید ثبت شده باشه، ردیفش رو برمی‌گردونه."""
    cur.execute(
        "SELECT * FROM receipts WHERE file_unique_id=? ORDER BY id", (file_unique_id,)
    )
    return cur.fetchall()


def record_receipt(file_unique_id, user_id, topup_id):
    cur.execute(
        "INSERT INTO receipts(file_unique_id, user_id, topup_id) VALUES (?, ?, ?)",
        (file_unique_id, str(user_id), topup_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# هشدار موجودی کم (فقط یکبار هشدار میده تا دوباره شارژ بشه)
# ---------------------------------------------------------------------------

def is_low_stock_alerted():
    return get_setting("low_stock_alerted", "0") == "1"


def set_low_stock_alerted(flag: bool):
    set_setting("low_stock_alerted", "1" if flag else "0")


# ---------------------------------------------------------------------------
# سیستم تیکت پشتیبانی
# ---------------------------------------------------------------------------

def create_ticket(user_id):
    cur.execute("INSERT INTO tickets(user_id) VALUES (?)", (str(user_id),))
    conn.commit()
    return cur.lastrowid


def record_ticket_message(admin_id, message_id, ticket_id, user_id):
    cur.execute(
        "INSERT OR REPLACE INTO ticket_messages(admin_id, message_id, ticket_id, user_id) "
        "VALUES (?, ?, ?, ?)",
        (str(admin_id), message_id, ticket_id, str(user_id)),
    )
    conn.commit()


def get_ticket_message_map(admin_id, message_id):
    cur.execute(
        "SELECT ticket_id, user_id FROM ticket_messages WHERE admin_id=? AND message_id=?",
        (str(admin_id), message_id),
    )
    return cur.fetchone()


def list_open_tickets(limit=15):
    cur.execute(
        "SELECT * FROM tickets WHERE status='open' ORDER BY id DESC LIMIT ?", (limit,)
    )
    return cur.fetchall()


def close_ticket(ticket_id):
    cur.execute(
        "UPDATE tickets SET status='closed', closed_at=datetime('now') WHERE id=?",
        (ticket_id,),
    )
    conn.commit()


def count_open_tickets():
    cur.execute("SELECT COUNT(*) AS c FROM tickets WHERE status='open'")
    return cur.fetchone()["c"]


# ---------------------------------------------------------------------------
# پیام‌های قابل‌ویرایش (متن/عکس سفارشی برای صفحات اصلی)
# ---------------------------------------------------------------------------

def get_message(key):
    cur.execute("SELECT * FROM messages WHERE key=?", (key,))
    return cur.fetchone()


def set_message_text(key, text):
    row = get_message(key)
    if row is None:
        cur.execute("INSERT INTO messages(key, text) VALUES (?, ?)", (key, text))
    else:
        cur.execute("UPDATE messages SET text=? WHERE key=?", (text, key))
    conn.commit()


def set_message_photo(key, photo_file_id):
    row = get_message(key)
    if row is None:
        cur.execute(
            "INSERT INTO messages(key, photo_file_id) VALUES (?, ?)", (key, photo_file_id)
        )
    else:
        cur.execute("UPDATE messages SET photo_file_id=? WHERE key=?", (photo_file_id, key))
    conn.commit()


def clear_message(key):
    cur.execute("DELETE FROM messages WHERE key=?", (key,))
    conn.commit()
            owner TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            assigned_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats(
            day TEXT PRIMARY KEY,
            new_users INTEGER DEFAULT 0,
            sales INTEGER DEFAULT 0,
            referral_rewards INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT 'awaiting_receipt',
            created_at TEXT DEFAULT (datetime('now')),
            reviewed_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receipts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_unique_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            topup_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticket_messages(
            admin_id TEXT,
            message_id INTEGER,
            ticket_id INTEGER,
            user_id TEXT,
            PRIMARY KEY (admin_id, message_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages(
            key TEXT PRIMARY KEY,
            text TEXT,
            photo_file_id TEXT
        )
    """)
    # ستون اضافه برای ثبت قیمتی که واقعا پرداخت شده (اگه بعدا قیمت پلن عوض
    # بشه، تاریخچه فروش‌های قدیمی درست می‌مونه)
    cur.execute("PRAGMA table_info(subs)")
    existing_cols = {row["name"] for row in cur.fetchall()}
    if "price_paid" not in existing_cols:
        cur.execute("ALTER TABLE subs ADD COLUMN price_paid INTEGER")
    conn.commit()


# ---------------------------------------------------------------------------
# کاربران
# ---------------------------------------------------------------------------

def get_user(user_id):
    cur.execute("SELECT * FROM users WHERE id=?", (str(user_id),))
    return cur.fetchone()


def get_or_create_user(user_id, username=None, ref=None):
    """اگه کاربر وجود نداشت می‌سازدش (با کد رفرال در صورت معتبر بودن)."""
    user_id = str(user_id)
    row = get_user(user_id)
    if row:
        return row, False

    if ref is not None:
        ref = str(ref)
        # خود-معرفی یا رفرر ناموجود رو نادیده می‌گیریم
        if ref == user_id or not get_user(ref):
            ref = None

    cur.execute(
        "INSERT INTO users(id, username, ref) VALUES (?, ?, ?)",
        (user_id, username or "", ref),
    )
    conn.commit()
    bump_daily("new_users")
    return get_user(user_id), True


def touch_active(user_id, username=None):
    if username is not None:
        cur.execute(
            "UPDATE users SET username=?, last_active=datetime('now') WHERE id=?",
            (username, str(user_id)),
        )
    else:
        cur.execute(
            "UPDATE users SET last_active=datetime('now') WHERE id=?", (str(user_id),)
        )
    conn.commit()


def add_balance(user_id, amount):
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, str(user_id)))
    conn.commit()


def set_ban(user_id, banned: bool):
    cur.execute("UPDATE users SET banned=? WHERE id=?", (1 if banned else 0, str(user_id)))
    conn.commit()


def mark_rewarded(referred_id):
    cur.execute(
        "UPDATE users SET rewarded=1, rewarded_at=datetime('now') WHERE id=?",
        (str(referred_id),),
    )
    conn.commit()


def increment_purchased(user_id):
    cur.execute("UPDATE users SET purchased = purchased + 1 WHERE id=?", (str(user_id),))
    conn.commit()


def search_users(query, limit=10):
    q = f"%{query.strip()}%"
    cur.execute(
        "SELECT * FROM users WHERE id LIKE ? OR username LIKE ? "
        "ORDER BY joined_at DESC LIMIT ?",
        (q, q, limit),
    )
    return cur.fetchall()


def list_users(offset=0, limit=10):
    cur.execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", (limit, offset)
    )
    return cur.fetchall()


def count_users():
    cur.execute("SELECT COUNT(*) AS c FROM users")
    return cur.fetchone()["c"]


def referral_count(user_id):
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE ref=?", (str(user_id),))
    return cur.fetchone()["c"]


def referrals_rewarded_today(ref_id):
    cur.execute(
        "SELECT COUNT(*) AS c FROM users "
        "WHERE ref=? AND rewarded=1 AND date(rewarded_at)=date('now')",
        (str(ref_id),),
    )
    return cur.fetchone()["c"]


def referred_users(user_id, limit=20):
    cur.execute(
        "SELECT id, username, purchased, rewarded FROM users WHERE ref=? LIMIT ?",
        (str(user_id), limit),
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# آمار روزانه
# ---------------------------------------------------------------------------

_VALID_STAT_FIELDS = {"new_users", "sales", "referral_rewards"}


def bump_daily(field, amount=1):
    if field not in _VALID_STAT_FIELDS:
        raise ValueError(f"unknown stat field: {field}")
    today = date.today().isoformat()
    cur.execute("INSERT OR IGNORE INTO daily_stats(day) VALUES (?)", (today,))
    cur.execute(
        f"UPDATE daily_stats SET {field} = {field} + ? WHERE day=?", (amount, today)
    )
    conn.commit()


def total_referral_rewards():
    cur.execute("SELECT SUM(referral_rewards) AS s FROM daily_stats")
    r = cur.fetchone()["s"]
    return r or 0


def recent_daily_stats(days=7):
    cur.execute("SELECT * FROM daily_stats ORDER BY day DESC LIMIT ?", (days,))
    return cur.fetchall()


def active_users_count(days=7):
    cur.execute(
        "SELECT COUNT(*) AS c FROM users WHERE last_active >= datetime('now', ?)",
        (f"-{days} days",),
    )
    return cur.fetchone()["c"]


def sum_all_balances():
    """جمع کل موجودی کیف‌پول همه کاربران (بدهی سیستم به کاربرا)."""
    cur.execute("SELECT SUM(balance) AS s FROM users")
    return cur.fetchone()["s"] or 0


# ---------------------------------------------------------------------------
# تنظیمات قابل ویرایش از پنل ادمین (بدون کدنویسی)
# ---------------------------------------------------------------------------

def get_setting(key, default=None):
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


def get_setting_int(key, default=0):
    val = get_setting(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def set_setting(key, value):
    cur.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# درخواست‌های شارژ کیف پول (topups)
# ---------------------------------------------------------------------------

def create_topup(user_id, amount):
    cur.execute(
        "INSERT INTO topups(user_id, amount) VALUES (?, ?)", (str(user_id), amount)
    )
    conn.commit()
    return cur.lastrowid


def get_topup(topup_id):
    cur.execute("SELECT * FROM topups WHERE id=?", (topup_id,))
    return cur.fetchone()


def get_pending_receipt_topup(user_id):
    """آخرین درخواست شارژی که منتظر عکس رسیده (برای این کاربر)."""
    cur.execute(
        "SELECT * FROM topups WHERE user_id=? AND status='awaiting_receipt' "
        "ORDER BY id DESC LIMIT 1",
        (str(user_id),),
    )
    return cur.fetchone()


def set_topup_status(topup_id, status):
    cur.execute(
        "UPDATE topups SET status=?, reviewed_at=datetime('now') WHERE id=?",
        (status, topup_id),
    )
    conn.commit()


def list_pending_topups(limit=15):
    cur.execute(
        "SELECT * FROM topups WHERE status='pending_review' ORDER BY id LIMIT ?",
        (limit,),
    )
    return cur.fetchall()


def count_pending_topups():
    cur.execute("SELECT COUNT(*) AS c FROM topups WHERE status='pending_review'")
    return cur.fetchone()["c"]


def sum_approved_topups():
    """درآمد واقعی سیستم: مجموع شارژهای تاییدشده (پولی که واقعا واریز شده)."""
    cur.execute("SELECT SUM(amount) AS s FROM topups WHERE status='approved'")
    return cur.fetchone()["s"] or 0


# ---------------------------------------------------------------------------
# جلوگیری از رسید تکراری
# ---------------------------------------------------------------------------

def find_receipt(file_unique_id):
    """اگه این عکس قبلا به‌عنوان رسید ثبت شده باشه، ردیفش رو برمی‌گردونه."""
    cur.execute(
        "SELECT * FROM receipts WHERE file_unique_id=? ORDER BY id", (file_unique_id,)
    )
    return cur.fetchall()


def record_receipt(file_unique_id, user_id, topup_id):
    cur.execute(
        "INSERT INTO receipts(file_unique_id, user_id, topup_id) VALUES (?, ?, ?)",
        (file_unique_id, str(user_id), topup_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# هشدار موجودی کم (فقط یکبار هشدار میده تا دوباره شارژ بشه)
# ---------------------------------------------------------------------------

def is_low_stock_alerted():
    return get_setting("low_stock_alerted", "0") == "1"


def set_low_stock_alerted(flag: bool):
    set_setting("low_stock_alerted", "1" if flag else "0")


# ---------------------------------------------------------------------------
# سیستم تیکت پشتیبانی
# ---------------------------------------------------------------------------

def create_ticket(user_id):
    cur.execute("INSERT INTO tickets(user_id) VALUES (?)", (str(user_id),))
    conn.commit()
    return cur.lastrowid


def record_ticket_message(admin_id, message_id, ticket_id, user_id):
    cur.execute(
        "INSERT OR REPLACE INTO ticket_messages(admin_id, message_id, ticket_id, user_id) "
        "VALUES (?, ?, ?, ?)",
        (str(admin_id), message_id, ticket_id, str(user_id)),
    )
    conn.commit()


def get_ticket_message_map(admin_id, message_id):
    cur.execute(
        "SELECT ticket_id, user_id FROM ticket_messages WHERE admin_id=? AND message_id=?",
        (str(admin_id), message_id),
    )
    return cur.fetchone()


def list_open_tickets(limit=15):
    cur.execute(
        "SELECT * FROM tickets WHERE status='open' ORDER BY id DESC LIMIT ?", (limit,)
    )
    return cur.fetchall()


def close_ticket(ticket_id):
    cur.execute(
        "UPDATE tickets SET status='closed', closed_at=datetime('now') WHERE id=?",
        (ticket_id,),
    )
    conn.commit()


def count_open_tickets():
    cur.execute("SELECT COUNT(*) AS c FROM tickets WHERE status='open'")
    return cur.fetchone()["c"]


# ---------------------------------------------------------------------------
# پیام‌های قابل‌ویرایش (متن/عکس سفارشی برای صفحات اصلی)
# ---------------------------------------------------------------------------

def get_message(key):
    cur.execute("SELECT * FROM messages WHERE key=?", (key,))
    return cur.fetchone()


def set_message_text(key, text):
    row = get_message(key)
    if row is None:
        cur.execute("INSERT INTO messages(key, text) VALUES (?, ?)", (key, text))
    else:
        cur.execute("UPDATE messages SET text=? WHERE key=?", (text, key))
    conn.commit()


def set_message_photo(key, photo_file_id):
    row = get_message(key)
    if row is None:
        cur.execute(
            "INSERT INTO messages(key, photo_file_id) VALUES (?, ?)", (key, photo_file_id)
        )
    else:
        cur.execute("UPDATE messages SET photo_file_id=? WHERE key=?", (photo_file_id, key))
    conn.commit()


def clear_message(key):
    cur.execute("DELETE FROM messages WHERE key=?", (key,))
    conn.commit()
