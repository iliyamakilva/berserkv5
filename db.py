import random
import sqlite3
import string
import threading
from datetime import date
from pathlib import Path

from config import DB_PATH

_db_parent = Path(DB_PATH).expanduser().parent
if str(_db_parent) not in ("", "."):
    _db_parent.mkdir(parents=True, exist_ok=True)

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
        (int(amount), today),
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
                link TEXT,
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
            CREATE TABLE IF NOT EXISTS custom_buttons(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                button_type TEXT,
                payload TEXT,
                location TEXT DEFAULT 'main',
                sort_order INTEGER DEFAULT 100,
                is_active INTEGER DEFAULT 1,
                audience TEXT DEFAULT 'all',
                starts_at TEXT,
                ends_at TEXT,
                status TEXT DEFAULT 'draft',
                draft_title TEXT,
                draft_button_type TEXT,
                draft_payload TEXT,
                draft_location TEXT,
                draft_sort_order INTEGER,
                draft_is_active INTEGER,
                draft_audience TEXT,
                draft_starts_at TEXT,
                draft_ends_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                published_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS backup_logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id TEXT,
                operation_type TEXT NOT NULL,
                backup_file_name TEXT,
                file_size INTEGER,
                status TEXT DEFAULT 'ok',
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
        _add_column_if_missing("purchase_items", "link", "TEXT")
        _add_column_if_missing("purchase_items", "status", "TEXT DEFAULT 'active'")
        _add_column_if_missing("purchase_items", "reverted_at", "TEXT")
        _add_column_if_missing("purchase_items", "reverted_by", "TEXT")
        _add_column_if_missing("purchase_items", "revert_reason", "TEXT")
        _add_column_if_missing("messages", "draft_text", "TEXT")
        _add_column_if_missing("messages", "draft_photo_file_id", "TEXT")
        _add_column_if_missing("messages", "updated_at", "TEXT")
        _add_column_if_missing("messages", "published_at", "TEXT")
        cur.execute("""
            SELECT file_unique_id, COUNT(*) AS c
            FROM receipts
            GROUP BY file_unique_id
            HAVING c > 1
            LIMIT 1
        """)
        if cur.fetchone() is None:
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_unique_file ON receipts(file_unique_id)")
        else:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_receipts_file ON receipts(file_unique_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_custom_buttons_location ON custom_buttons(location, sort_order)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_backup_logs_created ON backup_logs(created_at)")

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
    cur.execute("SELECT id FROM subs WHERE account_name IS NULL OR TRIM(account_name)=''")
    rows = cur.fetchall()
    for row in rows:
        cur.execute(
            "UPDATE subs SET account_name=? WHERE id=?",
            (generate_service_code(), row["id"]),
        )


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
                (username or "", str(user_id)),
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
        if not row:
            get_or_create_user(user_id)
            row = get_user(user_id)
        before = int(row["balance"] or 0)
        after = before + amount
        cur.execute("UPDATE users SET balance=? WHERE id=?", (after, user_id))
        cur.execute(
            """
            INSERT INTO ledger(user_id, action, amount, balance_before, balance_after, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, action, amount, before, after, note or ""),
        )
        conn.commit()
        return after


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
    q = f"%{str(query).strip().lstrip('@')}%"
    cur.execute(
        """
        SELECT * FROM users
        WHERE id LIKE ? OR username LIKE ?
        ORDER BY joined_at DESC
        LIMIT ?
        """,
        (q, q, int(limit)),
    )
    return cur.fetchall()


def list_users(offset=0, limit=10):
    cur.execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    )
    return cur.fetchall()


def count_users():
    cur.execute("SELECT COUNT(*) AS c FROM users")
    return cur.fetchone()["c"]


def active_users_count(days=7):
    cur.execute(
        "SELECT COUNT(*) AS c FROM users WHERE last_active >= datetime('now', ?)",
        (f"-{int(days)} days",),
    )
    return cur.fetchone()["c"]


def sum_all_balances():
    cur.execute("SELECT SUM(balance) AS s FROM users")
    return cur.fetchone()["s"] or 0


_BROADCAST_WHERE = {
    "all": "u.banned=0",
    "buyers": "u.banned=0 AND u.purchased > 0",
    "no_buy": "u.banned=0 AND u.purchased = 0",
    "has_sub": "u.banned=0 AND EXISTS (SELECT 1 FROM subs s WHERE s.owner=u.id AND s.used=1)",
    "no_sub": "u.banned=0 AND NOT EXISTS (SELECT 1 FROM subs s WHERE s.owner=u.id AND s.used=1)",
    "active7": "u.banned=0 AND u.last_active >= datetime('now', '-7 days')",
    "inactive7": "u.banned=0 AND (u.last_active < datetime('now', '-7 days') OR u.last_active IS NULL)",
    "positive_balance": "u.banned=0 AND u.balance > 0",
    "low_balance": "u.banned=0 AND u.balance > 0 AND u.balance < COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key='plan_price'), 100000)",
    "referred": "u.banned=0 AND u.ref IS NOT NULL AND TRIM(u.ref) <> ''",
    "referrers": "u.banned=0 AND EXISTS (SELECT 1 FROM users child WHERE child.ref=u.id)",
}


def _broadcast_where(scope):
    if scope not in _BROADCAST_WHERE:
        raise ValueError(f"unknown broadcast scope: {scope}")
    return _BROADCAST_WHERE[scope]


def count_broadcast_targets(scope):
    where = _broadcast_where(scope)
    cur.execute(f"SELECT COUNT(*) AS c FROM users u WHERE {where}")
    return cur.fetchone()["c"]


def list_broadcast_targets(scope, limit=None):
    where = _broadcast_where(scope)
    sql = f"""
        SELECT u.id, u.username, u.purchased, u.balance, u.last_active
        FROM users u
        WHERE {where}
        ORDER BY u.joined_at DESC
    """
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
    cur.execute("SELECT * FROM broadcast_logs ORDER BY id DESC LIMIT ?", (int(limit),))
    return cur.fetchall()


def referral_count(user_id):
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE ref=?", (str(user_id),))
    return cur.fetchone()["c"]


def referrals_rewarded_today(ref_id):
    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM users
        WHERE ref=? AND rewarded=1 AND date(rewarded_at)=date('now')
        """,
        (str(ref_id),),
    )
    return cur.fetchone()["c"]


def referred_users(user_id, limit=20):
    cur.execute(
        """
        SELECT id, username, purchased, rewarded, joined_at
        FROM users
        WHERE ref=?
        ORDER BY joined_at DESC
        LIMIT ?
        """,
        (str(user_id), int(limit)),
    )
    return cur.fetchall()


def rewarded_referral_count(user_id):
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE ref=? AND rewarded=1", (str(user_id),))
    return cur.fetchone()["c"]


def referral_reward_total(user_id):
    cur.execute(
        """
        SELECT SUM(amount) AS s
        FROM ledger
        WHERE user_id=? AND action='referral_reward'
        """,
        (str(user_id),),
    )
    return cur.fetchone()["s"] or 0


def total_referral_rewards():
    cur.execute("SELECT SUM(referral_rewards) AS s FROM daily_stats")
    return cur.fetchone()["s"] or 0


def bump_daily(field, amount=1):
    with LOCK:
        _bump_daily_tx(field, amount)
        conn.commit()


def recent_daily_stats(days=7):
    cur.execute("SELECT * FROM daily_stats ORDER BY day DESC LIMIT ?", (int(days),))
    return cur.fetchall()


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
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )
    conn.commit()


def create_topup(user_id, amount):
    cur.execute("INSERT INTO topups(user_id, amount) VALUES (?, ?)", (str(user_id), int(amount)))
    conn.commit()
    return cur.lastrowid


def get_topup(topup_id):
    cur.execute("SELECT * FROM topups WHERE id=?", (int(topup_id),))
    return cur.fetchone()


def get_pending_receipt_topup(user_id):
    cur.execute(
        """
        SELECT * FROM topups
        WHERE user_id=? AND status='awaiting_receipt'
        ORDER BY id DESC
        LIMIT 1
        """,
        (str(user_id),),
    )
    return cur.fetchone()


def set_topup_status(topup_id, status):
    cur.execute(
        "UPDATE topups SET status=?, reviewed_at=datetime('now') WHERE id=?",
        (status, int(topup_id)),
    )
    conn.commit()


def list_pending_topups(limit=15):
    cur.execute("SELECT * FROM topups WHERE status='pending_review' ORDER BY id LIMIT ?", (int(limit),))
    return cur.fetchall()


def count_pending_topups():
    cur.execute("SELECT COUNT(*) AS c FROM topups WHERE status='pending_review'")
    return cur.fetchone()["c"]


def sum_approved_topups():
    cur.execute("SELECT SUM(amount) AS s FROM topups WHERE status='approved'")
    return cur.fetchone()["s"] or 0


def list_user_topups(user_id, limit=10):
    cur.execute(
        """
        SELECT *
        FROM topups
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(user_id), int(limit)),
    )
    return cur.fetchall()


def find_receipt(file_unique_id):
    cur.execute("SELECT * FROM receipts WHERE file_unique_id=? ORDER BY id", (file_unique_id,))
    return cur.fetchall()


def record_receipt(file_unique_id, user_id, topup_id):
    try:
        cur.execute(
            "INSERT INTO receipts(file_unique_id, user_id, topup_id) VALUES (?, ?, ?)",
            (file_unique_id, str(user_id), int(topup_id)),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def is_low_stock_alerted():
    return get_setting("low_stock_alerted", "0") == "1"


def set_low_stock_alerted(flag: bool):
    set_setting("low_stock_alerted", "1" if flag else "0")


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
        (str(admin_id), int(message_id), int(ticket_id), str(user_id)),
    )
    conn.commit()


def get_ticket_message_map(admin_id, message_id):
    cur.execute(
        """
        SELECT ticket_id, user_id
        FROM ticket_messages
        WHERE admin_id=? AND message_id=?
        """,
        (str(admin_id), int(message_id)),
    )
    return cur.fetchone()


def list_open_tickets(limit=15):
    cur.execute("SELECT * FROM tickets WHERE status='open' ORDER BY id DESC LIMIT ?", (int(limit),))
    return cur.fetchall()


def close_ticket(ticket_id):
    cur.execute("UPDATE tickets SET status='closed', closed_at=datetime('now') WHERE id=?", (int(ticket_id),))
    conn.commit()


def count_open_tickets():
    cur.execute("SELECT COUNT(*) AS c FROM tickets WHERE status='open'")
    return cur.fetchone()["c"]


def list_user_tickets(user_id, limit=10):
    cur.execute(
        """
        SELECT *
        FROM tickets
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(user_id), int(limit)),
    )
    return cur.fetchall()


