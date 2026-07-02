import os
import random
import sqlite3
import string
import threading
from datetime import date

DB_PATH = os.getenv("DB_PATH", "berserk.db")

LOCK = threading.RLock()
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()


class PurchaseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _columns(table: str):
    cur.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cur.fetchall()}


def _add_column_if_missing(table: str, column: str, definition: str):
    if column not in _columns(table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _bump_daily_tx(field, amount=1):
    valid = {"new_users", "sales", "referral_rewards"}
    if field not in valid:
        raise ValueError(f"unknown stat field: {field}")
    today = date.today().isoformat()
    cur.execute("INSERT OR IGNORE INTO daily_stats(day) VALUES (?)", (today,))
    cur.execute(
        f"UPDATE daily_stats SET {field} = {field} + ? WHERE day=?",
        (amount, today),
    )


def init():
    with LOCK:
        cur.execute(
            """
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
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                owner TEXT,
                added_at TEXT DEFAULT (datetime('now')),
                assigned_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_stats(
                day TEXT PRIMARY KEY,
                new_users INTEGER DEFAULT 0,
                sales INTEGER DEFAULT 0,
                referral_rewards INTEGER DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS topups(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT DEFAULT 'awaiting_receipt',
                created_at TEXT DEFAULT (datetime('now')),
                reviewed_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_unique_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                topup_id INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now')),
                closed_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_messages(
                admin_id TEXT,
                message_id INTEGER,
                ticket_id INTEGER,
                user_id TEXT,
                PRIMARY KEY (admin_id, message_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages(
                key TEXT PRIMARY KEY,
                text TEXT,
                photo_file_id TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS purchases(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                unit_price INTEGER NOT NULL,
                status TEXT DEFAULT 'completed',
                note TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_id INTEGER NOT NULL,
                sub_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                account_name TEXT,
                price_paid INTEGER,
                assigned_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                amount INTEGER NOT NULL,
                balance_before INTEGER,
                balance_after INTEGER,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                content_type TEXT NOT NULL,
                preview TEXT,
                total INTEGER DEFAULT 0,
                success INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )

        _add_column_if_missing("subs", "price_paid", "INTEGER")
        _add_column_if_missing("subs", "account_name", "TEXT")
        _add_column_if_missing("subs", "status", "TEXT")
        _add_column_if_missing("subs", "purchase_id", "INTEGER")

        cur.execute("UPDATE subs SET status='available' WHERE status IS NULL AND used=0")
        cur.execute("UPDATE subs SET status='delivered' WHERE status IS NULL AND used=1")

        _backfill_missing_account_names()
        conn.commit()


def _random_token(length=6):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def generate_service_code():
    for _ in range(50):
        candidate = f"Berserk {_random_token(6)}"
        cur.execute("SELECT 1 FROM subs WHERE account_name=?", (candidate,))
        if cur.fetchone() is None:
            return candidate
    return f"Berserk {_random_token(8)}"


def _backfill_missing_account_names():
    cur.execute(
        "SELECT id FROM subs WHERE account_name IS NULL OR TRIM(account_name)=''"
    )
    rows = cur.fetchall()
    for row in rows:
        cur.execute(
            "UPDATE subs SET account_name=? WHERE id=?",
            (generate_service_code(), row["id"]),
        )


# کاربران

def get_user(user_id):
    cur.execute("SELECT * FROM users WHERE id=?", (str(user_id),))
    return cur.fetchone()


def get_or_create_user(user_id, username=None, ref=None):
    user_id = str(user_id)
    with LOCK:
        row = get_user(user_id)
        if row:
            return row, False
        if ref is not None:
            ref = str(ref)
            if ref == user_id or not get_user(ref):
                ref = None
        cur.execute(
            "INSERT INTO users(id, username, ref) VALUES (?, ?, ?)",
            (user_id, username or "", ref),
        )
        _bump_daily_tx("new_users")
        conn.commit()
        return get_user(user_id), True


def touch_active(user_id, username=None):
    with LOCK:
        if username is not None:
            cur.execute(
                "UPDATE users SET username=?, last_active=datetime('now') WHERE id=?",
                (username, str(user_id)),
            )
        else:
            cur.execute(
                "UPDATE users SET last_active=datetime('now') WHERE id=?",
                (str(user_id),),
            )
        conn.commit()


def add_balance(user_id, amount, action="balance_adjustment", note=""):
    user_id = str(user_id)
    amount = int(amount)
    with LOCK:
        row = get_user(user_id)
        before = int(row["balance"]) if row else None
        cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
        after = before + amount if before is not None else None
        cur.execute(
            """
            INSERT INTO ledger(user_id, action, amount, balance_before, balance_after, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, action, amount, before, after, note),
        )
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


def increment_purchased(user_id, amount=1):
    cur.execute(
        "UPDATE users SET purchased = purchased + ? WHERE id=?",
        (int(amount), str(user_id)),
    )
    conn.commit()


def search_users(query, limit=10):
    q = f"%{query.strip()}%"
    cur.execute(
        """
        SELECT * FROM users
        WHERE id LIKE ? OR username LIKE ?
        ORDER BY joined_at DESC
        LIMIT ?
        """,
        (q, q, limit),
    )
    return cur.fetchall()


def list_users(offset=0, limit=10):
    cur.execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return cur.fetchall()

# پیام همگانی

def _broadcast_where(scope):
    """Return safe WHERE clause for broadcast target scopes."""
    if scope == "all":
        return "banned=0"
    if scope == "buyers":
        return "banned=0 AND purchased > 0"
    if scope == "no_buy":
        return "banned=0 AND purchased = 0"
    if scope == "active7":
        return "banned=0 AND last_active >= datetime('now', '-7 days')"
    raise ValueError(f"unknown broadcast scope: {scope}")


def count_broadcast_targets(scope):
    where = _broadcast_where(scope)
    cur.execute(f"SELECT COUNT(*) AS c FROM users WHERE {where}")
    return cur.fetchone()["c"]


def list_broadcast_targets(scope, limit=None):
    where = _broadcast_where(scope)
    sql = f"SELECT id, username, purchased, last_active FROM users WHERE {where} ORDER BY joined_at DESC"
    params = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    cur.execute(sql, params)
    return cur.fetchall()


def log_broadcast(admin_id, scope, content_type, preview, total, success, failed):
    cur.execute(
        """
        INSERT INTO broadcast_logs(admin_id, scope, content_type, preview, total, success, failed)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(admin_id), scope, content_type, preview, int(total), int(success), int(failed)),
    )
    conn.commit()
    return cur.lastrowid


def list_broadcast_logs(limit=10):
    cur.execute(
        "SELECT * FROM broadcast_logs ORDER BY id DESC LIMIT ?",
        (int(limit),),
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
        """
        SELECT COUNT(*) AS c FROM users
        WHERE ref=? AND rewarded=1 AND date(rewarded_at)=date('now')
        """,
        (str(ref_id),),
    )
    return cur.fetchone()["c"]


def referred_users(user_id, limit=20):
    cur.execute(
        "SELECT id, username, purchased, rewarded FROM users WHERE ref=? LIMIT ?",
        (str(user_id), limit),
    )
    return cur.fetchall()


# آمار

def bump_daily(field, amount=1):
    with LOCK:
        _bump_daily_tx(field, amount)
        conn.commit()


def total_referral_rewards():
    cur.execute("SELECT SUM(referral_rewards) AS s FROM daily_stats")
    return cur.fetchone()["s"] or 0


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
    cur.execute("SELECT SUM(balance) AS s FROM users")
    return cur.fetchone()["s"] or 0


# تنظیمات

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
        """
        INSERT INTO settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )
    conn.commit()


# شارژ کیف پول

def create_topup(user_id, amount):
    cur.execute(
        "INSERT INTO topups(user_id, amount) VALUES (?, ?)",
        (str(user_id), amount),
    )
    conn.commit()
    return cur.lastrowid


def get_topup(topup_id):
    cur.execute("SELECT * FROM topups WHERE id=?", (topup_id,))
    return cur.fetchone()


def get_pending_receipt_topup(user_id):
    cur.execute(
        """
        SELECT * FROM topups
        WHERE user_id=? AND status='awaiting_receipt'
        ORDER BY id DESC LIMIT 1
        """,
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
    cur.execute("SELECT SUM(amount) AS s FROM topups WHERE status='approved'")
    return cur.fetchone()["s"] or 0


# رسید تکراری

def find_receipt(file_unique_id):
    cur.execute(
        "SELECT * FROM receipts WHERE file_unique_id=? ORDER BY id",
        (file_unique_id,),
    )
    return cur.fetchall()


def record_receipt(file_unique_id, user_id, topup_id):
    cur.execute(
        "INSERT INTO receipts(file_unique_id, user_id, topup_id) VALUES (?, ?, ?)",
        (file_unique_id, str(user_id), topup_id),
    )
    conn.commit()


# هشدار موجودی کم

def is_low_stock_alerted():
    return get_setting("low_stock_alerted", "0") == "1"


def set_low_stock_alerted(flag: bool):
    set_setting("low_stock_alerted", "1" if flag else "0")


# تیکت

def create_ticket(user_id):
    cur.execute("INSERT INTO tickets(user_id) VALUES (?)", (str(user_id),))
    conn.commit()
    return cur.lastrowid


def record_ticket_message(admin_id, message_id, ticket_id, user_id):
    cur.execute(
        """
        INSERT OR REPLACE INTO ticket_messages(admin_id, message_id, ticket_id, user_id)
        VALUES (?, ?, ?, ?)
        """,
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
        "SELECT * FROM tickets WHERE status='open' ORDER BY id DESC LIMIT ?",
        (limit,),
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


# پیام‌ها

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
            "INSERT INTO messages(key, photo_file_id) VALUES (?, ?)",
            (key, photo_file_id),
        )
    else:
        cur.execute(
            "UPDATE messages SET photo_file_id=? WHERE key=?",
            (photo_file_id, key),
        )
    conn.commit()


def clear_message(key):
    cur.execute("DELETE FROM messages WHERE key=?", (key,))
    conn.commit()


# خرید و دفترکل

def complete_purchase(user_id, quantity, unit_price, note=""):
    user_id = str(user_id)
    quantity = int(quantity)
    unit_price = int(unit_price)
    total = quantity * unit_price

    if quantity < 1 or quantity > 4:
        raise PurchaseError("invalid_quantity", "تعداد انتخاب‌شده معتبر نیست.")

    with LOCK:
        try:
            conn.execute("BEGIN IMMEDIATE")

            cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
            user = cur.fetchone()
            if not user:
                raise PurchaseError("user_not_found", "کاربر پیدا نشد.")
            if int(user["banned"] or 0):
                raise PurchaseError("banned", "حساب شما مسدود است.")

            balance_before = int(user["balance"] or 0)
            if balance_before < total:
                raise PurchaseError("insufficient_balance", "موجودی کیف پول کافی نیست.")

            cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=0")
            stock = int(cur.fetchone()["c"])
            if stock < quantity:
                raise PurchaseError("insufficient_stock", "موجودی سرویس کافی نیست.")

            cur.execute(
                """
                INSERT INTO purchases(user_id, quantity, amount, unit_price, status, note)
                VALUES (?, ?, ?, ?, 'completed', ?)
                """,
                (user_id, quantity, total, unit_price, note),
            )
            purchase_id = cur.lastrowid

            balance_after = balance_before - total
            cur.execute(
                "UPDATE users SET balance=?, purchased=purchased+? WHERE id=?",
                (balance_after, quantity, user_id),
            )
            cur.execute(
                """
                INSERT INTO ledger(user_id, action, amount, balance_before, balance_after, note)
                VALUES (?, 'purchase', ?, ?, ?, ?)
                """,
                (user_id, -total, balance_before, balance_after, f"purchase_id={purchase_id}"),
            )

            cur.execute(
                "SELECT * FROM subs WHERE used=0 ORDER BY id LIMIT ?",
                (quantity,),
            )
            available = cur.fetchall()
            items = []

            for sub in available:
                account_name = sub["account_name"] or generate_service_code()
                cur.execute(
                    """
                    UPDATE subs
                    SET used=1,
                        owner=?,
                        assigned_at=datetime('now'),
                        price_paid=?,
                        account_name=?,
                        status='delivered',
                        purchase_id=?
                    WHERE id=? AND used=0
                    """,
                    (user_id, unit_price, account_name, purchase_id, sub["id"]),
                )
                if cur.rowcount != 1:
                    raise PurchaseError("stock_race", "موجودی همزمان تغییر کرد. دوباره تلاش کنید.")

                cur.execute(
                    "SELECT id, link, account_name, assigned_at, price_paid, status, purchase_id FROM subs WHERE id=?",
                    (sub["id"],),
                )
                assigned = cur.fetchone()

                cur.execute(
                    """
                    INSERT INTO purchase_items(purchase_id, sub_id, user_id, account_name, price_paid, assigned_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        purchase_id,
                        assigned["id"],
                        user_id,
                        assigned["account_name"],
                        unit_price,
                        assigned["assigned_at"],
                    ),
                )
                items.append(dict(assigned))

            _bump_daily_tx("sales", quantity)
            conn.commit()
            return {
                "purchase_id": purchase_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": total,
                "balance_before": balance_before,
                "balance_after": balance_after,
                "items": items,
            }
        except PurchaseError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise PurchaseError("unexpected", "خطای داخلی خرید رخ داد.") from exc


def get_purchase(purchase_id):
    cur.execute("SELECT * FROM purchases WHERE id=?", (purchase_id,))
    return cur.fetchone()


def list_user_purchases(user_id, limit=20):
    cur.execute(
        "SELECT * FROM purchases WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (str(user_id), limit),
    )
    return cur.fetchall()


def list_user_ledger(user_id, limit=20):
    cur.execute(
        "SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (str(user_id), limit),
    )
    return cur.fetchall()


def purchase_count_by_user(user_id):
    cur.execute(
        "SELECT COUNT(*) AS c FROM purchases WHERE user_id=? AND status='completed'",
        (str(user_id),),
    )
    return cur.fetchone()["c"]
