"""Subscription-link pool management.

Core invariants:
- an exact link is stored once;
- an available row can be assigned only once;
- returning a service reuses the same row instead of creating a duplicate.
"""

from __future__ import annotations

import sqlite3

import db


def _generate_account_name():
    return db.generate_service_code()


def get_sub(plan_id=None):
    plan_id = int(plan_id) if plan_id is not None else db.default_plan_id()
    with db.LOCK:
        db.cur.execute(
            """
            SELECT id, link, account_name, status, price_paid, assigned_at,
                   owner, purchase_id, plan_id
            FROM subs
            WHERE used=0 AND plan_id=?
            ORDER BY id
            LIMIT 1
            """,
            (plan_id,),
        )
        return db.cur.fetchone()


def get_sub_detail(sub_id):
    with db.LOCK:
        db.cur.execute(
            """
            SELECT id, link, account_name, status, price_paid, assigned_at,
                   owner, purchase_id, used, plan_id
            FROM subs
            WHERE id=?
            """,
            (int(sub_id),),
        )
        return db.cur.fetchone()


def assign_sub(sub_id, user_id, price_paid=None):
    """Legacy one-link assignment kept for compatibility.

    Normal purchases should use db.complete_purchase(), which records purchase
    and ledger rows atomically.
    """
    with db.LOCK:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            user = db.get_user(user_id)
            is_test = int(user["is_test"] or 0) if user and "is_test" in user.keys() else 0
            account_name = _generate_account_name()
            db.cur.execute(
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
            changed = db.cur.rowcount == 1
            if changed and not is_test:
                db._bump_daily_tx("sales")
            db.conn.commit()
            return changed
        except Exception:
            db.conn.rollback()
            raise


def add_sub(link, plan_id=None):
    link = (link or "").strip()
    if not link:
        return None

    plan_id = int(plan_id) if plan_id is not None else db.default_plan_id()
    if not db.get_plan(plan_id):
        raise ValueError("plan not found")

    with db.LOCK:
        try:
            db.cur.execute(
                """
                INSERT INTO subs(link, account_name, status, plan_id)
                VALUES (?, ?, 'available', ?)
                """,
                (link, _generate_account_name(), plan_id),
            )
            db.conn.commit()
            new_id = db.cur.lastrowid
        except sqlite3.IntegrityError:
            db.conn.rollback()
            return None

    db.set_low_stock_alerted(False)
    db.set_plan_low_stock_alerted(plan_id, False)
    return new_id


def add_subs_bulk(links, plan_id=None):
    plan_id = int(plan_id) if plan_id is not None else db.default_plan_id()
    if not db.get_plan(plan_id):
        raise ValueError("plan not found")

    normalized = []
    seen = set()
    for raw_link in links:
        link = (raw_link or "").strip()
        if link and link not in seen:
            normalized.append(link)
            seen.add(link)

    if not normalized:
        return 0

    added = 0
    with db.LOCK:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            for link in normalized:
                try:
                    db.cur.execute(
                        """
                        INSERT INTO subs(link, account_name, status, plan_id)
                        VALUES (?, ?, 'available', ?)
                        """,
                        (link, _generate_account_name(), plan_id),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    continue
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise

    if added:
        db.set_low_stock_alerted(False)
        db.set_plan_low_stock_alerted(plan_id, False)
    return added


def stock_count(plan_id=None):
    with db.LOCK:
        if plan_id is None:
            db.cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=0")
        else:
            db.cur.execute(
                "SELECT COUNT(*) AS c FROM subs WHERE used=0 AND plan_id=?",
                (int(plan_id),),
            )
        return int(db.cur.fetchone()["c"] or 0)


def sold_count(plan_id=None):
    with db.LOCK:
        if plan_id is None:
            db.cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=1")
        else:
            db.cur.execute(
                "SELECT COUNT(*) AS c FROM subs WHERE used=1 AND plan_id=?",
                (int(plan_id),),
            )
        return int(db.cur.fetchone()["c"] or 0)


def user_subs(user_id, limit=None):
    sql = """
        SELECT id, link, account_name, assigned_at, price_paid, status,
               purchase_id, used, plan_id
        FROM subs
        WHERE owner=? AND used=1
        ORDER BY assigned_at ASC, id ASC
    """
    params = [str(user_id)]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with db.LOCK:
        db.cur.execute(sql, params)
        return db.cur.fetchall()


def short_link(link, size=34):
    link = link or ""
    return link if len(link) <= size else link[:size] + "..."


def link_counts():
    with db.LOCK:
        db.cur.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS available,
                SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS delivered,
                SUM(CASE WHEN status='disabled' THEN 1 ELSE 0 END) AS disabled
            FROM subs
            """
        )
        row = db.cur.fetchone()
    return {
        "total": int(row["total"] or 0),
        "available": int(row["available"] or 0),
        "delivered": int(row["delivered"] or 0),
        "disabled": int(row["disabled"] or 0),
    }


def list_links(kind="all", limit=15, offset=0):
    base = """
        SELECT id, link, used, owner, added_at, assigned_at, price_paid,
               account_name, status, purchase_id, plan_id
        FROM subs
    """
    queries = {
        "all": base + " ORDER BY id DESC LIMIT ? OFFSET ?",
        "available": base + " WHERE used=0 ORDER BY id DESC LIMIT ? OFFSET ?",
        "delivered": base + " WHERE used=1 ORDER BY id DESC LIMIT ? OFFSET ?",
        "disabled": base + " WHERE status='disabled' ORDER BY id DESC LIMIT ? OFFSET ?",
    }
    query = queries.get(kind, queries["all"])
    with db.LOCK:
        db.cur.execute(query, (int(limit), int(offset)))
        return db.cur.fetchall()


def search_links(query, limit=15):
    query = (query or "").strip()
    if not query:
        return []
    like = f"%{query}%"

    with db.LOCK:
        if query.isdigit():
            db.cur.execute(
                """
                SELECT id, link, used, owner, added_at, assigned_at, price_paid,
                       account_name, status, purchase_id, plan_id
                FROM subs
                WHERE id=? OR owner=? OR link LIKE ? OR account_name LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(query), query, like, like, int(limit)),
            )
        else:
            db.cur.execute(
                """
                SELECT id, link, used, owner, added_at, assigned_at, price_paid,
                       account_name, status, purchase_id, plan_id
                FROM subs
                WHERE link LIKE ? OR account_name LIKE ? OR owner LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (like, like, like, int(limit)),
            )
        return db.cur.fetchall()


def delete_available_link(link_id):
    """Delete only an unassigned row so purchase history cannot be orphaned."""
    link_id = int(link_id)
    with db.LOCK:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            db.cur.execute("SELECT id, used FROM subs WHERE id=?", (link_id,))
            row = db.cur.fetchone()
            if not row:
                db.conn.rollback()
                return False, "not_found"
            if int(row["used"] or 0) == 1:
                db.conn.rollback()
                return False, "already_delivered"

            db.cur.execute("DELETE FROM subs WHERE id=? AND used=0", (link_id,))
            changed = db.cur.rowcount == 1
            db.conn.commit()
            return (True, "deleted") if changed else (False, "not_deleted")
        except Exception:
            db.conn.rollback()
            raise


def return_delivered_link_to_pool(link_id, admin_id=None, reason=""):
    """Return the same subscription row to its plan pool without duplication."""
    link_id = int(link_id)
    reason = (reason or "manual_admin_return").strip()[:500]

    with db.LOCK:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            db.cur.execute(
                """
                SELECT id, link, used, owner, assigned_at, price_paid,
                       account_name, status, purchase_id, plan_id
                FROM subs
                WHERE id=?
                """,
                (link_id,),
            )
            row = db.cur.fetchone()
            if not row:
                db.conn.rollback()
                return False, "not_found", None
            if int(row["used"] or 0) != 1:
                db.conn.rollback()
                return False, "not_delivered", row

            old_owner = row["owner"]
            purchase_id = row["purchase_id"]
            plan_id = row["plan_id"]

            db.cur.execute(
                """
                UPDATE subs
                SET used=0,
                    owner=NULL,
                    assigned_at=NULL,
                    price_paid=NULL,
                    status='available',
                    purchase_id=NULL
                WHERE id=? AND used=1
                """,
                (link_id,),
            )
            if db.cur.rowcount != 1:
                db.conn.rollback()
                return False, "not_updated", row

            if old_owner:
                db.cur.execute(
                    """
                    UPDATE users
                    SET purchased=CASE WHEN purchased > 0 THEN purchased - 1 ELSE 0 END
                    WHERE id=?
                    """,
                    (str(old_owner),),
                )
                owner = db.get_user(old_owner)
                is_test = int(owner["is_test"] or 0) if owner and "is_test" in owner.keys() else 0
                db.cur.execute(
                    """
                    INSERT INTO ledger(
                        user_id, action, amount, balance_before, balance_after, note, is_test
                    ) VALUES (?, 'admin_return_sub_to_pool', 0, NULL, NULL, ?, ?)
                    """,
                    (
                        str(old_owner),
                        f"sub_id={link_id};purchase_id={purchase_id or '-'};"
                        f"admin_id={admin_id or '-'};reason={reason}",
                        is_test,
                    ),
                )

            db.cur.execute(
                """
                UPDATE purchase_items
                SET status='returned_to_pool',
                    reverted_at=datetime('now'),
                    reverted_by=?,
                    revert_reason=?
                WHERE sub_id=?
                  AND (? IS NULL OR purchase_id=?)
                  AND COALESCE(status, 'active') != 'returned_to_pool'
                """,
                (
                    str(admin_id) if admin_id is not None else None,
                    reason,
                    link_id,
                    purchase_id,
                    purchase_id,
                ),
            )
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise

    db.set_low_stock_alerted(False)
    if plan_id:
        db.set_plan_low_stock_alerted(plan_id, False)
    return True, "returned", row


def get_link_detail(link_id):
    with db.LOCK:
        db.cur.execute(
            """
            SELECT id, link, used, owner, added_at, assigned_at, price_paid,
                   account_name, status, purchase_id, plan_id
            FROM subs
            WHERE id=?
            """,
            (int(link_id),),
        )
        return db.cur.fetchone()