def user_ticket_counts(user_id):
    cur.execute(
        """
        SELECT
            SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_count,
            COUNT(*) AS total_count
        FROM tickets
        WHERE user_id=?
        """,
        (str(user_id),),
    )
    row = cur.fetchone()
    return {
        "open": row["open_count"] or 0,
        "closed": row["closed_count"] or 0,
        "total": row["total_count"] or 0,
    }


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
        cur.execute("INSERT INTO messages(key, photo_file_id) VALUES (?, ?)", (key, photo_file_id))
    else:
        cur.execute("UPDATE messages SET photo_file_id=? WHERE key=?", (photo_file_id, key))
    conn.commit()


def clear_message(key):
    cur.execute("DELETE FROM messages WHERE key=?", (key,))
    conn.commit()


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
                (user_id, quantity, total, unit_price, note or ""),
            )
            purchase_id = cur.lastrowid

            balance_after = balance_before - total
            cur.execute("UPDATE users SET balance=?, purchased=purchased+? WHERE id=?", (balance_after, quantity, user_id))
            cur.execute(
                """
                INSERT INTO ledger(user_id, action, amount, balance_before, balance_after, note)
                VALUES (?, 'purchase', ?, ?, ?, ?)
                """,
                (user_id, -total, balance_before, balance_after, f"purchase_id={purchase_id}"),
            )

            cur.execute("SELECT * FROM subs WHERE used=0 ORDER BY id LIMIT ?", (quantity,))
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
                    """
                    SELECT id, link, account_name, assigned_at, price_paid, status, purchase_id
                    FROM subs
                    WHERE id=?
                    """,
                    (sub["id"],),
                )
                assigned = cur.fetchone()
                cur.execute(
                    """
                    INSERT INTO purchase_items(purchase_id, sub_id, user_id, account_name, link, price_paid, assigned_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        purchase_id,
                        assigned["id"],
                        user_id,
                        assigned["account_name"],
                        assigned["link"],
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
    cur.execute("SELECT * FROM purchases WHERE id=?", (int(purchase_id),))
    return cur.fetchone()


def list_user_purchases(user_id, limit=20):
    cur.execute(
        """
        SELECT *
        FROM purchases
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(user_id), int(limit)),
    )
    return cur.fetchall()


