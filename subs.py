"""Subscription-link pool management.

Core invariants:
- an exact link is stored once;
- an available row can be assigned only once;
- returning a service reuses the same row instead of creating a duplicate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time

import aiohttp

import db
from config import (
    YOUPANEL_BASE_URL,
    YOUPANEL_INBOUNDS_JSON,
    YOUPANEL_PASSWORD,
    YOUPANEL_TIMEOUT_SECONDS,
    YOUPANEL_TRIAL_MAX_DEVICES,
    YOUPANEL_USERNAME,
    YOUPANEL_VERIFY_SSL,
    youpanel_configured,
)

logger = logging.getLogger(__name__)


def _generate_account_name():
    return db.generate_service_code()


def get_sub(plan_id=None):
    plan_id = int(plan_id) if plan_id is not None else db.default_plan_id()
    with db.LOCK:
        db.cur.execute(
            """
            SELECT id, link, account_name, status, price_paid, assigned_at,
                   owner, purchase_id, plan_id, source_type, panel_username, is_trial
            FROM subs
            WHERE used=0 AND plan_id=? AND COALESCE(source_type,'pool')='pool'
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
                   owner, purchase_id, used, plan_id, source_type, panel_provider,
                   panel_username, panel_status, panel_data_limit, panel_used_traffic,
                   panel_expires_at, panel_duration_seconds, is_trial, last_synced_at
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
    plan = db.get_plan(plan_id)
    if not plan:
        raise ValueError("plan not found")
    if db.plan_delivery_type(plan) != "pool":
        raise ValueError("panel plans do not accept pooled links")

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
    plan = db.get_plan(plan_id)
    if not plan:
        raise ValueError("plan not found")
    if db.plan_delivery_type(plan) != "pool":
        raise ValueError("panel plans do not accept pooled links")

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
            db.cur.execute("SELECT COUNT(*) AS c FROM subs WHERE used=0 AND COALESCE(source_type,'pool')='pool'")
        else:
            db.cur.execute(
                "SELECT COUNT(*) AS c FROM subs WHERE used=0 AND plan_id=? AND COALESCE(source_type,'pool')='pool'",
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
               purchase_id, used, plan_id, source_type, panel_provider, panel_username,
               panel_status, panel_data_limit, panel_used_traffic, panel_expires_at,
               panel_duration_seconds, is_trial, last_synced_at
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
            WHERE COALESCE(source_type,'pool')='pool'
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
               account_name, status, purchase_id, plan_id, source_type
        FROM subs
    """
    queries = {
        "all": base + " WHERE COALESCE(source_type,'pool')='pool' ORDER BY id DESC LIMIT ? OFFSET ?",
        "available": base + " WHERE COALESCE(source_type,'pool')='pool' AND used=0 ORDER BY id DESC LIMIT ? OFFSET ?",
        "delivered": base + " WHERE COALESCE(source_type,'pool')='pool' AND used=1 ORDER BY id DESC LIMIT ? OFFSET ?",
        "disabled": base + " WHERE COALESCE(source_type,'pool')='pool' AND status='disabled' ORDER BY id DESC LIMIT ? OFFSET ?",
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
                       account_name, status, purchase_id, plan_id, source_type
                FROM subs
                WHERE COALESCE(source_type,'pool')='pool' AND (id=? OR owner=? OR link LIKE ? OR account_name LIKE ?)
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
                WHERE COALESCE(source_type,'pool')='pool' AND (link LIKE ? OR account_name LIKE ? OR owner LIKE ?)
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
            db.cur.execute("SELECT id, used, source_type FROM subs WHERE id=?", (link_id,))
            row = db.cur.fetchone()
            if not row:
                db.conn.rollback()
                return False, "not_found"
            if (row["source_type"] or "pool") != "pool":
                db.conn.rollback()
                return False, "not_pool"
            if int(row["used"] or 0) == 1:
                db.conn.rollback()
                return False, "already_delivered"

            db.cur.execute("DELETE FROM subs WHERE id=? AND used=0 AND COALESCE(source_type,'pool')='pool'", (link_id,))
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
                       account_name, status, purchase_id, plan_id, source_type
                FROM subs
                WHERE id=?
                """,
                (link_id,),
            )
            row = db.cur.fetchone()
            if not row:
                db.conn.rollback()
                return False, "not_found", None
            if (row["source_type"] or "pool") != "pool":
                db.conn.rollback()
                return False, "not_pool", row
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


# -------------------- YouPanel integration --------------------

class YouPanelError(RuntimeError):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


_panel_token: str | None = None
_panel_token_lock = asyncio.Lock()


def _panel_ssl():
    return None if YOUPANEL_VERIFY_SSL else False


def _panel_inbounds() -> dict:
    try:
        value = json.loads(YOUPANEL_INBOUNDS_JSON or "{}")
    except json.JSONDecodeError as exc:
        raise YouPanelError("invalid_inbounds", "YOUPANEL_INBOUNDS_JSON معتبر نیست.") from exc
    if not isinstance(value, dict) or not value:
        raise YouPanelError("invalid_inbounds", "حداقل یک inbound برای پنل تنظیم کنید.")
    return value