def list_user_ledger(user_id, limit=20):
    cur.execute(
        """
        SELECT *
        FROM ledger
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(user_id), int(limit)),
    )
    return cur.fetchall()


def purchase_count_by_user(user_id):
    cur.execute(
        "SELECT COUNT(*) AS c FROM purchases WHERE user_id=? AND status='completed'",
        (str(user_id),),
    )
    return cur.fetchone()["c"]


def delivered_sub_count_by_user(user_id):
    cur.execute("SELECT COUNT(*) AS c FROM subs WHERE owner=? AND used=1", (str(user_id),))
    return cur.fetchone()["c"]



def approve_topup_atomic(topup_id, admin_id=None):
    """
    تایید شارژ به صورت اتمیک:
    اگر وضعیت هنوز pending_review باشد، وضعیت approved می‌شود و همان داخل تراکنش موجودی اضافه می‌شود.
    خروجی: (ok, reason, topup_row, new_balance)
    """
    topup_id = int(topup_id)
    with LOCK:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT * FROM topups WHERE id=?", (topup_id,))
            topup = cur.fetchone()
            if not topup:
                conn.rollback()
                return False, "not_found", None, None
            if topup["status"] != "pending_review":
                conn.rollback()
                return False, "already_reviewed", topup, None

            user_id = str(topup["user_id"])
            amount = int(topup["amount"])
            cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
            user = cur.fetchone()
            if not user:
                cur.execute("INSERT INTO users(id) VALUES (?)", (user_id,))
                before = 0
            else:
                before = int(user["balance"] or 0)
            after = before + amount
            cur.execute("UPDATE users SET balance=? WHERE id=?", (after, user_id))
            cur.execute(
                """
                INSERT INTO ledger(user_id, action, amount, balance_before, balance_after, note)
                VALUES (?, 'topup_approved', ?, ?, ?, ?)
                """,
                (user_id, amount, before, after, f"topup_id={topup_id};admin_id={admin_id or '-'}"),
            )
            cur.execute(
                "UPDATE topups SET status='approved', reviewed_at=datetime('now') WHERE id=? AND status='pending_review'",
                (topup_id,),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False, "already_reviewed", topup, None
            conn.commit()
            cur.execute("SELECT * FROM topups WHERE id=?", (topup_id,))
            return True, "approved", cur.fetchone(), after
        except Exception:
            conn.rollback()
            raise

# --- Text message drafts / publishing ---

def set_message_draft_text(key, text):
    row = get_message(key)
    if row is None:
        cur.execute(
            "INSERT INTO messages(key, draft_text, updated_at) VALUES (?, ?, datetime('now'))",
            (key, text),
        )
    else:
        cur.execute(
            "UPDATE messages SET draft_text=?, updated_at=datetime('now') WHERE key=?",
            (text, key),
        )
    conn.commit()


def set_message_draft_photo(key, photo_file_id):
    row = get_message(key)
    if row is None:
        cur.execute(
            "INSERT INTO messages(key, draft_photo_file_id, updated_at) VALUES (?, ?, datetime('now'))",
            (key, photo_file_id),
        )
    else:
        cur.execute(
            "UPDATE messages SET draft_photo_file_id=?, updated_at=datetime('now') WHERE key=?",
            (photo_file_id, key),
        )
    conn.commit()


def publish_message_draft(key):
    row = get_message(key)
    if row is None:
        return False
    draft_text = row["draft_text"] if "draft_text" in row.keys() else None
    draft_photo = row["draft_photo_file_id"] if "draft_photo_file_id" in row.keys() else None
    if draft_text is None and draft_photo is None:
        return False
    cur.execute(
        """
        UPDATE messages
        SET text=COALESCE(draft_text, text),
            photo_file_id=COALESCE(draft_photo_file_id, photo_file_id),
            draft_text=NULL,
            draft_photo_file_id=NULL,
            published_at=datetime('now'),
            updated_at=datetime('now')
        WHERE key=?
        """,
        (key,),
    )
    conn.commit()
    return cur.rowcount == 1


def clear_message_draft(key):
    cur.execute(
        "UPDATE messages SET draft_text=NULL, draft_photo_file_id=NULL, updated_at=datetime('now') WHERE key=?",
        (key,),
    )
    conn.commit()


# --- Custom buttons (draft first, publish after preview) ---

ALLOWED_CUSTOM_BUTTON_TYPES = {"text", "link", "submenu", "file", "support", "buy_plan", "faq", "guide"}
ALLOWED_CUSTOM_BUTTON_LOCATIONS = {"main", "buy", "my_services", "wallet", "support", "guide", "account"}
ALLOWED_CUSTOM_BUTTON_AUDIENCES = {"all", "buyers", "no_buy", "has_service", "no_service", "admins"}


def _normalize_custom_button_payload(data):
    data = dict(data or {})
    title = (data.get("title") or "").strip()
    button_type = (data.get("button_type") or "text").strip().lower()
    payload = (data.get("payload") or "").strip()
    location = (data.get("location") or "main").strip().lower()
    audience = (data.get("audience") or "all").strip().lower()

    if not title:
        raise ValueError("button title is required")
    if button_type not in ALLOWED_CUSTOM_BUTTON_TYPES:
        raise ValueError("invalid button type")
    if location not in ALLOWED_CUSTOM_BUTTON_LOCATIONS:
        raise ValueError("invalid button location")
    if audience not in ALLOWED_CUSTOM_BUTTON_AUDIENCES:
        raise ValueError("invalid button audience")

    sort_order = int(data.get("sort_order") if data.get("sort_order") not in (None, "") else 100)
    is_active = 1 if str(data.get("is_active", "1")).lower() in {"1", "true", "active", "yes", "on", "فعال"} else 0
    starts_at = (data.get("starts_at") or "").strip() or None
    ends_at = (data.get("ends_at") or "").strip() or None

    return {
        "title": title,
        "button_type": button_type,
        "payload": payload,
        "location": location,
        "sort_order": sort_order,
        "is_active": is_active,
        "audience": audience,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def create_custom_button_draft(data):
    data = _normalize_custom_button_payload(data)
    cur.execute(
        """
        INSERT INTO custom_buttons(
            status,
            draft_title, draft_button_type, draft_payload, draft_location,
            draft_sort_order, draft_is_active, draft_audience, draft_starts_at, draft_ends_at,
            updated_at
        ) VALUES ('draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            data["title"], data["button_type"], data["payload"], data["location"],
            data["sort_order"], data["is_active"], data["audience"], data["starts_at"], data["ends_at"],
        ),
    )
    conn.commit()
    return cur.lastrowid