def panel_is_configured() -> bool:
    return youpanel_configured()


async def _panel_login(force: bool = False) -> str:
    global _panel_token
    if not panel_is_configured():
        raise YouPanelError("not_configured", "اتصال YouPanel در متغیرهای محیطی تنظیم نشده است.")
    if _panel_token and not force:
        return _panel_token
    async with _panel_token_lock:
        if _panel_token and not force:
            return _panel_token
        timeout = aiohttp.ClientTimeout(total=YOUPANEL_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{YOUPANEL_BASE_URL}/api/admin/token",
                    data={"username": YOUPANEL_USERNAME, "password": YOUPANEL_PASSWORD, "grant_type": "password"},
                    ssl=_panel_ssl(),
                ) as response:
                    payload = await _read_json(response)
                    if response.status != 200:
                        raise YouPanelError("login_failed", "ورود به YouPanel ناموفق بود.", response.status)
        except YouPanelError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise YouPanelError("network", "ارتباط با YouPanel برقرار نشد.") from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise YouPanelError("invalid_login_response", "پاسخ ورود YouPanel توکن معتبر ندارد.")
        _panel_token = str(token)
        return _panel_token


async def _read_json(response: aiohttp.ClientResponse):
    try:
        return await response.json(content_type=None)
    except Exception:
        text = await response.text()
        return {"detail": text[:500]}


async def _panel_request(method: str, path: str, *, json_body=None, params=None, retry_auth=True):
    token = await _panel_login()
    timeout = aiohttp.ClientTimeout(total=YOUPANEL_TIMEOUT_SECONDS)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                f"{YOUPANEL_BASE_URL}{path}",
                headers=headers,
                json=json_body,
                params=params,
                ssl=_panel_ssl(),
            ) as response:
                payload = await _read_json(response)
                status = response.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise YouPanelError("network", "ارتباط با YouPanel قطع یا زمان‌بر شد.") from exc
    if status == 401 and retry_auth:
        await _panel_login(force=True)
        return await _panel_request(method, path, json_body=json_body, params=params, retry_auth=False)
    if status < 200 or status >= 300:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise YouPanelError("api_error", f"خطای YouPanel: {detail or status}", status)
    if not isinstance(payload, dict):
        raise YouPanelError("invalid_response", "پاسخ YouPanel ساختار معتبر ندارد.", status)
    return payload


def _clean_panel_username(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value or "").strip("-_")
    return value[:48] or "bsv-user"


def panel_username_for_order(user_id, purchase_id, index: int) -> str:
    return _clean_panel_username(f"bsv-{user_id}-{purchase_id}-{index}")


def panel_trial_username(user_id) -> str:
    return _clean_panel_username(f"trial-{user_id}")


def _panel_user_payload(username: str, data_limit_bytes: int, duration_days: int, start_mode="on_hold", reset_strategy="no_reset", max_devices=None):
    duration_seconds = max(1, int(duration_days)) * 86400
    active = start_mode == "active"
    return {
        "username": _clean_panel_username(username),
        "status": "active" if active else "on_hold",
        "expire": int(time.time()) + duration_seconds if active else None,
        "on_hold_expire_duration": None if active else duration_seconds,
        "data_limit": max(1, int(data_limit_bytes)),
        "data_limit_reset_strategy": reset_strategy or "no_reset",
        "inbounds": _panel_inbounds(),
        "proxies": {"vless": {"flow": ""}},
        "note": "created-by-berserk-bot",
        "backup_outbound_tags": [],
        "primary_outbound_tag": None,
        "routing_mode": "manual",
        "single_device_mode": "off",
        "max_devices": int(max_devices) if max_devices not in (None, "") else None,
        "user_location_label": None,
    }


async def panel_create_user(username: str, data_limit_bytes: int, duration_days: int, start_mode="on_hold", reset_strategy="no_reset", max_devices=None):
    payload = _panel_user_payload(username, data_limit_bytes, duration_days, start_mode, reset_strategy, max_devices)
    result = await _panel_request("POST", "/api/user", json_body=payload)
    if not result.get("subscription_url") or not result.get("username"):
        raise YouPanelError("invalid_create_response", "پنل سرویس را ساخت اما لینک اشتراک برنگرداند.")
    return result


async def panel_delete_user(username: str):
    return await _panel_request("DELETE", f"/api/user/{_clean_panel_username(username)}")


async def panel_reset_usage(username: str):
    return await _panel_request("POST", f"/api/user/{_clean_panel_username(username)}/reset")


async def panel_revoke_subscription(username: str):
    result = await _panel_request("POST", f"/api/user/{_clean_panel_username(username)}/revoke_sub")
    if not result.get("subscription_url"):
        raise YouPanelError("invalid_revoke_response", "پنل لینک اشتراک جدید برنگرداند.")
    return result


async def panel_usage(username: str, start: str = "1970-01-01T00:00:00"):
    return await _panel_request("GET", f"/api/user/{_clean_panel_username(username)}/usage", params={"start": start})