def save_custom_button_draft(button_id, data):
    data = _normalize_custom_button_payload(data)
    cur.execute(
        """
        UPDATE custom_buttons
        SET draft_title=?, draft_button_type=?, draft_payload=?, draft_location=?,
            draft_sort_order=?, draft_is_active=?, draft_audience=?, draft_starts_at=?, draft_ends_at=?,
            updated_at=datetime('now')
        WHERE id=?
        """,
        (
            data["title"], data["button_type"], data["payload"], data["location"],
            data["sort_order"], data["is_active"], data["audience"], data["starts_at"], data["ends_at"],
            int(button_id),
        ),
    )
    conn.commit()
    return cur.rowcount == 1


def publish_custom_button(button_id):
    row = get_custom_button(button_id)
    if not row:
        return False
    if not row["draft_title"]:
        return False
    cur.execute(
        """
        UPDATE custom_buttons
        SET title=draft_title,
            button_type=draft_button_type,
            payload=draft_payload,
            location=draft_location,
            sort_order=draft_sort_order,
            is_active=draft_is_active,
            audience=draft_audience,
            starts_at=draft_starts_at,
            ends_at=draft_ends_at,
            status='published',
            draft_title=NULL,
            draft_button_type=NULL,
            draft_payload=NULL,
            draft_location=NULL,
            draft_sort_order=NULL,
            draft_is_active=NULL,
            draft_audience=NULL,
            draft_starts_at=NULL,
            draft_ends_at=NULL,
            published_at=datetime('now'),
            updated_at=datetime('now')
        WHERE id=?
        """,
        (int(button_id),),
    )
    conn.commit()
    return cur.rowcount == 1


def get_custom_button(button_id):
    cur.execute("SELECT * FROM custom_buttons WHERE id=?", (int(button_id),))
    return cur.fetchone()


def delete_custom_button(button_id):
    cur.execute("DELETE FROM custom_buttons WHERE id=?", (int(button_id),))
    conn.commit()
    return cur.rowcount == 1


def list_custom_buttons(location=None, include_drafts=True, limit=50):
    sql = "SELECT * FROM custom_buttons"
    params = []
    where = []
    if location:
        where.append("COALESCE(draft_location, location)=?")
        params.append(location)
    if not include_drafts:
        where.append("status='published'")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(draft_sort_order, sort_order, 100), id DESC LIMIT ?"
    params.append(int(limit))
    cur.execute(sql, params)
    return cur.fetchall()


def list_active_custom_buttons(location="main"):
    cur.execute(
        """
        SELECT * FROM custom_buttons
        WHERE status='published'
          AND is_active=1
          AND location=?
          AND (starts_at IS NULL OR starts_at='' OR starts_at <= datetime('now'))
          AND (ends_at IS NULL OR ends_at='' OR ends_at >= datetime('now'))
        ORDER BY sort_order, id
        """,
        (location,),
    )
    return cur.fetchall()


def get_active_custom_button_by_title(title):
    cur.execute(
        """
        SELECT * FROM custom_buttons
        WHERE status='published'
          AND is_active=1
          AND location='main'
          AND title=?
          AND (starts_at IS NULL OR starts_at='' OR starts_at <= datetime('now'))
          AND (ends_at IS NULL OR ends_at='' OR ends_at >= datetime('now'))
        ORDER BY sort_order, id
        LIMIT 1
        """,
        ((title or "").strip(),),
    )
    return cur.fetchone()