async def panel_health_check():
    return await _panel_request("GET", "/api/admin")


async def provision_panel_purchase(user_id, quantity, plan_id, unit_price=None, note=""):
    plan = db.get_plan(plan_id)
    if not plan:
        raise db.PurchaseError("plan_not_found", "پلن پیدا نشد.")
    reservation = db.begin_panel_purchase(user_id, quantity, unit_price, note=note, plan_id=plan_id)
    created = []
    attempted_usernames = []
    try:
        for index in range(1, int(quantity) + 1):
            panel_username = panel_username_for_order(user_id, reservation["purchase_id"], index)
            attempted_usernames.append(panel_username)
            panel_item = await panel_create_user(
                panel_username,
                int(plan["panel_data_limit_bytes"] or 0),
                int(plan["panel_duration_days"] or 0),
                plan["panel_start_mode"] or "on_hold",
                plan["panel_reset_strategy"] or "no_reset",
                plan["panel_max_devices"],
            )
            panel_item["account_name"] = db.generate_service_code()
            created.append(panel_item)
        finalized = db.finalize_panel_purchase(reservation["purchase_id"], created)
        purchase = finalized["purchase"]
        return {
            "purchase_id": int(purchase["id"]),
            "quantity": int(purchase["quantity"]),
            "unit_price": int(purchase["unit_price"]),
            "amount": int(purchase["amount"]),
            "balance_before": reservation["balance_before"],
            "balance_after": reservation["balance_after"],
            "is_test": int(purchase["is_test"] or 0),
            "items": finalized["items"],
        }
    except Exception as exc:
        cleanup_errors = []
        for panel_username in dict.fromkeys(attempted_usernames):
            try:
                await panel_delete_user(panel_username)
            except YouPanelError as cleanup_exc:
                if cleanup_exc.status != 404:
                    cleanup_errors.append(cleanup_exc.message)
            except Exception as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
        detail = str(exc)
        if cleanup_errors:
            detail += "; cleanup_failed=" + " | ".join(cleanup_errors)
        db.refund_panel_purchase(reservation["purchase_id"], detail)
        if isinstance(exc, db.PurchaseError):
            raise
        if isinstance(exc, YouPanelError):
            raise db.PurchaseError("panel_error", exc.message) from exc
        raise db.PurchaseError("panel_error", "ساخت خودکار سرویس ناموفق بود و مبلغ به کیف پول برگشت.") from exc


async def create_trial_service(user_id, size_mb: int, days: int):
    username = panel_trial_username(user_id)
    ok, reason, claim = db.begin_trial_claim(user_id, username)
    if not ok:
        if reason == "already_claimed":
            raise YouPanelError("already_claimed", "برای این حساب قبلاً اکانت تست ثبت شده است.")
        raise YouPanelError(reason, "امکان شروع اکانت تست وجود ندارد.")
    try:
        item = await panel_create_user(
            username,
            int(size_mb) * 1024 * 1024,
            int(days),
            "on_hold",
            max_devices=YOUPANEL_TRIAL_MAX_DEVICES,
        )
        return db.complete_trial_claim(user_id, item)
    except Exception as exc:
        try:
            await panel_delete_user(username)
        except YouPanelError as cleanup_exc:
            if cleanup_exc.status != 404:
                logger.warning("trial cleanup failed for %s: %s", username, cleanup_exc.message)
        except Exception:
            logger.warning("trial cleanup failed for %s", username, exc_info=True)
        db.fail_trial_claim(user_id, str(exc))
        raise


async def recover_stale_panel_purchases(minutes: int = 15):
    """Best-effort cleanup and refund for interrupted provisioning orders."""
    recovered = []
    for purchase in db.list_stale_panel_purchases(minutes):
        cleanup_errors = []
        for index in range(1, int(purchase["quantity"] or 0) + 1):
            username = panel_username_for_order(purchase["user_id"], purchase["id"], index)
            try:
                await panel_delete_user(username)
            except YouPanelError as exc:
                # A 404 means there was no orphan account to delete. Other
                # failures are recorded but the wallet is still refunded.
                if exc.status != 404:
                    cleanup_errors.append(exc.message)
        detail = "startup_recovery"
        if cleanup_errors:
            detail += "; cleanup=" + " | ".join(cleanup_errors)
        ok, _, _ = db.refund_panel_purchase(purchase["id"], detail)
        if ok:
            recovered.append(int(purchase["id"]))
    return recovered

async def recover_stale_trial_claims(minutes: int = 15):
    """Delete deterministic orphan trial users and reopen failed claims."""
    recovered = []
    for claim in db.list_stale_trial_claims(minutes):
        username = claim["panel_username"]
        error = "startup_trial_recovery"
        try:
            await panel_delete_user(username)
        except YouPanelError as exc:
            if exc.status != 404:
                error += f"; cleanup={exc.message}"
        except Exception as exc:
            error += f"; cleanup={str(exc)[:200]}"
        if db.fail_trial_claim(claim["user_id"], error):
            recovered.append(str(claim["user_id"]))
    return recovered