def custom_button_has_draft(row):
    return bool(row and row["draft_title"])


def stage_custom_button_toggle(button_id):
    row = get_custom_button(button_id)
    if not row:
        return False
    base = custom_button_effective_data(row)
    base["is_active"] = 0 if int(base.get("is_active") or 0) else 1
    return save_custom_button_draft(button_id, base)


def custom_button_effective_data(row, prefer_draft=True):
    if prefer_draft and row["draft_title"]:
        return {
            "title": row["draft_title"],
            "button_type": row["draft_button_type"],
            "payload": row["draft_payload"],
            "location": row["draft_location"],
            "sort_order": row["draft_sort_order"],
            "is_active": row["draft_is_active"],
            "audience": row["draft_audience"],
            "starts_at": row["draft_starts_at"],
            "ends_at": row["draft_ends_at"],
        }
    return {
        "title": row["title"],
        "button_type": row["button_type"],
        "payload": row["payload"],
        "location": row["location"],
        "sort_order": row["sort_order"],
        "is_active": row["is_active"],
        "audience": row["audience"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
    }


# --- Backup logs / schema metadata ---

def log_backup_operation(admin_id, operation_type, backup_file_name=None, file_size=0, status="ok", note=""):
    cur.execute(
        """
        INSERT INTO backup_logs(admin_id, operation_type, backup_file_name, file_size, status, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (str(admin_id) if admin_id is not None else None, operation_type, backup_file_name, int(file_size or 0), status, note or ""),
    )
    conn.commit()
    return cur.lastrowid


def list_backup_logs(limit=10):
    cur.execute("SELECT * FROM backup_logs ORDER BY id DESC LIMIT ?", (int(limit),))
    return cur.fetchall()
