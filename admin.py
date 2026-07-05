import asyncio
import logging
import os
import sys
from datetime import datetime

from aiogram import Bot, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import backup
import db
import menus
import messages
import settings
import subs
from config import ADMIN_COMMAND, ADMIN_IDS, BROADCAST_DELAY, OWNER_IDS
from utils import cleanup_qr, make_qr, format_dual_datetime


def is_admin(user_id) -> bool:
    return int(user_id) in ADMIN_IDS


def is_owner(user_id) -> bool:
    return int(user_id) in OWNER_IDS


class AdminStates(StatesGroup):
    waiting_search = State()
    waiting_balance_id = State()
    waiting_balance_amount = State()
    waiting_ban_id = State()
    waiting_unban_id = State()
    waiting_user_note = State()
    waiting_direct_message = State()
    waiting_add_sub = State()
    waiting_link_search = State()
    waiting_link_delete_id = State()
    waiting_setting_value = State()
    waiting_message_edit = State()
    waiting_restore_file = State()
    waiting_restore_confirm = State()
    waiting_custom_button_form = State()
    waiting_custom_button_title = State()
    waiting_custom_button_payload = State()
    waiting_button_order = State()
    waiting_button_location = State()
    waiting_system_button_title = State()
    waiting_system_button_order = State()
    waiting_system_button_location = State()
    waiting_plan_form = State()
    waiting_broadcast_content = State()
    waiting_broadcast_confirm = State()


def cancel_kb():
    return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))


def admin_back_kb():
    return menus.admin_back_inline()


def admin_menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👥 کاربران", callback_data="adm_section_users"),
        InlineKeyboardButton("📦 سرویس‌ها و پلن‌ها", callback_data="adm_section_services"),
        InlineKeyboardButton("💰 مالی و پرداخت‌ها", callback_data="adm_section_finance"),
        InlineKeyboardButton("🎫 تیکت‌ها", callback_data="adm_tickets"),
        InlineKeyboardButton("🎛 شخصی‌سازی", callback_data="adm_section_personalize"),
        InlineKeyboardButton("📊 گزارش‌ها", callback_data="adm_section_reports"),
        InlineKeyboardButton("💾 بک‌آپ و امنیت", callback_data="adm_backup_menu"),
        InlineKeyboardButton("⚙️ تنظیمات", callback_data="adm_settings"),
    )
    kb.add(InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def admin_users_section_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👤 آخرین کاربران", callback_data="adm_users"),
        InlineKeyboardButton("🔎 جستجوی کاربر", callback_data="adm_search"),
        InlineKeyboardButton("💰 تغییر موجودی", callback_data="adm_addbal"),
        InlineKeyboardButton("⛔ بن کاربر", callback_data="adm_ban"),
        InlineKeyboardButton("✅ آن‌بن کاربر", callback_data="adm_unban"),
    )
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def admin_services_section_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🏷 مدیریت پلن‌ها", callback_data="adm_plans"),
        InlineKeyboardButton("🔗 مدیریت لینک‌ها", callback_data="adm_links"),
        InlineKeyboardButton("➕ افزودن لینک", callback_data="adm_link_add"),
        InlineKeyboardButton("🔎 جستجوی لینک", callback_data="adm_link_search"),
    )
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def admin_finance_section_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("💳 شارژهای در انتظار", callback_data="adm_topups"))
    kb.add(InlineKeyboardButton("⚙️ تنظیمات مالی و فروشگاه", callback_data="adm_settings"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def admin_personalize_section_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🎛 مدیریت دکمه‌ها", callback_data="adm_buttons"))
    kb.add(InlineKeyboardButton("📝 مدیریت پیام‌ها", callback_data="adm_messages"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def admin_reports_section_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📊 آمار و درآمد", callback_data="adm_stats"))
    kb.add(InlineKeyboardButton("💹 گزارش فروش سریع", callback_data="adm_sales_report"))
    kb.add(InlineKeyboardButton("🧾 لاگ عملیات ادمین", callback_data="adm_admin_logs"))
    kb.add(InlineKeyboardButton("📢 پیام همگانی", callback_data="adm_broadcast"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def _fmt_money(amount):
    return f"{int(amount or 0):,} تومان"


def _short(value, size=45):
    value = value or "-"
    if len(value) <= size:
        return value
    return value[:size] + "..."

def _display_username(user):
    username = user["username"] if user and "username" in user.keys() else ""
    display_name = user["display_name"] if user and "display_name" in user.keys() else ""
    if username:
        return f"@{username}"
    if display_name:
        return f"{display_name} (بدون یوزرنیم)"
    return "بدون یوزرنیم"


def _user_button_label(row, index=None):
    prefix = f"{index}. " if index is not None else ""
    test_mark = "🧪 " if "is_test" in row.keys() and int(row["is_test"] or 0) else ""
    username = row["username"] if row["username"] else ""
    display_name = row["display_name"] if "display_name" in row.keys() else ""
    if username:
        name = f"@{username}"
    elif display_name:
        name = f"{display_name} | بدون یوزرنیم"
    else:
        name = "بدون یوزرنیم"
    return f"{prefix}👤 {test_mark}{name} | {row['id']}"


def _dual(value):
    return format_dual_datetime(value)


async def _send_long(message, text, reply_markup=None):
    chunks = []
    while len(text) > 3900:
        split_at = text.rfind("\n", 0, 3900)
        if split_at == -1:
            split_at = 3900
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    chunks.append(text)

    for index, chunk in enumerate(chunks):
        await message.answer(chunk, reply_markup=reply_markup if index == len(chunks) - 1 else None)

async def _safe_remove_inline_keyboard(message):
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


async def _safe_delete_message(message):
    try:
        await message.delete()
        return True
    except Exception:
        return False


async def _replace_callback_message(c: types.CallbackQuery, text: str, reply_markup=None, parse_mode=None):
    """
    برای جلوگیری از شلوغ شدن پنل:
    - اگر پیام قابل ویرایش باشد، همان پیام edit می‌شود.
    - اگر قابل edit نباشد، پیام قبلی حذف و پیام جدید ارسال می‌شود.
    """
    try:
        if getattr(c.message, "content_type", None) == types.ContentType.TEXT:
            await c.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
    except Exception as exc:
        if "message is not modified" in str(exc).lower():
            return

    deleted = await _safe_delete_message(c.message)

    if not deleted:
        await _safe_remove_inline_keyboard(c.message)

    await c.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


def user_detail_kb(user_id):
    kb = InlineKeyboardMarkup(row_width=1)
    # ساخت دکمه URL با tg://user?id برای بعضی کاربران توسط تلگرام با
    # BUTTON_USER_PRIVACY_RESTRICTED رد می‌شود و کل پیام را fail می‌کند.
    # به همین دلیل پروفایل را با callback امن نمایش می‌دهیم و URL خام داخل متن می‌آید.
    kb.add(InlineKeyboardButton("👁 اطلاعات پروفایل", callback_data=f"adm_user_profile_{user_id}"))
    kb.add(InlineKeyboardButton("💬 ارسال پیام به کاربر", callback_data=f"adm_msg_user_{user_id}"))
    kb.add(InlineKeyboardButton("📝 یادداشت ادمین", callback_data=f"adm_user_note_{user_id}"))
    kb.add(InlineKeyboardButton("🧪 تغییر وضعیت کاربر تست", callback_data=f"adm_user_test_{user_id}"))
    kb.add(InlineKeyboardButton("🔄 بروزرسانی جزئیات", callback_data=f"adm_user_{user_id}"))
    kb.add(InlineKeyboardButton("💳 افزایش / کاهش موجودی", callback_data="adm_addbal"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به بخش کاربران", callback_data="adm_section_users"))
    kb.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm_back"))
    return kb


def user_services_kb(user_id):
    rows = subs.user_subs(user_id, limit=8)
    kb = InlineKeyboardMarkup(row_width=2)

    for index, row in enumerate(rows, start=1):
        label = row["account_name"] or f"Sub #{row['id']}"
        kb.add(
            InlineKeyboardButton(f"{index}. 🔗 ارسال لینک {label}", callback_data=f"adm_resend_link_{row['id']}_{user_id}"),
            InlineKeyboardButton(f"{index}. 🔳 QR {label}", callback_data=f"adm_resend_qr_{row['id']}_{user_id}"),
        )

    kb.add(InlineKeyboardButton("⬅️ بازگشت به جزئیات", callback_data=f"adm_user_{user_id}"))
    return kb


def _fmt_user_detail(user_id):
    user = db.get_user(user_id)
    if not user:
        return "کاربر پیدا نشد."

    status = "⛔ بن شده" if user["banned"] else "✅ فعال"
    username_text = _display_username(user)
    test_text = "🧪 کاربر تست" if "is_test" in user.keys() and int(user["is_test"] or 0) else "عادی"
    admin_note = user["admin_note"] if "admin_note" in user.keys() and user["admin_note"] else "-"
    owned = subs.user_subs(user_id, limit=20)
    purchases = db.list_user_purchases(user_id, limit=10)
    ledger = db.list_user_ledger(user_id, limit=10)
    topups = db.list_user_topups(user_id, limit=8)
    tickets = db.list_user_tickets(user_id, limit=5)
    ticket_counts = db.user_ticket_counts(user_id)
    referred = db.referred_users(user_id, limit=8)

    refs_count = db.referral_count(user_id)
    rewarded_refs = db.rewarded_referral_count(user_id)
    ref_reward_total = db.referral_reward_total(user_id)
    referrer_text = user["ref"] or "-"

    text = (
        f"👤 جزئیات کامل کاربر\n\n"
        f"User ID: {user['id']}\n"
        f"Username: @{user['username'] or '-'}\n"
        f"نمایش: {username_text}\n"
        f"نوع کاربر: {test_text}\n"
        f"یادداشت ادمین: {admin_note}\n"
        f"وضعیت: {status}\n"
        f"موجودی کیف پول: {_fmt_money(user['balance'])}\n"
        f"تعداد خرید ثبت‌شده روی کاربر: {user['purchased']}\n"
        f"تعداد سرویس تحویل‌شده: {db.delivered_sub_count_by_user(user_id)}\n"
        f"معرف: {referrer_text}\n"
        f"عضویت: {_dual(user['joined_at'])}\n"
        f"آخرین فعالیت: {_dual(user['last_active'])}\n"
    )

    text += (
        f"\n👥 رفرال\n"
        f"تعداد زیرمجموعه‌ها: {refs_count}\n"
        f"زیرمجموعه‌های خریدکرده/پاداش‌داده‌شده: {rewarded_refs}\n"
        f"مجموع پاداش رفرال دریافتی: {_fmt_money(ref_reward_total)}\n"
    )

    if referred:
        text += "آخرین زیرمجموعه‌ها:\n"
        for row in referred:
            mark = "✅ خرید کرده" if row["rewarded"] else "⏳ بدون خرید"
            text += f"• {row['id']} {_display_username(row)} | {mark} | خرید: {row['purchased']} | عضویت: {_dual(row['joined_at'])}\n"

    text += "\n🧾 خریدها\n"
    if purchases:
        for p in purchases[:7]:
            text += (
                f"• خرید #{p['id']} | تعداد {p['quantity']} | "
                f"مبلغ {_fmt_money(p['amount'])} | قیمت واحد {_fmt_money(p['unit_price'])} | {_dual(p['created_at'])}\n"
            )
    else:
        text += "خرید ثبت نشده.\n"

    text += "\n📦 سرویس‌های تحویل‌شده\n"
    if owned:
        for s in owned[:12]:
            text += (
                f"• Sub #{s['id']} | {s['account_name'] or '-'}\n"
                f"  خرید/تحویل: {_dual(s['assigned_at'])} | مبلغ: {_fmt_money(s['price_paid'])}\n"
                f"  وضعیت: {s['status'] or 'delivered'} | خرید #{s['purchase_id'] or '-'}\n"
                f"  لینک کوتاه: {_short(s['link'])}\n"
            )
    else:
        text += "سرویسی به این کاربر تحویل نشده.\n"

    text += "\n💳 شارژهای کیف پول\n"
    if topups:
        for t in topups:
            text += f"• شارژ #{t['id']} | {_fmt_money(t['amount'])} | {t['status']} | {_dual(t['created_at'])}\n"
    else:
        text += "شارژ ثبت نشده.\n"

    text += "\n📒 آخرین تراکنش‌های کیف پول\n"
    if ledger:
        for l in ledger:
            text += (
                f"• #{l['id']} | {l['action']} | {_fmt_money(l['amount'])}\n"
                f"  قبل: {_fmt_money(l['balance_before'])} | بعد: {_fmt_money(l['balance_after'])} | {_dual(l['created_at'])}\n"
            )
    else:
        text += "تراکنش ثبت نشده.\n"

    text += (
        f"\n🎫 پشتیبانی\n"
        f"کل تیکت‌ها: {ticket_counts['total']} | باز: {ticket_counts['open']} | بسته: {ticket_counts['closed']}\n"
    )
    if tickets:
        text += "آخرین تیکت‌ها:\n"
        for t in tickets:
            text += f"• تیکت #{t['id']} | {t['status']} | {_dual(t['created_at'])}\n"

    text += (
        "\nℹ️ نکته: چون API پنل VPN نداریم، تاریخ اولین اتصال یا مصرف واقعی قابل تشخیص نیست؛ "
        "اینجا تاریخ خرید/تحویل لینک نمایش داده می‌شود."
    )
    return text


async def cmd_admin(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("⚙️ پنل مدیریت Berserk VPN", reply_markup=admin_menu_kb())


async def cb_open_panel(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(c, "⚙️ پنل مدیریت Berserk VPN", reply_markup=admin_menu_kb())


async def cb_back(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(c, "⚙️ پنل مدیریت Berserk VPN", reply_markup=admin_menu_kb())


async def cb_section_users(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(c, "👥 بخش کاربران\n\nجستجو، بن/آن‌بن، موجودی و جزئیات کاربران از این بخش مدیریت می‌شود.", reply_markup=admin_users_section_kb())


async def cb_section_services(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(c, "📦 سرویس‌ها و پلن‌ها\n\nپلن‌ها، استخر لینک‌ها و موجودی هر پلن از این بخش مدیریت می‌شود.", reply_markup=admin_services_section_kb())


async def cb_section_finance(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(c, "💰 مالی و پرداخت‌ها", reply_markup=admin_finance_section_kb())


async def cb_section_personalize(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(c, "🎛 شخصی‌سازی ربات\n\nمدیریت دکمه‌ها و متن‌های قابل ویرایش از این بخش انجام می‌شود.", reply_markup=admin_personalize_section_kb())


async def cb_section_reports(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(c, "📊 گزارش‌ها و ارسال همگانی", reply_markup=admin_reports_section_kb())


async def cb_users(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    rows = db.list_users(limit=15)

    if not rows:
        return await _replace_callback_message(c, "هیچ کاربری ثبت نشده.", reply_markup=admin_back_kb())

    lines = ["👥 کاربران بر اساس زمان عضویت؛ از قدیمی‌ترین تا جدیدترین:\n"]
    kb = InlineKeyboardMarkup(row_width=1)

    for index, r in enumerate(rows, start=1):
        flag = "⛔" if r["banned"] else "✅"
        username = _display_username(r)
        delivered = db.delivered_sub_count_by_user(r["id"])
        test_mark = " | 🧪 تست" if "is_test" in r.keys() and int(r["is_test"] or 0) else ""
        lines.append(
            f"{index}. {flag} {r['id']} | {username}{test_mark} | خرید: {r['purchased']} | سرویس: {delivered} | موجودی: {_fmt_money(r['balance'])} | عضویت: {_dual(r['joined_at'])}"
        )
        kb.add(InlineKeyboardButton(_user_button_label(r, index), callback_data=f"adm_user_{r['id']}"))

    kb.add(InlineKeyboardButton("⬅️ بازگشت به بخش کاربران", callback_data="adm_section_users"))
    await _replace_callback_message(c, "\n".join(lines), reply_markup=kb)


async def cb_user_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_", 1)[1]
    await _send_long(c.message, _fmt_user_detail(user_id), reply_markup=user_detail_kb(user_id))
    if subs.user_subs(user_id, limit=1):
        await c.message.answer("🔁 عملیات سریع روی سرویس‌های این کاربر:", reply_markup=user_services_kb(user_id))


async def cb_user_profile_info(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_profile_", 1)[1]
    user = db.get_user(user_id)
    if not user:
        return await c.message.answer("کاربر پیدا نشد.", reply_markup=admin_back_kb())

    username = f"@{user['username']}" if user['username'] else "ندارد"
    display = _display_username(user)
    text = (
        "👁 اطلاعات دسترسی به پروفایل کاربر\n\n"
        f"نمایش: {display}\n"
        f"یوزرنیم: {username}\n"
        f"شناسه عددی: {user_id}\n\n"
        "برای کاربران بدون یوزرنیم، مطمئن‌ترین شناسه همین Telegram ID است.\n"
        "اگر کلاینت تلگرام اجازه بدهد، می‌توانید این لینک داخلی را کپی و باز کنید:\n"
        f"tg://user?id={user_id}\n\n"
        "اگر لینک باز نشد، یعنی محدودیت حریم خصوصی/کلاینت تلگرام اجازه نمایش مستقیم نمی‌دهد. "
        "در این حالت از دکمه «💬 ارسال پیام به کاربر» استفاده کنید."
    )
    await _replace_callback_message(c, text, reply_markup=user_detail_kb(user_id))


async def cb_user_note(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_note_", 1)[1]
    user = db.get_user(user_id)
    if not user:
        return await c.message.answer("کاربر پیدا نشد.", reply_markup=admin_back_kb())
    current = user["admin_note"] if "admin_note" in user.keys() and user["admin_note"] else ""
    await state.update_data(note_user_id=user_id)
    await _replace_callback_message(
        c,
        "📝 یادداشت ادمین برای کاربر\n\n"
        f"کاربر: {_display_username(user)} | ID: {user_id}\n"
        f"یادداشت فعلی:\n{current or '-'}\n\n"
        "متن یادداشت جدید را بفرستید. برای پاک کردن یادداشت، فقط یک خط تیره - بفرستید.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_user_note.set()


async def process_user_note(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفاً یادداشت را به صورت متن بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    user_id = data.get("note_user_id")
    note = "" if m.text.strip() == "-" else m.text.strip()
    db.set_user_admin_note(user_id, note)
    db.log_admin_action(m.from_user.id, "user_note_update", user_id, f"note_len={len(note)}")
    await state.finish()
    await m.answer("✅ یادداشت ادمین ذخیره شد.\n\n" + _fmt_user_detail(user_id), reply_markup=user_detail_kb(user_id))


async def cb_user_test_toggle(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_test_", 1)[1]
    if not db.get_user(user_id):
        return await c.message.answer("کاربر پیدا نشد.", reply_markup=admin_back_kb())
    db.toggle_user_test(user_id)
    user = db.get_user(user_id)
    db.log_admin_action(c.from_user.id, "toggle_test_user", user_id, f"is_test={user['is_test'] if 'is_test' in user.keys() else '-'}")
    await _replace_callback_message(c, "✅ وضعیت کاربر تست تغییر کرد.\n\n" + _fmt_user_detail(user_id), reply_markup=user_detail_kb(user_id))


async def cb_direct_message_start(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_msg_user_", 1)[1]
    user = db.get_user(user_id)
    if not user:
        return await c.message.answer("کاربر پیدا نشد.", reply_markup=admin_back_kb())
    await state.update_data(direct_user_id=user_id)
    await _replace_callback_message(
        c,
        "💬 ارسال پیام مستقیم به کاربر\n\n"
        f"گیرنده: {_display_username(user)} | ID: {user_id}\n\n"
        "متن پیام را بفرستید. پیام از طرف ربات برای کاربر ارسال می‌شود.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_direct_message.set()


async def process_direct_message(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text" or not m.text.strip():
        return await m.answer("لطفاً متن پیام را بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    user_id = data.get("direct_user_id")
    bot = Bot.get_current()
    body = (
        "📩 پیام پشتیبانی\n\n"
        f"{m.text.strip()}\n\n"
        "برای پاسخ، از بخش پشتیبانی ربات استفاده کنید."
    )
    try:
        await bot.send_message(int(user_id), body, reply_markup=menus.main_reply_kb(user_id))
        db.log_admin_action(m.from_user.id, "send_direct_message", user_id, f"len={len(m.text.strip())}")
        await state.finish()
        await m.answer("✅ پیام برای کاربر ارسال شد.", reply_markup=user_detail_kb(user_id))
    except Exception as exc:
        await m.answer(f"❌ ارسال پیام ناموفق بود: {exc}", reply_markup=user_detail_kb(user_id))



async def cb_resend_link(c: types.CallbackQuery):
    bot = Bot.get_current()
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    payload = c.data.replace("adm_resend_link_", "", 1)
    sub_id, user_id = payload.split("_", 1)
    item = subs.get_sub_detail(sub_id)
    if not item or str(item["owner"]) != str(user_id):
        return await c.message.answer("این سرویس برای این کاربر پیدا نشد.", reply_markup=admin_back_kb())
    try:
        await bot.send_message(
            int(user_id),
            f"🔗 ارسال مجدد سرویس شما\n\n"
            f"شناسه سرویس: {item['account_name'] or '-'}\n"
            f"تاریخ خرید/تحویل: {item['assigned_at'] or '-'}\n\n"
            f"لینک:\n{item['link']}",
            reply_markup=menus.main_reply_kb(user_id),
        )
        await c.message.answer("✅ لینک سرویس دوباره برای کاربر ارسال شد.", reply_markup=user_services_kb(user_id))
    except Exception:
        await c.message.answer("❌ ارسال لینک به کاربر ناموفق بود.", reply_markup=user_services_kb(user_id))


async def cb_resend_qr(c: types.CallbackQuery):
    bot = Bot.get_current()
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    payload = c.data.replace("adm_resend_qr_", "", 1)
    sub_id, user_id = payload.split("_", 1)
    item = subs.get_sub_detail(sub_id)
    if not item or str(item["owner"]) != str(user_id):
        return await c.message.answer("این سرویس برای این کاربر پیدا نشد.", reply_markup=admin_back_kb())

    qr_path = make_qr(item["link"], user_id)
    try:
        with open(qr_path, "rb") as qr_file:
            await bot.send_photo(
                int(user_id),
                qr_file,
                caption=(
                    f"🔳 QR سرویس شما\n\n"
                    f"شناسه سرویس: {item['account_name'] or '-'}\n"
                    f"تاریخ خرید/تحویل: {item['assigned_at'] or '-'}"
                ),
                reply_markup=menus.main_reply_kb(user_id),
            )
        await c.message.answer("✅ QR سرویس دوباره برای کاربر ارسال شد.", reply_markup=user_services_kb(user_id))
    except Exception:
        await c.message.answer("❌ ارسال QR به کاربر ناموفق بود.", reply_markup=user_services_kb(user_id))
    finally:
        cleanup_qr(qr_path)


async def cb_search(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(c, "آیدی عددی یا یوزرنیم کاربر رو بفرستید:", reply_markup=cancel_kb())
    await AdminStates.waiting_search.set()


async def process_search(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفا آیدی یا یوزرنیم رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())
    rows = db.search_users(m.text)
    await state.finish()
    if not rows:
        return await m.answer("چیزی پیدا نشد.", reply_markup=admin_back_kb())
    for r in rows[:10]:
        await _send_long(m, _fmt_user_detail(r["id"]), reply_markup=user_detail_kb(r["id"]))
        if subs.user_subs(r["id"], limit=1):
            await m.answer("🔁 عملیات سریع روی سرویس‌های این کاربر:", reply_markup=user_services_kb(r["id"]))


async def cb_addbal(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(c, "آیدی کاربر رو بفرستید:", reply_markup=cancel_kb())
    await AdminStates.waiting_balance_id.set()


async def process_balance_id(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفا آیدی کاربر رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())
    target = m.text.strip()
    if not db.get_user(target):
        return await m.answer("این کاربر پیدا نشد. دوباره بفرستید یا لغو کنید:", reply_markup=cancel_kb())
    await state.update_data(target_id=target)
    await m.answer("چه مبلغی اضافه/کم بشه؟ (برای کسر، عدد منفی بفرستید مثل -10000)", reply_markup=cancel_kb())
    await AdminStates.waiting_balance_amount.set()


async def process_balance_amount(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفا فقط عدد بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    try:
        amount = int(m.text.strip().replace(",", ""))
    except ValueError:
        return await m.answer("لطفا فقط عدد بفرستید.", reply_markup=cancel_kb())
    db.add_balance(data["target_id"], amount, action="admin_adjustment", note=f"admin_id={m.from_user.id}")
    db.log_admin_action(m.from_user.id, "balance_adjustment", data["target_id"], f"amount={amount}")
    await state.finish()
    await m.answer(f"✅ موجودی کاربر {data['target_id']} به‌روزرسانی شد.", reply_markup=admin_back_kb())


async def cb_ban(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(c, "آیدی کاربری که باید بن بشه رو بفرستید:", reply_markup=cancel_kb())
    await AdminStates.waiting_ban_id.set()


async def process_ban(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    target = m.text.strip()
    if not db.get_user(target):
        return await m.answer("این کاربر پیدا نشد. دوباره بفرستید یا لغو کنید:", reply_markup=cancel_kb())
    db.set_ban(target, True)
    db.log_admin_action(m.from_user.id, "ban_user", target, "manual_ban")
    await state.finish()
    await m.answer(f"⛔ کاربر {target} بن شد.", reply_markup=admin_back_kb())


async def cb_unban(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(c, "آیدی کاربری که باید آنبن بشه رو بفرستید:", reply_markup=cancel_kb())
    await AdminStates.waiting_unban_id.set()


async def process_unban(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    target = m.text.strip()
    if not db.get_user(target):
        return await m.answer("این کاربر پیدا نشد. دوباره بفرستید یا لغو کنید:", reply_markup=cancel_kb())
    db.set_ban(target, False)
    db.log_admin_action(m.from_user.id, "unban_user", target, "manual_unban")
    await state.finish()
    await m.answer(f"✅ کاربر {target} آنبن شد.", reply_markup=admin_back_kb())



def link_manager_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ افزودن لینک", callback_data="adm_link_add"),
        InlineKeyboardButton("📦 لینک‌های آزاد", callback_data="adm_links_available"),
        InlineKeyboardButton("✅ لینک‌های تحویل‌شده", callback_data="adm_links_delivered"),
        InlineKeyboardButton("🔎 جستجوی لینک", callback_data="adm_link_search"),
        InlineKeyboardButton("🗑 حذف لینک آزاد", callback_data="adm_link_delete_manual"),
    )
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def link_back_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔗 بازگشت به مدیریت لینک‌ها", callback_data="adm_links"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def _link_status_label(row):
    if not row:
        return "-"

    if int(row["used"] or 0) == 1:
        return "✅ تحویل‌شده"

    if (row["status"] or "") == "disabled":
        return "🚫 غیرفعال"

    return "📦 آزاد"


def _fmt_link_row(row):
    owner = row["owner"] or "-"
    owner_text = owner

    if owner != "-":
        user = db.get_user(owner)
        if user and user["username"]:
            owner_text = f"{owner} (@{user['username']})"

    return (
        f"🔗 Link #{row['id']}\n"
        f"شناسه سرویس: {row['account_name'] or '-'}\n"
        f"وضعیت: {_link_status_label(row)}\n"
        f"مالک: {owner_text}\n"
        f"قیمت فروش: {_fmt_money(row['price_paid'])}\n"
        f"خرید/تحویل: {row['assigned_at'] or '-'}\n"
        f"Purchase ID: {row['purchase_id'] or '-'}\n"
        f"افزوده‌شده: {row['added_at'] or '-'}\n"
        f"لینک کوتاه: {_short(row['link'], 80)}"
    )


def _links_list_text(title, rows):
    if not rows:
        return f"{title}\n\nموردی پیدا نشد."

    text = f"{title}\n\n"

    for row in rows:
        text += (
            f"• #{row['id']} | {row['account_name'] or '-'} | {_link_status_label(row)}\n"
            f"  مالک: {row['owner'] or '-'} | قیمت: {_fmt_money(row['price_paid'])}\n"
            f"  لینک: {_short(row['link'], 65)}\n\n"
        )

    return text.strip()


def _links_list_kb(rows, back_callback="adm_links"):
    kb = InlineKeyboardMarkup(row_width=2)

    for row in rows[:12]:
        kb.insert(InlineKeyboardButton(f"جزئیات #{row['id']}", callback_data=f"adm_link_detail_{row['id']}"))

        if int(row["used"] or 0) == 0:
            kb.insert(InlineKeyboardButton(f"حذف #{row['id']}", callback_data=f"adm_link_delete_ask_{row['id']}"))

    kb.add(InlineKeyboardButton("🔗 بازگشت به مدیریت لینک‌ها", callback_data=back_callback))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


async def cb_links(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    counts = subs.link_counts()

    text = (
        "🔗 مدیریت لینک‌ها\n\n"
        "از این بخش می‌تونی لینک‌های ساب رو مدیریت کنی؛ جزئیات ببینی، لینک جدید اضافه کنی یا لینک آزاد رو حذف کنی.\n\n"
        f"📊 آمار لینک‌ها:\n"
        f"کل لینک‌ها: {counts['total']}\n"
        f"آزاد/قابل فروش: {counts['available']}\n"
        f"تحویل‌شده/فروخته‌شده: {counts['delivered']}\n\n"
        "⚠️ نکته: لینک تحویل‌شده حذف مستقیم نمی‌شود. اگر تحویل اشتباه یا تست بود، از جزئیات لینک گزینه «بازگردانی به استخر» را بزنید."
    )

    await _replace_callback_message(c, text, reply_markup=link_manager_kb())


async def cb_links_available(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    rows = subs.list_links("available", limit=15)
    await _replace_callback_message(
        c,
        _links_list_text("📦 آخرین لینک‌های آزاد", rows),
        reply_markup=_links_list_kb(rows),
    )


async def cb_links_delivered(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    rows = subs.list_links("delivered", limit=15)
    await _replace_callback_message(
        c,
        _links_list_text("✅ آخرین لینک‌های تحویل‌شده", rows),
        reply_markup=_links_list_kb(rows),
    )


async def cb_link_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    link_id = c.data.split("adm_link_detail_", 1)[1]
    row = subs.get_link_detail(link_id)

    if not row:
        return await _replace_callback_message(c, "این لینک پیدا نشد.", reply_markup=link_back_kb())

    text = _fmt_link_row(row) + f"\n\nلینک کامل:\n{row['link']}"

    kb = InlineKeyboardMarkup(row_width=1)

    if int(row["used"] or 0) == 0:
        kb.add(InlineKeyboardButton("🗑 حذف این لینک آزاد", callback_data=f"adm_link_delete_ask_{row['id']}"))
    elif row["owner"]:
        kb.add(InlineKeyboardButton("👤 جزئیات مالک", callback_data=f"adm_user_{row['owner']}"))
        kb.add(InlineKeyboardButton("↩️ بازگردانی به استخر", callback_data=f"adm_link_repool_ask_{row['id']}"))

    kb.add(InlineKeyboardButton("🔗 بازگشت به مدیریت لینک‌ها", callback_data="adm_links"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))

    await _replace_callback_message(c, text, reply_markup=kb)


async def cb_link_delete_ask(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    link_id = c.data.split("adm_link_delete_ask_", 1)[1]
    row = subs.get_link_detail(link_id)

    if not row:
        return await _replace_callback_message(c, "این لینک پیدا نشد.", reply_markup=link_back_kb())

    if int(row["used"] or 0) == 1:
        return await _replace_callback_message(
            c,
            "❌ این لینک قبلاً تحویل شده و حذف نمی‌شود.\n"
            "حذف لینک تحویل‌شده باعث خراب شدن سابقه خرید و جزئیات کاربر می‌شود.",
            reply_markup=link_back_kb(),
        )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"adm_link_delete_confirm_{row['id']}"),
        InlineKeyboardButton("❌ منصرف شدم", callback_data="adm_links"),
    )

    await _replace_callback_message(
        c,
        f"⚠️ حذف لینک آزاد\n\n"
        f"Link ID: #{row['id']}\n"
        f"شناسه سرویس: {row['account_name'] or '-'}\n"
        f"لینک: {_short(row['link'], 100)}\n\n"
        "آیا مطمئنی؟",
        reply_markup=kb,
    )


async def cb_link_delete_confirm(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    link_id = c.data.split("adm_link_delete_confirm_", 1)[1]
    ok, reason = subs.delete_available_link(link_id)

    if ok:
        db.log_admin_action(c.from_user.id, "delete_available_link", None, f"link_id={link_id}")
        counts = subs.link_counts()
        return await _replace_callback_message(
            c,
            f"✅ لینک #{link_id} حذف شد.\n\nموجودی آزاد فعلی: {counts['available']}",
            reply_markup=link_manager_kb(),
        )

    if reason == "already_delivered":
        msg = "❌ این لینک قبلاً تحویل شده و قابل حذف نیست."
    elif reason == "not_found":
        msg = "❌ این لینک پیدا نشد."
    else:
        msg = "❌ حذف لینک انجام نشد."

    await _replace_callback_message(c, msg, reply_markup=link_back_kb())



async def cb_link_repool_ask(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    link_id = c.data.split("adm_link_repool_ask_", 1)[1]
    row = subs.get_link_detail(link_id)

    if not row:
        return await _replace_callback_message(c, "این لینک پیدا نشد.", reply_markup=link_back_kb())

    if int(row["used"] or 0) != 1:
        return await _replace_callback_message(c, "این لینک تحویل‌شده نیست و نیازی به بازگردانی ندارد.", reply_markup=link_back_kb())

    owner = row["owner"] or "-"
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ بله، به استخر برگردان", callback_data=f"adm_link_repool_confirm_{row['id']}"),
        InlineKeyboardButton("❌ لغو", callback_data=f"adm_link_detail_{row['id']}"),
    )

    await _replace_callback_message(
        c,
        "⚠️ بازگردانی لینک تحویل‌شده به استخر\n\n"
        f"Link ID: #{row['id']}\n"
        f"شناسه سرویس: {row['account_name'] or '-'}\n"
        f"مالک فعلی: {owner}\n"
        f"Purchase ID: {row['purchase_id'] or '-'}\n"
        f"لینک: {_short(row['link'], 100)}\n\n"
        "این عملیات لینک را از بخش «سرویس‌های من» کاربر حذف می‌کند و همان لینک را دوباره قابل فروش می‌کند.\n"
        "هیچ رکورد تکراری ساخته نمی‌شود. اگر کاربر قبلاً لینک را کپی کرده باشد، ممکن است همچنان لینک را داشته باشد؛ پس این گزینه را فقط برای تست، تحویل اشتباه یا اصلاح دستی استفاده کنید.",
        reply_markup=kb,
    )


async def cb_link_repool_confirm(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    link_id = c.data.split("adm_link_repool_confirm_", 1)[1]
    ok, reason, old_row = subs.return_delivered_link_to_pool(
        link_id,
        admin_id=c.from_user.id,
        reason="admin_manual_return_to_pool",
    )

    if ok:
        counts = subs.link_counts()
        owner = old_row["owner"] if old_row else "-"
        db.log_admin_action(c.from_user.id, "return_link_to_pool", owner if owner != "-" else None, f"link_id={link_id}")
        return await _replace_callback_message(
            c,
            f"✅ لینک #{link_id} از حساب {owner or '-'} حذف شد و به استخر برگشت.\n\n"
            f"موجودی آزاد فعلی: {counts['available']}",
            reply_markup=link_manager_kb(),
        )

    if reason == "not_delivered":
        msg = "❌ این لینک تحویل‌شده نیست و قابل بازگردانی نیست."
    elif reason == "not_found":
        msg = "❌ این لینک پیدا نشد."
    else:
        msg = "❌ بازگردانی لینک انجام نشد."

    await _replace_callback_message(c, msg, reply_markup=link_back_kb())


async def cb_link_add(c: types.CallbackQuery, state: FSMContext):
    await cb_addsub(c, state)


async def cb_link_search(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(
        c,
        "🔎 جستجوی لینک\n\n"
        "یکی از این موارد رو بفرست:\n"
        "• Link ID\n"
        "• شناسه Berserk\n"
        "• بخشی از لینک\n"
        "• User ID مالک",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_link_search.set()


async def process_link_search(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text":
        return await m.answer("لطفاً عبارت جستجو را به صورت متن بفرستید.", reply_markup=cancel_kb())

    rows = subs.search_links(m.text, limit=15)
    await state.finish()

    await m.answer(
        _links_list_text("🔎 نتیجه جستجوی لینک", rows),
        reply_markup=_links_list_kb(rows),
    )


async def cb_link_delete_manual(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(
        c,
        "🗑 حذف لینک آزاد\n\n"
        "Link ID لینکی که می‌خوای حذف بشه رو بفرست.\n"
        "فقط لینک‌هایی که هنوز به کاربر تحویل نشده‌اند قابل حذف هستند.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_link_delete_id.set()


async def process_link_delete_id(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text" or not m.text.strip().isdigit():
        return await m.answer("لطفاً فقط Link ID عددی را بفرستید.", reply_markup=cancel_kb())

    link_id = int(m.text.strip())
    row = subs.get_link_detail(link_id)

    if not row:
        await state.finish()
        return await m.answer("این لینک پیدا نشد.", reply_markup=link_back_kb())

    await state.finish()

    if int(row["used"] or 0) == 1:
        return await m.answer(
            "❌ این لینک قبلاً تحویل شده و قابل حذف نیست.\n"
            "حذف لینک تحویل‌شده سابقه خرید کاربر را خراب می‌کند.",
            reply_markup=link_back_kb(),
        )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"adm_link_delete_confirm_{row['id']}"),
        InlineKeyboardButton("❌ منصرف شدم", callback_data="adm_links"),
    )
    await m.answer(
        f"⚠️ حذف لینک آزاد\n\n"
        f"Link ID: #{row['id']}\n"
        f"شناسه سرویس: {row['account_name'] or '-'}\n"
        f"لینک: {_short(row['link'], 100)}\n\n"
        "آیا مطمئنی؟",
        reply_markup=kb,
    )


async def cb_addsub(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    plans = db.list_plans(active_only=True)
    if len(plans) > 1:
        kb = InlineKeyboardMarkup(row_width=1)
        for plan in plans:
            kb.add(InlineKeyboardButton(f"#{plan['id']} {plan['title']} | {_fmt_money(plan['price'])}", callback_data=f"adm_addsub_plan_{plan['id']}"))
        kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
        return await _replace_callback_message(
            c,
            "📥 افزودن لینک به استخر\n\nاین لینک‌ها برای کدام پلن هستند؟",
            reply_markup=kb,
        )

    plan_id = plans[0]["id"] if plans else db.default_plan_id()
    await state.update_data(add_sub_plan_id=int(plan_id))
    plan = db.get_plan(plan_id)
    await _replace_callback_message(
        c,
        f"لینک(های) ساب پلن «{plan['title'] if plan else '-'}» را بفرستید.\nبرای افزودن چند لینک هم‌زمان، هرکدام را در یک خط جدا بنویسید.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_add_sub.set()


async def cb_addsub_plan(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("adm_addsub_plan_", 1)[1])
    plan = db.get_plan(plan_id)
    if not plan:
        return await _replace_callback_message(c, "این پلن پیدا نشد.", reply_markup=admin_services_section_kb())
    await state.update_data(add_sub_plan_id=plan_id)
    await _replace_callback_message(
        c,
        f"لینک(های) ساب پلن «{plan['title']}» را بفرستید.\nبرای افزودن چند لینک هم‌زمان، هرکدام را در یک خط جدا بنویسید.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_add_sub.set()

async def process_addsub(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفا لینک(ها) رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    plan_id = int(data.get("add_sub_plan_id") or db.default_plan_id())
    count = subs.add_subs_bulk(m.text.splitlines(), plan_id=plan_id)
    await state.finish()
    plan = db.get_plan(plan_id)
    await m.answer(
        f"✅ {count} لینک به پلن «{plan['title'] if plan else '-'}» اضافه شد.\n"
        f"موجودی فعلی این پلن: {subs.stock_count(plan_id)}\n"
        f"موجودی کل: {subs.stock_count()}",
        reply_markup=link_manager_kb(),
    )

async def cb_topups(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    rows = db.list_pending_topups()
    if not rows:
        return await c.message.answer("درخواست شارژ در انتظار بررسی وجود نداره.", reply_markup=admin_back_kb())
    for r in rows:
        user = db.get_user(r["user_id"])
        uname = user["username"] if user else ""
        text = (
            f"💳 درخواست شارژ #{r['id']}\n"
            f"کاربر: @{uname or '-'} | ID: {r['user_id']}\n"
            f"مبلغ: {r['amount']:,} تومان\n"
            f"ثبت: {r['created_at']}"
        )
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ تایید", callback_data=f"topup_confirm_{r['id']}"),
            InlineKeyboardButton("❌ رد", callback_data=f"topup_reject_{r['id']}"),
        )
        kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
        await c.message.answer(text, reply_markup=kb)


async def cb_stats(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    lines = [
        "📊 آمار کلی Berserk VPN",
        "",
        "👥 کاربران",
        f"کل کاربران: {db.count_users()}",
        f"فعال (۷ روز اخیر): {db.active_users_count(7)}",
        "",
        "💰 درآمد و مالی",
        f"مجموع شارژهای تایید‌شده: {db.sum_approved_topups():,} تومان",
        f"مجموع موجودی فعلی همه کیف‌پول‌ها: {db.sum_all_balances():,} تومان",
        f"مجموع پاداش رفرال پرداختی: {db.total_referral_rewards():,} تومان",
        f"شارژهای در انتظار بررسی: {db.count_pending_topups()}",
        "",
        "📦 سرویس",
        f"فروخته‌شده: {subs.sold_count()}",
        f"موجودی فعلی: {subs.stock_count()}",
        "",
        "🏷 موجودی پلن‌ها:",
    ]
    for plan in db.list_plans(limit=20):
        lines.append(f"• #{plan['id']} {plan['title']}: موجودی {subs.stock_count(plan['id'])} | فروش {subs.sold_count(plan['id'])} | قیمت {int(plan['price']):,}")
    lines += [
        "📢 مخاطب‌های پیام همگانی",
        f"همه کاربران غیر بن‌شده: {db.count_broadcast_targets('all')}",
        f"خریداران: {db.count_broadcast_targets('buyers')}",
        f"بدون خرید: {db.count_broadcast_targets('no_buy')}",
        f"دارای سرویس: {db.count_broadcast_targets('has_sub')}",
        f"بدون سرویس: {db.count_broadcast_targets('no_sub')}",
        "",
        "📈 روند روزانه (۷ روز اخیر):",
    ]
    for row in db.recent_daily_stats(7):
        lines.append(f"{row['day']}: کاربر جدید {row['new_users']} | فروش {row['sales']} | رفرال {row['referral_rewards']:,}")
    await _replace_callback_message(c, "\n".join(lines), reply_markup=admin_back_kb())


# -------------------- مدیریت پلن‌ها --------------------


async def cb_sales_report(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    lines = [
        "💹 گزارش فروش سریع",
        "",
        f"فروش امروز: {_fmt_money(db.today_sales_total())}",
        f"فروش دیروز: {_fmt_money(db.yesterday_sales_total())}",
        f"فروش ۷ روز اخیر: {_fmt_money(db.period_sales_total(7))}",
        f"فروش ۳۰ روز اخیر: {_fmt_money(db.period_sales_total(30))}",
        f"تعداد خرید ۷ روز اخیر: {db.period_purchase_count(7)}",
        f"پرداخت‌های تأییدشده ۷ روز اخیر: {_fmt_money(db.approved_topups_total_for_days(7))}",
        f"موجودی کل کیف پول کاربران: {_fmt_money(db.sum_all_balances())}",
        "",
        "📦 موجودی پلن‌ها:",
    ]
    for plan in db.list_plans(limit=30):
        stock = subs.stock_count(plan["id"])
        sold = subs.sold_count(plan["id"])
        warn = " ⚠️" if stock <= int(plan["low_stock_threshold"] or 0) else ""
        lines.append(f"• #{plan['id']} {plan['title']}: موجودی {stock} | فروش {sold} | قیمت {_fmt_money(plan['price'])}{warn}")
    await _replace_callback_message(c, "\n".join(lines), reply_markup=admin_reports_section_kb())


async def cb_admin_logs(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    rows = db.list_admin_logs(limit=25)
    if not rows:
        return await _replace_callback_message(c, "🧾 لاگ عملیات ادمین\n\nهنوز لاگی ثبت نشده.", reply_markup=admin_reports_section_kb())
    lines = ["🧾 آخرین عملیات ادمین‌ها:\n"]
    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"{idx}. admin={row['admin_id'] or '-'} | action={row['action_type']} | target={row['target_user_id'] or '-'}\n"
            f"   زمان: {_dual(row['created_at'])}\n"
            f"   توضیح: {_short(row['details'], 120)}"
        )
    await _replace_callback_message(c, "\n".join(lines), reply_markup=admin_reports_section_kb())


def plans_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("➕ ساخت پلن جدید", callback_data="plan_create"))
    for plan in db.list_plans(limit=30):
        active = "✅" if int(plan["is_active"] or 0) else "🚫"
        default = " ⭐" if int(plan["is_default"] or 0) else ""
        kb.add(InlineKeyboardButton(f"{active} #{plan['id']} {plan['title']}{default}", callback_data=f"plan_detail_{plan['id']}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به سرویس‌ها", callback_data="adm_section_services"))
    return kb


def plan_detail_kb(plan_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ ویرایش", callback_data=f"plan_edit_{plan_id}"),
        InlineKeyboardButton("👁 فعال/غیرفعال", callback_data=f"plan_toggle_{plan_id}"),
    )
    kb.add(InlineKeyboardButton("⬅️ مدیریت پلن‌ها", callback_data="adm_plans"))
    kb.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm_back"))
    return kb


def _fmt_plan(plan):
    stock = db.plan_stock_count(plan["id"])
    sold = db.plan_sold_count(plan["id"])
    return (
        f"🏷 پلن #{plan['id']}\n\n"
        f"عنوان: {plan['title']}\n"
        f"حجم: {plan['volume_label'] or '-'}\n"
        f"مدت: {plan['duration_label'] or '-'}\n"
        f"قیمت فروش: {_fmt_money(plan['price'])}\n"
        f"قیمت خرید/هزینه: {_fmt_money(plan['cost_price'])}\n"
        f"برچسب: {plan['tag'] or '-'}\n"
        f"توضیح: {plan['description'] or '-'}\n"
        f"ترتیب نمایش: {plan['sort_order']}\n"
        f"حداکثر خرید در سفارش: {plan['max_per_order']}\n"
        f"نمایش موجودی به کاربر: {'بله' if int(plan['show_stock'] or 0) else 'خیر'}\n"
        f"آستانه هشدار موجودی: {plan['low_stock_threshold']}\n"
        f"وضعیت: {'فعال' if int(plan['is_active'] or 0) else 'غیرفعال'}\n"
        f"پیش‌فرض: {'بله' if int(plan['is_default'] or 0) else 'خیر'}\n"
        f"موجودی آزاد این پلن: {stock}\n"
        f"فروخته‌شده از این پلن: {sold}"
    )


def _plan_form_help(current=None):
    sample = (
        "عنوان: 100 گیگ یک ماهه\n"
        "حجم: 100GB\n"
        "مدت: 30 روز\n"
        "قیمت: 350000\n"
        "توضیح: مناسب استفاده عمومی\n"
        "ترتیب: 100\n"
        "وضعیت: active\n"
        "حداکثر: 4\n"
        "هزینه: 0\n"
        "برچسب: پرفروش\n"
        "نمایش موجودی: yes\n"
        "هشدار موجودی: 5"
    )
    if current:
        sample = current
    return "فرم پلن را به این شکل بفرستید:\n\n" + sample


def _parse_plan_form(text):
    aliases = {
        "عنوان": "title", "title": "title",
        "حجم": "volume_label", "volume": "volume_label",
        "مدت": "duration_label", "duration": "duration_label",
        "قیمت": "price", "price": "price",
        "توضیح": "description", "description": "description",
        "ترتیب": "sort_order", "order": "sort_order",
        "وضعیت": "is_active", "active": "is_active",
        "حداکثر": "max_per_order", "max": "max_per_order",
        "هزینه": "cost_price", "cost": "cost_price",
        "برچسب": "tag", "tag": "tag",
        "نمایش موجودی": "show_stock", "show_stock": "show_stock",
        "هشدار موجودی": "low_stock_threshold", "low_stock": "low_stock_threshold",
    }
    data = {}
    for raw in (text or "").splitlines():
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        key = aliases.get(k.strip())
        if key:
            value = v.strip()
            if key in {"price", "sort_order", "max_per_order", "cost_price", "low_stock_threshold"}:
                value = int(value.replace(",", "") or 0)
            if key in {"is_active", "show_stock"}:
                value = 0 if value.lower() in {"inactive", "off", "0", "false", "غیرفعال", "no", "خیر"} else 1
            data[key] = value
    return data


async def cb_plans(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    lines = ["🏷 مدیریت پلن‌ها", "", "پلن‌های فعال در خرید سرویس به کاربر نمایش داده می‌شوند.", ""]
    for idx, plan in enumerate(db.list_plans(limit=30), start=1):
        lines.append(f"{idx}. {'✅' if int(plan['is_active'] or 0) else '🚫'} #{plan['id']} {plan['title']} | {_fmt_money(plan['price'])} | موجودی {db.plan_stock_count(plan['id'])}")
    await _replace_callback_message(c, "\n".join(lines), reply_markup=plans_menu_kb())


async def cb_plan_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_detail_", 1)[1])
    plan = db.get_plan(plan_id)
    if not plan:
        return await _replace_callback_message(c, "این پلن پیدا نشد.", reply_markup=plans_menu_kb())
    await _replace_callback_message(c, _fmt_plan(plan), reply_markup=plan_detail_kb(plan_id))


async def cb_plan_create(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await state.update_data(plan_action="create")
    await _replace_callback_message(c, "➕ ساخت پلن جدید\n\n" + _plan_form_help(), reply_markup=cancel_kb())
    await AdminStates.waiting_plan_form.set()


async def cb_plan_edit(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_edit_", 1)[1])
    plan = db.get_plan(plan_id)
    if not plan:
        return await c.message.answer("این پلن پیدا نشد.", reply_markup=plans_menu_kb())
    current = (
        f"عنوان: {plan['title']}\n"
        f"حجم: {plan['volume_label'] or ''}\n"
        f"مدت: {plan['duration_label'] or ''}\n"
        f"قیمت: {plan['price']}\n"
        f"توضیح: {plan['description'] or ''}\n"
        f"ترتیب: {plan['sort_order']}\n"
        f"وضعیت: {'active' if int(plan['is_active'] or 0) else 'inactive'}\n"
        f"حداکثر: {plan['max_per_order']}\n"
        f"هزینه: {plan['cost_price'] or 0}\n"
        f"برچسب: {plan['tag'] or ''}\n"
        f"نمایش موجودی: {'yes' if int(plan['show_stock'] or 0) else 'no'}\n"
        f"هشدار موجودی: {plan['low_stock_threshold']}"
    )
    await state.update_data(plan_action="edit", plan_id=plan_id)
    await _replace_callback_message(c, "✏️ ویرایش پلن\n\n" + _plan_form_help(current), reply_markup=cancel_kb())
    await AdminStates.waiting_plan_form.set()


async def process_plan_form(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفاً فرم پلن را به صورت متن بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    form = _parse_plan_form(m.text)
    try:
        if data.get("plan_action") == "edit":
            plan_id = int(data["plan_id"])
            ok = db.update_plan(plan_id, form)
            if not ok:
                await state.finish()
                return await m.answer("این پلن پیدا نشد.", reply_markup=plans_menu_kb())
        else:
            plan_id = db.create_plan(form)
    except Exception as exc:
        return await m.answer(f"❌ اطلاعات پلن معتبر نیست: {exc}\n\n" + _plan_form_help(), reply_markup=cancel_kb())
    await state.finish()
    action_type = "update_plan" if data.get("plan_action") == "edit" else "create_plan"
    db.log_admin_action(m.from_user.id, action_type, None, f"plan_id={plan_id}; title={form.get('title','')}")
    plan = db.get_plan(plan_id)
    await m.answer("✅ پلن ذخیره شد.\n\n" + _fmt_plan(plan), reply_markup=plan_detail_kb(plan_id))


async def cb_plan_toggle(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_toggle_", 1)[1])
    ok = db.toggle_plan(plan_id)
    plan = db.get_plan(plan_id)
    if ok:
        db.log_admin_action(c.from_user.id, "toggle_plan", None, f"plan_id={plan_id}; active={plan['is_active'] if plan else '-'}")
    msg = "✅ وضعیت پلن تغییر کرد." if ok else "❌ امکان تغییر وضعیت این پلن وجود ندارد. پلن پیش‌فرض را غیرفعال نکنید."
    await _replace_callback_message(c, msg + ("\n\n" + _fmt_plan(plan) if plan else ""), reply_markup=plan_detail_kb(plan_id) if plan else plans_menu_kb())


SETTING_FIELDS = [
    ("plan_title", "عنوان پلن", settings.plan_title, "text"),
    ("plan_duration_label", "مدت پلن", settings.plan_duration_label, "text"),
    ("plan_price", "قیمت پلن", settings.plan_price, "int"),
    ("ref_reward", "پاداش رفرال", settings.ref_reward, "int"),
    ("card_number", "شماره کارت", settings.card_number, "text"),
    ("card_holder", "نام صاحب کارت", settings.card_holder, "text"),
    ("min_topup", "حداقل شارژ", settings.min_topup, "int"),
    ("low_stock_threshold", "آستانه هشدار موجودی", settings.low_stock_threshold, "int"),
]
_FIELDS_BY_KEY = {f[0]: f for f in SETTING_FIELDS}


def settings_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    for key, label, getter, _ in SETTING_FIELDS:
        value = getter()
        display = f"{value:,}" if isinstance(value, int) else value
        kb.add(InlineKeyboardButton(f"{label}: {display}", callback_data=f"setkey_{key}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="adm_back"))
    return kb


async def cb_settings(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(
        c,
        "⚙️ تنظیمات — روی هر مورد بزنید تا مقدارش رو تغییر بدید:",
        reply_markup=settings_menu_kb(),
    )


async def cb_setkey(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("setkey_", 1)[1]
    field = _FIELDS_BY_KEY.get(key)
    if not field:
        return await c.message.answer("این تنظیم پیدا نشد.", reply_markup=admin_back_kb())
    _, label, getter, ftype = field
    current = getter()
    hint = " (فقط عدد)" if ftype == "int" else ""
    await state.update_data(setting_key=key, setting_type=ftype)
    await c.message.answer(f"مقدار جدید برای «{label}»{hint} رو بفرستید.\nمقدار فعلی: {current}", reply_markup=cancel_kb())
    await AdminStates.waiting_setting_value.set()


async def process_setting_value(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    key, ftype = data["setting_key"], data["setting_type"]
    value = m.text.strip()
    if ftype == "int":
        if not value.replace(",", "").lstrip("-").isdigit():
            return await m.answer("لطفا فقط عدد بفرستید.", reply_markup=cancel_kb())
        value = int(value.replace(",", ""))
    db.set_setting(key, value)
    await state.finish()
    await m.answer("✅ تنظیمات به‌روزرسانی شد.", reply_markup=settings_menu_kb())



# -------------------- مدیریت متن‌ها با Draft / Preview / Publish --------------------

DEFAULT_MESSAGE_TEXTS = {
    "welcome": "⚡ Berserk VPN Ready\n\nمنوی اصلی پایین صفحه همیشه در دسترس شماست.",
    "main_menu": "⚡ Berserk VPN Ready\n\nاز منوی پایین تلگرام استفاده کنید؛ لازم نیست هر بار /start بزنید.",
    "menu_buy": "{body}",
    "menu_wallet": "{body}",
    "menu_referral": "{body}",
    "my_services_empty": "هنوز هیچ سرویسی خریداری نکردید.",
    "guide_home": "📚 آموزش اتصال\n\nدستگاه خود را انتخاب کنید:",
    "guide_android": "📱 آموزش اندروید\n\n۱. یک برنامه سازگار با ساب‌لینک نصب کنید.\n۲. لینک سرویس را کپی کنید.\n۳. داخل برنامه، گزینه Import/Subscription را بزنید.\n۴. لینک را وارد و بروزرسانی کنید.",
    "guide_ios": "🍎 آموزش آیفون\n\n۱. یک کلاینت سازگار نصب کنید.\n۲. ساب‌لینک را کپی کنید.\n۳. از بخش Subscription یا Import، لینک را اضافه کنید.\n۴. اتصال را تست کنید.",
    "guide_windows": "💻 آموزش ویندوز\n\n۱. برنامه مناسب ویندوز را نصب کنید.\n۲. لینک سرویس را از بخش سرویس‌های من کپی کنید.\n۳. از بخش Subscription لینک را اضافه کنید.\n۴. Update subscription را بزنید.",
    "guide_mac": "🖥 آموزش مک\n\n۱. کلاینت سازگار با مک را نصب کنید.\n۲. لینک سرویس را اضافه کنید.\n۳. ساب‌لینک را بروزرسانی و اتصال را فعال کنید.",
    "guide_troubleshoot": "❓ مشکل اتصال دارم\n\nاول اینترنت اصلی را بررسی کنید، سپس ساب‌لینک را بروزرسانی کنید. اگر مشکل ادامه داشت، از بخش پشتیبانی پیام بدهید.",
    "guide_update": "🔄 بروزرسانی ساب‌لینک\n\nدر برنامه خود گزینه Update/Refresh Subscription را بزنید تا لیست سرورها تازه شود.",
    "support_intro": "برای ارسال پیام به پشتیبانی، روی دکمه زیر بزنید:",
    "rules": "📜 قوانین و شرایط خرید\n\nبعد از خرید، لینک آماده تحویل داده می‌شود. در صورت خرابی واقعی سرویس، از پشتیبانی پیگیری کنید.",
}

MESSAGE_SAMPLE_BODIES = {
    "menu_buy": (
        "🛒 خرید سرویس\n\n"
        "پلن: نمونه پلن\n"
        "⏳ مدت: ۳۰ روز\n"
        "قیمت هر عدد: 100,000 تومان\n"
        "موجودی کیف پول شما: 40,000 تومان\n"
        "موجودی سرویس: 25\n\n"
        "⚠️ موجودی کافی نیست. 60,000 تومان دیگر شارژ کنید.\n"
        "برای هماهنگی دستی یا تعداد بالا می‌توانید خرید عمده بزنید."
    ),
    "menu_wallet": "💳 موجودی: 40,000 تومان\n📦 تعداد خرید: 1",
    "menu_referral": (
        "👥 لینک دعوت اختصاصی شما:\nhttps://t.me/YourBot?start=123456\n\n"
        "تعداد زیرمجموعه: 2 نفر\n"
        "پاداش هر اولین خرید واقعی زیرمجموعه: 10,000 تومان\n\n"
        "پاداش فقط بعد از اولین خرید واقعی زیرمجموعه پرداخت می‌شود."
    ),
}


def _message_default_template(key):
    return DEFAULT_MESSAGE_TEXTS.get(key, "")


def _message_preview_body(key):
    return MESSAGE_SAMPLE_BODIES.get(key, _message_default_template(key))


def _short_block(text, limit=900):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n..."


MESSAGE_CATEGORIES = {
    "buy": ("🛒 پیام‌های خرید", ["menu_buy", "rules"]),
    "wallet": ("💰 پیام‌های کیف پول", ["menu_wallet"]),
    "services": ("📦 پیام‌های سرویس‌های من", ["my_services_empty"]),
    "guide": ("📚 پیام‌های آموزش اتصال", ["guide_home", "guide_android", "guide_ios", "guide_windows", "guide_mac", "guide_troubleshoot", "guide_update"]),
    "support": ("🎫 پیام‌های پشتیبانی", ["support_intro"]),
    "referral": ("👥 پیام‌های دعوت دوستان", ["menu_referral"]),
    "main": ("👋 شروع و منوی اصلی", ["welcome", "main_menu"]),
}


def _message_keys_for_category(cat):
    keys = MESSAGE_CATEGORIES.get(cat, ("", []))[1]
    labels = dict(messages.MESSAGE_KEYS)
    return [(k, labels.get(k, k)) for k in keys if messages.is_valid_key(k)]


def messages_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    for cat, (label, _) in MESSAGE_CATEGORIES.items():
        kb.add(InlineKeyboardButton(label, callback_data=f"msgcat_{cat}"))
    kb.add(InlineKeyboardButton("📋 نمایش همه پیام‌ها", callback_data="msgcat_all"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="adm_section_personalize"))
    return kb


def message_category_kb(cat):
    kb = InlineKeyboardMarkup(row_width=1)
    items = messages.MESSAGE_KEYS if cat == "all" else _message_keys_for_category(cat)
    for key, label in items:
        kb.add(InlineKeyboardButton(label, callback_data=f"msgkey_{key}"))
    kb.add(InlineKeyboardButton("⬅️ دسته‌بندی پیام‌ها", callback_data="adm_messages"))
    return kb


def message_action_kb(key):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👁 مشاهده متن پیش‌فرض", callback_data=f"msg_default_{key}"))
    kb.add(InlineKeyboardButton("📋 کپی پیش‌فرض به Draft", callback_data=f"msg_copy_default_{key}"))
    kb.add(InlineKeyboardButton("⚡ ویرایش سریع", callback_data=f"msg_edit_{key}"))
    kb.add(InlineKeyboardButton("➕ افزودن متن قبل", callback_data=f"msg_prefix_{key}"))
    kb.add(InlineKeyboardButton("➕ افزودن متن بعد", callback_data=f"msg_suffix_{key}"))
    kb.add(InlineKeyboardButton("🧪 پیش‌نمایش Draft", callback_data=f"msg_preview_{key}"))
    kb.add(InlineKeyboardButton("✅ ثبت نهایی / انتشار", callback_data=f"msg_publish_{key}"))
    kb.add(InlineKeyboardButton("🧹 حذف Draft", callback_data=f"msg_clear_draft_{key}"))
    kb.add(InlineKeyboardButton("♻️ بازگشت به متن پیش‌فرض", callback_data=f"msg_clear_published_{key}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به مدیریت متن‌ها", callback_data="adm_messages"))
    return kb


async def cb_messages(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(
        c,
        "📝 مدیریت متن‌ها\n\n"
        "متن‌ها ابتدا به‌صورت Draft ذخیره می‌شوند. بعد از پیش‌نمایش، با «ثبت نهایی» برای کاربران منتشر می‌شوند.\n\n"
        "برای پیام‌های سیستمی مثل خرید سرویس، کد {body} نماینده بخش خودکار ربات است؛ مثل قیمت، موجودی، کسری موجودی و لینک دعوت.\n"
        "اگر در این پیام‌ها {body} را حذف کنید، ربات برای امنیت بخش سیستمی را خودکار پایین متن شما اضافه می‌کند.",
        reply_markup=messages_menu_kb(),
    )


async def cb_msg_category(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    cat = c.data.split("msgcat_", 1)[1]
    title = "📋 همه پیام‌ها" if cat == "all" else MESSAGE_CATEGORIES.get(cat, (cat, []))[0]
    await _replace_callback_message(c, f"{title}\n\nیک پیام را برای مشاهده، ویرایش یا پیش‌نمایش انتخاب کنید.", reply_markup=message_category_kb(cat))


async def cb_msgkey(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    key = c.data.split("msgkey_", 1)[1]
    label = dict(messages.MESSAGE_KEYS).get(key, key)
    current_text, current_photo = messages.get(key)
    draft_text, draft_photo = messages.get_draft(key)
    default_template = _message_default_template(key)

    info = (
        f"📝 مدیریت متن «{label}»\n\n"
        f"نوع متن: {'سیستمی/داینامیک' if messages.is_dynamic_key(key) else 'معمولی'}\n"
        f"متن پیش‌فرض: {_short_block(default_template, 250) or '(ندارد)'}\n\n"
        f"متن منتشرشده: {_short_block(current_text, 350) or '(تنظیم نشده؛ متن پیش‌فرض استفاده می‌شود)'}\n\n"
        f"Draft: {_short_block(draft_text, 350) or '(ندارد)'}\n"
        f"عکس منتشرشده: {'دارد' if current_photo else '(ندارد)'}\n"
        f"عکس Draft: {'دارد' if draft_photo else '(ندارد)'}\n\n"
        "تغییرات Draft تا وقتی ثبت نهایی نشوند، برای کاربر نمایش داده نمی‌شوند."
    )

    await _replace_callback_message(c, info, reply_markup=message_action_kb(key))


async def cb_msg_default(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_default_", 1)[1]
    if not messages.is_valid_key(key):
        return await c.message.answer("این متن پیدا نشد.", reply_markup=messages_menu_kb())
    label = dict(messages.MESSAGE_KEYS).get(key, key)
    default_template = _message_default_template(key)
    sample_body = _message_preview_body(key)
    sample_rendered = messages.render_template(key, default_template, sample_body)

    help_text = (
        f"👁 متن پیش‌فرض «{label}»\n\n"
        f"قالب قابل کپی:\n{default_template or '(متن پیش‌فرض خالی است)'}\n\n"
    )

    if messages.is_dynamic_key(key):
        help_text += (
            "ℹ️ این پیام بخش سیستمی دارد. کد {body} یعنی همان متن خودکار ربات؛ مثل قیمت، موجودی، کسری موجودی یا لینک دعوت.\n"
            "می‌توانید متن خودتان را قبل یا بعد از {body} اضافه کنید، ولی بهتر است خود {body} را نگه دارید.\n\n"
            f"نمونه نمایش با اطلاعات فرضی:\n{sample_rendered}"
        )

    await c.message.answer(_short_block(help_text, 3800), reply_markup=message_action_kb(key))


async def cb_msg_copy_default(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_copy_default_", 1)[1]
    if not messages.is_valid_key(key):
        return await c.message.answer("این متن پیدا نشد.", reply_markup=messages_menu_kb())
    default_template = _message_default_template(key)
    messages.set_draft_text(key, default_template)
    await _replace_callback_message(
        c,
        "✅ متن پیش‌فرض به Draft کپی شد.\n"
        "حالا می‌توانید Draft را ویرایش کنید، پیش‌نمایش بگیرید و بعد ثبت نهایی کنید.",
        reply_markup=message_action_kb(key),
    )


async def cb_msg_edit_start(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_edit_", 1)[1]
    if not messages.is_valid_key(key):
        return await c.message.answer("این متن پیدا نشد.", reply_markup=messages_menu_kb())
    label = dict(messages.MESSAGE_KEYS).get(key, key)
    await state.update_data(message_key=key, message_edit_mode="replace")

    hint = ""
    if messages.is_dynamic_key(key):
        hint = (
            "\n\nکد سیستمی مهم: {body}\n"
            "{body} جای قیمت، موجودی، کسری موجودی، لینک دعوت و متن خودکار ربات قرار می‌گیرد.\n"
            "مثال:\n"
            "توضیح دلخواه شما\n\n{body}\n\nپیام پایانی دلخواه شما"
        )

    await _replace_callback_message(
        c,
        f"✏️ ویرایش آزمایشی «{label}»\n\n"
        "متن جدید را بفرستید تا به‌عنوان Draft ذخیره شود.\n"
        "برای شروع راحت‌تر می‌توانید اول «کپی پیش‌فرض به Draft» را بزنید.\n"
        "برای تنظیم عکس Draft، عکس را همراه کپشن اختیاری بفرستید.\n"
        "برای لغو /cancel را بزنید."
        f"{hint}",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_message_edit.set()


async def cb_msg_preview(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_preview_", 1)[1]
    if not messages.is_valid_key(key):
        return await c.message.answer("این متن پیدا نشد.", reply_markup=messages_menu_kb())
    label = dict(messages.MESSAGE_KEYS).get(key, key)
    text, photo = messages.compose_preview(key, _message_preview_body(key))
    await c.message.answer(f"🧪 پیش‌نمایش «{label}»:")
    if photo:
        await c.message.answer_photo(photo, caption=text or None)
    else:
        await c.message.answer(text or "(متن خالی است)")
    await c.message.answer("بعد از بررسی، می‌توانید ثبت نهایی کنید.", reply_markup=message_action_kb(key))


async def cb_msg_publish(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_publish_", 1)[1]
    ok = messages.publish_draft(key)
    if ok:
        await _replace_callback_message(c, "✅ Draft منتشر شد و از این به بعد برای کاربران نمایش داده می‌شود.", reply_markup=message_action_kb(key))
    else:
        await _replace_callback_message(c, "Draft فعالی برای انتشار وجود ندارد.", reply_markup=message_action_kb(key))


async def cb_msg_clear_draft(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_clear_draft_", 1)[1]
    messages.clear_draft(key)
    await _replace_callback_message(c, "✅ Draft حذف شد. متن منتشرشده قبلی دست‌نخورده باقی ماند.", reply_markup=message_action_kb(key))


async def cb_msg_clear_published(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_clear_published_", 1)[1]
    messages.clear(key)
    await _replace_callback_message(c, "✅ متن سفارشی منتشرشده حذف شد. از این به بعد متن پیش‌فرض ربات استفاده می‌شود.", reply_markup=messages_menu_kb())


async def cb_msg_prefix(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_prefix_", 1)[1]
    if not messages.is_valid_key(key):
        return await c.message.answer("این متن پیدا نشد.", reply_markup=messages_menu_kb())
    await state.update_data(message_key=key, message_edit_mode="prefix")
    await _replace_callback_message(c, "متنی که می‌خواهید قبل از پیام اضافه شود را بفرستید.", reply_markup=cancel_kb())
    await AdminStates.waiting_message_edit.set()


async def cb_msg_suffix(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msg_suffix_", 1)[1]
    if not messages.is_valid_key(key):
        return await c.message.answer("این متن پیدا نشد.", reply_markup=messages_menu_kb())
    await state.update_data(message_key=key, message_edit_mode="suffix")
    await _replace_callback_message(c, "متنی که می‌خواهید بعد از پیام اضافه شود را بفرستید.", reply_markup=cancel_kb())
    await AdminStates.waiting_message_edit.set()


async def process_message_edit(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    data = await state.get_data()
    key = data["message_key"]
    label = dict(messages.MESSAGE_KEYS).get(key, key)

    if m.content_type == "text":
        messages.set_draft_text(key, m.text)
        await state.finish()
        return await m.answer(
            f"✅ Draft متن «{label}» ذخیره شد.\n"
            "برای اعمال روی ربات، از پیش‌نمایش و سپس ثبت نهایی استفاده کنید.",
            reply_markup=message_action_kb(key),
        )

    if m.content_type == "photo":
        messages.set_draft_photo(key, m.photo[-1].file_id)

        if m.caption:
            messages.set_draft_text(key, m.caption)

        await state.finish()
        return await m.answer(
            f"✅ Draft عکس/متن «{label}» ذخیره شد.\n"
            "برای اعمال روی ربات، از پیش‌نمایش و سپس ثبت نهایی استفاده کنید.",
            reply_markup=message_action_kb(key),
        )

    await m.answer("لطفاً فقط متن یا عکس بفرستید.", reply_markup=cancel_kb())


# -------------------- مدیریت دکمه‌های اختصاصی --------------------

BUTTON_TYPE_LABELS = {
    "text": "متنی",
    "link": "لینک",
    "submenu": "زیرمنو",
    "file": "فایل",
    "support": "پشتیبانی",
    "buy_plan": "خرید پلن",
    "faq": "سوالات متداول",
    "guide": "آموزش",
}

BUTTON_LOCATION_LABELS = {
    "main": "منوی اصلی",
    "buy": "منوی خرید سرویس",
    "my_services": "منوی سرویس‌های من",
    "wallet": "منوی کیف پول",
    "support": "منوی پشتیبانی",
    "guide": "منوی آموزش‌ها",
    "account": "منوی حساب کاربری",
}

BUTTON_AUDIENCE_LABELS = {
    "all": "همه کاربران",
    "buyers": "خریداران",
    "no_buy": "بدون خرید",
    "has_service": "دارای سرویس",
    "no_service": "بدون سرویس",
    "admins": "فقط ادمین‌ها",
}


def custom_buttons_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("➕ ساخت دکمه جدید ساده", callback_data="btn_create"))
    kb.add(InlineKeyboardButton("📋 دکمه‌های اختصاصی", callback_data="btn_list"))
    kb.add(InlineKeyboardButton("🧩 دکمه‌های فعلی ربات", callback_data="sysbtn_list"))
    kb.add(InlineKeyboardButton("🧪 پیش‌نمایش منوی اصلی", callback_data="btn_preview_main"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="adm_section_personalize"))
    return kb


def system_buttons_list_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    for row in db.list_system_buttons():
        active = "✅" if int(row["is_active"] or 0) else "🚫"
        kb.add(InlineKeyboardButton(f"{active} {row['title'] or row['default_title']} ({row['key']})", callback_data=f"sysbtn_detail_{row['key']}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به مدیریت دکمه‌ها", callback_data="adm_buttons"))
    return kb


def system_button_detail_kb(key):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ تغییر عنوان", callback_data=f"sysbtn_title_{key}"),
        InlineKeyboardButton("📍 تغییر جایگاه", callback_data=f"sysbtn_location_{key}"),
    )
    kb.add(
        InlineKeyboardButton("↕️ تغییر ترتیب", callback_data=f"sysbtn_order_{key}"),
        InlineKeyboardButton("👁 فعال/غیرفعال", callback_data=f"sysbtn_toggle_{key}"),
    )
    kb.add(InlineKeyboardButton("♻️ بازگردانی پیش‌فرض", callback_data=f"sysbtn_reset_{key}"))
    kb.add(InlineKeyboardButton("⬅️ دکمه‌های فعلی ربات", callback_data="sysbtn_list"))
    return kb


def _fmt_system_button(row):
    return (
        f"🧩 دکمه سیستمی: {row['key']}\n\n"
        f"عنوان فعلی: {row['title'] or row['default_title']}\n"
        f"عنوان پیش‌فرض: {row['default_title']}\n"
        f"جایگاه: {BUTTON_LOCATION_LABELS.get(row['location'], row['location'])}\n"
        f"ترتیب: {row['sort_order']}\n"
        f"وضعیت نمایش: {'فعال' if int(row['is_active'] or 0) else 'غیرفعال'}\n\n"
        "عملکرد این دکمه قفل است و فقط ظاهر/جایگاه آن تغییر می‌کند."
    )


async def cb_system_buttons(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(c, "🧩 دکمه‌های فعلی ربات\n\nاین دکمه‌ها حذف نمی‌شوند و عملکرد اصلی‌شان قفل می‌ماند.", reply_markup=system_buttons_list_kb())


async def cb_system_button_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("sysbtn_detail_", 1)[1]
    row = db.get_system_button(key)
    if not row:
        return await _replace_callback_message(c, "این دکمه پیدا نشد.", reply_markup=system_buttons_list_kb())
    await _replace_callback_message(c, _fmt_system_button(row), reply_markup=system_button_detail_kb(key))


async def cb_system_button_title(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("sysbtn_title_", 1)[1]
    row = db.get_system_button(key)
    if not row:
        return await c.message.answer("این دکمه پیدا نشد.", reply_markup=system_buttons_list_kb())
    await state.update_data(system_button_key=key)
    await _replace_callback_message(c, f"عنوان جدید برای دکمه «{row['title'] or row['default_title']}» را بفرستید.", reply_markup=cancel_kb())
    await AdminStates.waiting_system_button_title.set()


async def process_system_button_title(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text" or not m.text.strip():
        return await m.answer("لطفاً عنوان را به صورت متن بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    key = data["system_button_key"]
    db.update_system_button(key, title=m.text.strip())
    await state.finish()
    row = db.get_system_button(key)
    await m.answer("✅ عنوان دکمه به‌روزرسانی شد.\n\n" + _fmt_system_button(row), reply_markup=system_button_detail_kb(key))


async def cb_system_button_order(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("sysbtn_order_", 1)[1]
    await state.update_data(system_button_key=key)
    await _replace_callback_message(c, "عدد ترتیب جدید را بفرستید. عدد کمتر بالاتر نمایش داده می‌شود.", reply_markup=cancel_kb())
    await AdminStates.waiting_system_button_order.set()


async def process_system_button_order(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text" or not m.text.strip().lstrip("-").isdigit():
        return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    key = data["system_button_key"]
    db.update_system_button(key, sort_order=int(m.text.strip()))
    await state.finish()
    row = db.get_system_button(key)
    await m.answer("✅ ترتیب دکمه به‌روزرسانی شد.\n\n" + _fmt_system_button(row), reply_markup=system_button_detail_kb(key))


async def cb_system_button_location(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("sysbtn_location_", 1)[1]
    await state.update_data(system_button_key=key)
    await _replace_callback_message(c, "جایگاه جدید را بفرستید.\nمجاز: main, buy, my_services, wallet, support, guide, account", reply_markup=cancel_kb())
    await AdminStates.waiting_system_button_location.set()


async def process_system_button_location(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفاً جایگاه را به صورت متن بفرستید.", reply_markup=cancel_kb())
    loc = m.text.strip().lower()
    if loc not in db.ALLOWED_CUSTOM_BUTTON_LOCATIONS:
        return await m.answer("جایگاه معتبر نیست. مجاز: main, buy, my_services, wallet, support, guide, account", reply_markup=cancel_kb())
    data = await state.get_data()
    key = data["system_button_key"]
    db.update_system_button(key, location=loc)
    await state.finish()
    row = db.get_system_button(key)
    await m.answer("✅ جایگاه دکمه به‌روزرسانی شد.\n\n" + _fmt_system_button(row), reply_markup=system_button_detail_kb(key))


async def cb_system_button_toggle(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("sysbtn_toggle_", 1)[1]
    row = db.get_system_button(key)
    if not row:
        return await _replace_callback_message(c, "این دکمه پیدا نشد.", reply_markup=system_buttons_list_kb())
    db.update_system_button(key, is_active=0 if int(row["is_active"] or 0) else 1)
    row = db.get_system_button(key)
    await _replace_callback_message(c, "✅ وضعیت نمایش دکمه تغییر کرد.\n\n" + _fmt_system_button(row), reply_markup=system_button_detail_kb(key))


async def cb_system_button_reset(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("sysbtn_reset_", 1)[1]
    db.reset_system_button(key)
    row = db.get_system_button(key)
    await _replace_callback_message(c, "♻️ دکمه به حالت پیش‌فرض برگشت.\n\n" + _fmt_system_button(row), reply_markup=system_button_detail_kb(key))


def custom_button_detail_kb(button_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ ویرایش دکمه", callback_data=f"btn_edit_{button_id}"),
        InlineKeyboardButton("🗑 حذف دکمه", callback_data=f"btn_delete_{button_id}"),
    )
    kb.add(
        InlineKeyboardButton("👁 فعال / غیرفعال", callback_data=f"btn_toggle_{button_id}"),
        InlineKeyboardButton("↕️ تغییر ترتیب", callback_data=f"btn_order_{button_id}"),
    )
    kb.add(
        InlineKeyboardButton("📍 تغییر جایگاه", callback_data=f"btn_location_{button_id}"),
        InlineKeyboardButton("🧪 پیش‌نمایش", callback_data=f"btn_preview_{button_id}"),
    )
    kb.add(InlineKeyboardButton("✅ ثبت نهایی / انتشار", callback_data=f"btn_publish_{button_id}"))
    kb.add(InlineKeyboardButton("⬅️ لیست دکمه‌ها", callback_data="btn_list"))
    kb.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm_back"))
    return kb


def _button_form_help(current=None):
    base = "" if current is None else "\nمقدارهای فعلی/پیشنهادی را تغییر بده و دوباره بفرست:\n"
    return (
        "فرمت ساخت/ویرایش دکمه را به همین شکل بفرست:\n\n"
        "عنوان: 📚 نمونه دکمه\n"
        "نوع: text\n"
        "متن یا لینک: متن، لینک، file_id یا توضیح دکمه\n"
        "جایگاه: main\n"
        "ترتیب: 100\n"
        "وضعیت: active\n"
        "نمایش: all\n"
        "شروع: \n"
        "پایان: \n\n"
        "نوع‌های مجاز: text, link, submenu, file, support, buy_plan, faq, guide\n"
        "جایگاه‌های مجاز: main, buy, my_services, wallet, support, guide, account\n"
        "نمایش‌های مجاز: all, buyers, no_buy, has_service, no_service, admins\n"
        "نکته: تغییر اول Draft می‌شود؛ بعد از پیش‌نمایش باید ثبت نهایی شود."
        + base
    )


def _parse_button_form(text):
    aliases = {
        "عنوان": "title",
        "title": "title",
        "نوع": "button_type",
        "type": "button_type",
        "متن یا لینک": "payload",
        "متن": "payload",
        "لینک": "payload",
        "payload": "payload",
        "جایگاه": "location",
        "location": "location",
        "ترتیب": "sort_order",
        "order": "sort_order",
        "وضعیت": "is_active",
        "active": "is_active",
        "نمایش": "audience",
        "audience": "audience",
        "شروع": "starts_at",
        "start": "starts_at",
        "پایان": "ends_at",
        "end": "ends_at",
    }
    data = {}
    for raw in (text or "").splitlines():
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        key = aliases.get(k.strip())
        if key:
            data[key] = v.strip()
    if "is_active" in data:
        val = data["is_active"].strip().lower()
        data["is_active"] = 0 if val in {"inactive", "off", "0", "false", "غیرفعال"} else 1
    return data


def _fmt_custom_button(row, prefer_draft=True):
    data = db.custom_button_effective_data(row, prefer_draft=prefer_draft)
    draft_mark = "📝 Draft آماده انتشار دارد" if db.custom_button_has_draft(row) else "بدون Draft"
    status = "منتشرشده" if row["status"] == "published" else "پیش‌نویس"
    active = "فعال" if int(data.get("is_active") or 0) else "غیرفعال"
    return (
        f"🎛 دکمه #{row['id']}\n\n"
        f"عنوان: {data.get('title') or '-'}\n"
        f"نوع: {BUTTON_TYPE_LABELS.get(data.get('button_type'), data.get('button_type'))}\n"
        f"متن یا لینک: {_short(data.get('payload'), 160)}\n"
        f"جایگاه: {BUTTON_LOCATION_LABELS.get(data.get('location'), data.get('location'))}\n"
        f"ترتیب: {data.get('sort_order')}\n"
        f"وضعیت: {active}\n"
        f"نمایش: {BUTTON_AUDIENCE_LABELS.get(data.get('audience'), data.get('audience'))}\n"
        f"شروع: {data.get('starts_at') or '-'}\n"
        f"پایان: {data.get('ends_at') or '-'}\n"
        f"حالت: {status}\n"
        f"Draft: {draft_mark}"
    )


async def cb_buttons(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(
        c,
        "🎛 مدیریت دکمه‌ها\n\nاز این بخش می‌توانید دکمه اختصاصی بسازید، ویرایش کنید، پیش‌نمایش بگیرید و بعد ثبت نهایی کنید. دکمه‌های سیستمی حذف نمی‌شوند.",
        reply_markup=custom_buttons_menu_kb(),
    )


async def cb_button_create(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    kb = InlineKeyboardMarkup(row_width=2)
    for btype, label in [
        ("text", "📝 متنی"),
        ("link", "🔗 لینک‌دار"),
        ("guide", "📚 آموزشی"),
        ("buy_plan", "🛒 خرید پلن"),
        ("support", "🎫 پشتیبانی"),
        ("submenu", "📂 زیرمنو"),
    ]:
        kb.insert(InlineKeyboardButton(label, callback_data=f"btn_wizard_type_{btype}"))
    kb.add(InlineKeyboardButton("⚙️ فرم پیشرفته", callback_data="btn_create_advanced"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="adm_buttons"))
    await state.update_data(button_action="create")
    await _replace_callback_message(c, "➕ ساخت دکمه جدید\n\nاول نوع دکمه را انتخاب کنید:", reply_markup=kb)


async def cb_button_create_advanced(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await state.update_data(button_action="create")
    await _replace_callback_message(c, "➕ ساخت دکمه جدید با فرم پیشرفته\n\n" + _button_form_help(), reply_markup=cancel_kb())
    await AdminStates.waiting_custom_button_form.set()


async def cb_button_wizard_type(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    btype = c.data.split("btn_wizard_type_", 1)[1]
    await state.update_data(button_action="wizard_create", button_type=btype)
    await _replace_callback_message(c, "عنوان دکمه را بفرستید.\nمثال: 📚 آموزش آیفون", reply_markup=cancel_kb())
    await AdminStates.waiting_custom_button_title.set()


async def process_custom_button_title(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text" or not m.text.strip():
        return await m.answer("لطفاً عنوان دکمه را به صورت متن بفرستید.", reply_markup=cancel_kb())
    await state.update_data(button_title=m.text.strip())
    data = await state.get_data()
    btype = data.get("button_type") or "text"
    prompts = {
        "link": "لینک مقصد را بفرستید.",
        "buy_plan": "شناسه عددی پلن را بفرستید. اگر خالی بماند، کاربر به انتخاب پلن می‌رود.",
        "file": "file_id فایل را بفرستید.",
        "support": "متن کوتاه پشتیبانی را بفرستید یا یک نقطه بفرستید تا متن پیش‌فرض استفاده شود.",
    }
    await m.answer(prompts.get(btype, "متنی که بعد از کلیک نمایش داده شود را بفرستید."), reply_markup=cancel_kb())
    await AdminStates.waiting_custom_button_payload.set()


async def process_custom_button_payload(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفاً محتوا را به صورت متن بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    payload = "" if m.text.strip() == "." else m.text.strip()
    form = {
        "title": data.get("button_title"),
        "button_type": data.get("button_type") or "text",
        "payload": payload,
        "location": "main",
        "sort_order": 100,
        "is_active": 1,
        "audience": "all",
    }
    try:
        button_id = db.create_custom_button_draft(form)
    except Exception as exc:
        await state.finish()
        return await m.answer(f"❌ ساخت دکمه ناموفق بود: {exc}", reply_markup=custom_buttons_menu_kb())
    await state.finish()
    row = db.get_custom_button(button_id)
    await m.answer(
        "✅ دکمه به‌صورت Draft ساخته شد.\n"
        "از تنظیمات پیشرفته می‌توانید جایگاه، ترتیب، گروه هدف و تاریخ را تغییر دهید؛ سپس پیش‌نمایش و ثبت نهایی کنید.\n\n"
        + _fmt_custom_button(row),
        reply_markup=custom_button_detail_kb(button_id),
    )

async def cb_button_list(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    rows = db.list_custom_buttons(limit=30)
    if not rows:
        return await _replace_callback_message(c, "هنوز دکمه اختصاصی ساخته نشده.", reply_markup=custom_buttons_menu_kb())
    kb = InlineKeyboardMarkup(row_width=1)
    lines = ["📋 لیست دکمه‌های اختصاصی:\n"]
    for row in rows:
        data = db.custom_button_effective_data(row)
        active = "✅" if int(data.get("is_active") or 0) else "🚫"
        draft = " 📝" if db.custom_button_has_draft(row) else ""
        lines.append(f"#{row['id']} | {active} {data.get('title') or '-'} | {data.get('location')} | order {data.get('sort_order')}{draft}")
        kb.add(InlineKeyboardButton(f"#{row['id']} {data.get('title') or '-'}{draft}", callback_data=f"btn_detail_{row['id']}"))
    kb.add(InlineKeyboardButton("➕ ساخت دکمه جدید", callback_data="btn_create"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="adm_buttons"))
    await _replace_callback_message(c, "\n".join(lines), reply_markup=kb)


async def cb_button_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = c.data.split("btn_detail_", 1)[1]
    row = db.get_custom_button(button_id)
    if not row:
        return await _replace_callback_message(c, "این دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
    await _replace_callback_message(c, _fmt_custom_button(row), reply_markup=custom_button_detail_kb(button_id))


async def cb_button_edit(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = c.data.split("btn_edit_", 1)[1]
    row = db.get_custom_button(button_id)
    if not row:
        return await c.message.answer("این دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
    data = db.custom_button_effective_data(row)
    current = (
        f"عنوان: {data.get('title') or ''}\n"
        f"نوع: {data.get('button_type') or 'text'}\n"
        f"متن یا لینک: {data.get('payload') or ''}\n"
        f"جایگاه: {data.get('location') or 'main'}\n"
        f"ترتیب: {data.get('sort_order') or 100}\n"
        f"وضعیت: {'active' if int(data.get('is_active') or 0) else 'inactive'}\n"
        f"نمایش: {data.get('audience') or 'all'}\n"
        f"شروع: {data.get('starts_at') or ''}\n"
        f"پایان: {data.get('ends_at') or ''}"
    )
    await state.update_data(button_action="edit", button_id=int(button_id))
    await _replace_callback_message(c, "✏️ ویرایش آزمایشی دکمه\n\n" + _button_form_help() + "\n\n" + current, reply_markup=cancel_kb())
    await AdminStates.waiting_custom_button_form.set()


async def process_custom_button_form(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفاً اطلاعات دکمه را به‌صورت متن بفرستید.", reply_markup=cancel_kb())
    form = _parse_button_form(m.text)
    data = await state.get_data()
    try:
        if data.get("button_action") == "edit":
            button_id = int(data["button_id"])
            ok = db.save_custom_button_draft(button_id, form)
            if not ok:
                await state.finish()
                return await m.answer("این دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
        else:
            button_id = db.create_custom_button_draft(form)
    except Exception as exc:
        return await m.answer(f"❌ اطلاعات دکمه معتبر نیست: {exc}\n\n" + _button_form_help(), reply_markup=cancel_kb())

    await state.finish()
    row = db.get_custom_button(button_id)
    await m.answer(
        "✅ دکمه به‌صورت Draft ذخیره شد.\n"
        "قبل از نمایش برای کاربران، پیش‌نمایش بگیرید و ثبت نهایی کنید.\n\n"
        + _fmt_custom_button(row),
        reply_markup=custom_button_detail_kb(button_id),
    )


async def cb_button_delete(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = c.data.split("btn_delete_", 1)[1]
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ بله حذف شود", callback_data=f"btn_delete_confirm_{button_id}"),
        InlineKeyboardButton("❌ لغو", callback_data=f"btn_detail_{button_id}"),
    )
    await _replace_callback_message(c, f"⚠️ حذف دکمه #{button_id}\n\nآیا مطمئن هستید؟", reply_markup=kb)


async def cb_button_delete_confirm(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = c.data.split("btn_delete_confirm_", 1)[1]
    ok = db.delete_custom_button(button_id)
    await _replace_callback_message(c, "✅ دکمه حذف شد." if ok else "دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())


async def cb_button_toggle(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = c.data.split("btn_toggle_", 1)[1]
    ok = db.stage_custom_button_toggle(button_id)
    row = db.get_custom_button(button_id)
    if not ok or not row:
        return await _replace_callback_message(c, "دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
    await _replace_callback_message(c, "👁 تغییر وضعیت به‌صورت Draft ذخیره شد. برای اعمال، ثبت نهایی کنید.\n\n" + _fmt_custom_button(row), reply_markup=custom_button_detail_kb(button_id))


async def cb_button_order(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = int(c.data.split("btn_order_", 1)[1])
    await state.update_data(button_id=button_id)
    await _replace_callback_message(c, "↕️ عدد ترتیب جدید را بفرستید. عدد کمتر بالاتر نمایش داده می‌شود.", reply_markup=cancel_kb())
    await AdminStates.waiting_button_order.set()


async def process_button_order(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text" or not m.text.strip().lstrip("-").isdigit():
        return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    row = db.get_custom_button(data["button_id"])
    if not row:
        await state.finish()
        return await m.answer("دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
    current = db.custom_button_effective_data(row)
    current["sort_order"] = int(m.text.strip())
    db.save_custom_button_draft(row["id"], current)
    await state.finish()
    row = db.get_custom_button(row["id"])
    await m.answer("✅ ترتیب جدید به‌صورت Draft ذخیره شد. برای اعمال، ثبت نهایی کنید.\n\n" + _fmt_custom_button(row), reply_markup=custom_button_detail_kb(row["id"]))


async def cb_button_location(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = int(c.data.split("btn_location_", 1)[1])
    await state.update_data(button_id=button_id)
    await _replace_callback_message(c, "📍 جایگاه جدید را بفرستید.\nمجاز: main, buy, my_services, wallet, support, guide, account", reply_markup=cancel_kb())
    await AdminStates.waiting_button_location.set()


async def process_button_location(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفاً جایگاه را به‌صورت متن بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    row = db.get_custom_button(data["button_id"])
    if not row:
        await state.finish()
        return await m.answer("دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
    current = db.custom_button_effective_data(row)
    current["location"] = m.text.strip().lower()
    try:
        db.save_custom_button_draft(row["id"], current)
    except Exception as exc:
        return await m.answer(f"❌ جایگاه معتبر نیست: {exc}", reply_markup=cancel_kb())
    await state.finish()
    row = db.get_custom_button(row["id"])
    await m.answer("✅ جایگاه جدید به‌صورت Draft ذخیره شد. برای اعمال، ثبت نهایی کنید.\n\n" + _fmt_custom_button(row), reply_markup=custom_button_detail_kb(row["id"]))


async def cb_button_preview(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = c.data.split("btn_preview_", 1)[1]
    row = db.get_custom_button(button_id)
    if not row:
        return await c.message.answer("دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
    data = db.custom_button_effective_data(row, prefer_draft=True)
    kb = InlineKeyboardMarkup(row_width=1)
    if data.get("button_type") == "link" and str(data.get("payload") or "").startswith(("http://", "https://", "tg://")):
        kb.add(InlineKeyboardButton(data.get("title") or "باز کردن لینک", url=data.get("payload")))
    else:
        kb.add(InlineKeyboardButton(data.get("title") or "دکمه نمونه", callback_data="btn_preview_noop"))
    await c.message.answer("🧪 پیش‌نمایش دکمه:\n\n" + _fmt_custom_button(row), reply_markup=kb)
    await c.message.answer("برای اعمال روی منو، ثبت نهایی را بزنید.", reply_markup=custom_button_detail_kb(button_id))


async def cb_button_preview_main(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await c.message.answer("🧪 پیش‌نمایش منوی اصلی با دکمه‌های منتشرشده:", reply_markup=menus.main_reply_kb(c.from_user.id))


async def cb_button_publish(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    button_id = c.data.split("btn_publish_", 1)[1]
    ok = db.publish_custom_button(button_id)
    row = db.get_custom_button(button_id)
    if ok and row:
        await _replace_callback_message(c, "✅ دکمه منتشر شد و طبق جایگاه/وضعیت برای کاربران نمایش داده می‌شود.\n\n" + _fmt_custom_button(row, prefer_draft=False), reply_markup=custom_button_detail_kb(button_id))
    else:
        await _replace_callback_message(c, "Draft فعالی برای انتشار وجود ندارد.", reply_markup=custom_button_detail_kb(button_id))


# -------------------- بک‌آپ و ری‌استور قوی‌تر --------------------


def backup_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📥 دریافت بک‌آپ کامل", callback_data="adm_backup"))
    kb.add(InlineKeyboardButton("🧪 تست سلامت دیتابیس فعلی", callback_data="adm_backup_health"))
    kb.add(InlineKeyboardButton("🗂 لیست بک‌آپ‌های محلی", callback_data="adm_backup_files"))
    kb.add(InlineKeyboardButton("📜 لاگ بک‌آپ و ری‌استور", callback_data="adm_backup_logs"))
    kb.add(InlineKeyboardButton("♻️ بارگذاری بک‌آپ / ری‌استور", callback_data="adm_restore"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="adm_back"))
    return kb


async def cb_backup_menu(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(
        c,
        "💾 بک‌آپ و ری‌استور\n\nقبل از ری‌استور، فایل بررسی می‌شود و از دیتابیس فعلی بک‌آپ اضطراری گرفته می‌شود.",
        reply_markup=backup_menu_kb(),
    )


async def cb_backup_health(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    info = backup.inspect_sqlite_file(db.DB_PATH)
    await _replace_callback_message(c, backup.format_backup_info(info), reply_markup=backup_menu_kb())


async def cb_backup_files(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    files = backup.list_local_backups(limit=10)
    if not files:
        return await _replace_callback_message(c, "بک‌آپ محلی ذخیره‌شده‌ای پیدا نشد.", reply_markup=backup_menu_kb())
    lines = ["🗂 آخرین بک‌آپ‌های محلی:\n"]
    for f in files:
        stat = f.stat()
        lines.append(f"• {f.name} | {stat.st_size:,} بایت | {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')}")
    await _replace_callback_message(c, "\n".join(lines), reply_markup=backup_menu_kb())


async def cb_backup_logs(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    rows = db.list_backup_logs(limit=10)
    if not rows:
        return await _replace_callback_message(c, "هنوز لاگ بک‌آپ ثبت نشده.", reply_markup=backup_menu_kb())
    lines = ["📜 آخرین لاگ‌های بک‌آپ/ری‌استور:\n"]
    for r in rows:
        lines.append(f"• #{r['id']} | {r['operation_type']} | {r['status']} | {r['backup_file_name'] or '-'} | admin {r['admin_id'] or '-'} | {r['created_at']}")
        if r["note"]:
            lines.append(f"  note: {_short(r['note'], 120)}")
    await _replace_callback_message(c, "\n".join(lines), reply_markup=backup_menu_kb())

BROADCAST_SCOPES = {
    "all": "همه کاربران غیر بن‌شده",
    "buyers": "فقط خریداران",
    "no_buy": "کاربران عضو ولی بدون خرید",
    "has_sub": "کاربران دارای سرویس تحویل‌شده",
    "no_sub": "کاربران بدون سرویس تحویل‌شده",
    "active7": "فعال‌های ۷ روز اخیر",
    "inactive7": "غیرفعال‌های ۷ روز اخیر",
    "positive_balance": "کاربران با موجودی مثبت",
    "low_balance": "موجودی مثبت ولی کمتر از قیمت پلن",
    "referred": "کاربران دعوت‌شده توسط رفرال",
    "referrers": "کاربرانی که زیرمجموعه دارند",
}


def broadcast_scope_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    for scope, label in BROADCAST_SCOPES.items():
        try:
            count = db.count_broadcast_targets(scope)
        except Exception:
            count = "?"
        kb.add(InlineKeyboardButton(f"{label} ({count})", callback_data=f"broadcast_scope_{scope}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="adm_back"))
    return kb


def broadcast_confirm_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🚀 تایید و ارسال", callback_data="broadcast_confirm"),
        InlineKeyboardButton("❌ لغو ارسال", callback_data="broadcast_cancel"),
    )
    return kb


def _broadcast_preview_text(data):
    content_type = data.get("content_type", "text")
    if content_type == "text":
        return (data.get("text") or "")[:700]
    if content_type == "photo":
        return f"[photo] {(data.get('caption') or '')[:650]}"
    if content_type == "document":
        return f"[document: {data.get('document_name') or 'document'}] {(data.get('caption') or '')[:650]}"
    return "[unknown]"


async def cb_broadcast_menu(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    recent = db.list_broadcast_logs(limit=3)
    text = (
        "📢 پیام همگانی حرفه‌ای\n\n"
        "اول جامعه هدف را انتخاب کنید. بعد متن، عکس+کپشن یا فایل/Document بفرستید.\n"
        "قبل از ارسال، پیش‌نمایش و تعداد گیرنده‌ها نمایش داده می‌شود."
    )
    if recent:
        text += "\n\nآخرین ارسال‌ها:\n"
        for log in recent:
            text += (
                f"• #{log['id']} | {BROADCAST_SCOPES.get(log['scope'], log['scope'])} | "
                f"{log['content_type']} | موفق {log['success']}/{log['total']} | {log['created_at']}\n"
            )
    await _replace_callback_message(c, text, reply_markup=broadcast_scope_menu_kb())


async def cb_broadcast_scope(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    scope = c.data.split("broadcast_scope_", 1)[1]

    if scope not in BROADCAST_SCOPES:
        return await _replace_callback_message(c, "جامعه هدف نامعتبر است.", reply_markup=admin_back_kb())

    total = db.count_broadcast_targets(scope)
    await state.update_data(scope=scope)
    await _replace_callback_message(
        c,
        f"جامعه هدف: {BROADCAST_SCOPES[scope]}\n"
        f"تعداد مخاطب: {total}\n\n"
        "حالا یکی از این‌ها را بفرستید:\n"
        "• متن ساده\n"
        "• عکس همراه کپشن اختیاری\n"
        "• فایل/Document همراه کپشن اختیاری",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_broadcast_content.set()


async def process_broadcast_content(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    scope = data.get("scope")
    if scope not in BROADCAST_SCOPES:
        await state.finish()
        return await m.answer("جامعه هدف پیدا نشد. دوباره شروع کنید.", reply_markup=admin_back_kb())

    if m.content_type == "text":
        payload = {"scope": scope, "content_type": "text", "text": m.text, "photo_file_id": "", "document_file_id": "", "document_name": "", "caption": ""}
    elif m.content_type == "photo":
        payload = {"scope": scope, "content_type": "photo", "text": "", "photo_file_id": m.photo[-1].file_id, "document_file_id": "", "document_name": "", "caption": m.caption or ""}
    elif m.content_type == "document":
        payload = {"scope": scope, "content_type": "document", "text": "", "photo_file_id": "", "document_file_id": m.document.file_id, "document_name": m.document.file_name or "document", "caption": m.caption or ""}
    else:
        return await m.answer("برای پیام همگانی فقط متن، عکس یا فایل/Document پشتیبانی می‌شود.", reply_markup=cancel_kb())

    await state.set_data(payload)
    total = db.count_broadcast_targets(scope)
    type_label = {"text": "متن", "photo": "عکس", "document": "فایل/Document"}[payload["content_type"]]
    await m.answer(
        f"🔎 پیش‌نمایش پیام همگانی\n\n"
        f"جامعه هدف: {BROADCAST_SCOPES[scope]}\n"
        f"تعداد مخاطب: {total}\n"
        f"نوع پیام: {type_label}"
    )
    if payload["content_type"] == "text":
        await m.answer(payload["text"])
    elif payload["content_type"] == "photo":
        await m.answer_photo(payload["photo_file_id"], caption=payload["caption"] or None)
    else:
        await m.answer_document(payload["document_file_id"], caption=payload["caption"] or None)
    await m.answer(
        "ارسال نهایی انجام بشه؟\n"
        "بعد از تایید، پیام به‌صورت تدریجی ارسال می‌شود تا ریسک محدودیت تلگرام کمتر شود.",
        reply_markup=broadcast_confirm_kb(),
    )
    await AdminStates.waiting_broadcast_confirm.set()


async def cb_broadcast_cancel(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer("لغو شد")
    await state.finish()
    await c.message.answer("❌ ارسال پیام همگانی لغو شد.", reply_markup=admin_back_kb())


async def cb_broadcast_confirm(c: types.CallbackQuery, state: FSMContext):
    bot = Bot.get_current()
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    data = await state.get_data()
    scope = data.get("scope")
    content_type = data.get("content_type")
    if scope not in BROADCAST_SCOPES or content_type not in {"text", "photo", "document"}:
        await state.finish()
        return await c.message.answer("اطلاعات ارسال کامل نیست. دوباره شروع کنید.", reply_markup=admin_back_kb())

    targets = db.list_broadcast_targets(scope)
    total = len(targets)
    if total == 0:
        await state.finish()
        return await c.message.answer("هیچ مخاطبی برای این جامعه هدف وجود ندارد.", reply_markup=admin_back_kb())

    progress = await c.message.answer(f"🚀 ارسال پیام همگانی شروع شد...\nمخاطب‌ها: {total}")
    success = 0
    failed = 0
    for index, user in enumerate(targets, start=1):
        uid = user["id"]
        try:
            if content_type == "text":
                await bot.send_message(int(uid), data["text"], reply_markup=menus.main_reply_kb(uid))
            elif content_type == "photo":
                await bot.send_photo(int(uid), data["photo_file_id"], caption=data.get("caption") or None, reply_markup=menus.main_reply_kb(uid))
            else:
                await bot.send_document(int(uid), data["document_file_id"], caption=data.get("caption") or None, reply_markup=menus.main_reply_kb(uid))
            success += 1
        except Exception:
            failed += 1

        if index % 25 == 0 or index == total:
            try:
                await progress.edit_text(f"📢 در حال ارسال...\nپیشرفت: {index}/{total}\nموفق: {success}\nناموفق: {failed}")
            except Exception:
                pass
        await asyncio.sleep(BROADCAST_DELAY)

    preview = _broadcast_preview_text(data)
    log_id = db.log_broadcast(c.from_user.id, scope, content_type, preview, total, success, failed)
    await state.finish()
    await c.message.answer(
        f"✅ پیام همگانی ارسال شد.\n\n"
        f"Log ID: #{log_id}\n"
        f"جامعه هدف: {BROADCAST_SCOPES[scope]}\n"
        f"کل مخاطب: {total}\n"
        f"موفق: {success}\n"
        f"ناموفق: {failed}",
        reply_markup=admin_back_kb(),
    )



async def cb_backup(c: types.CallbackQuery):
    bot = Bot.get_current()
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer("در حال آماده‌سازی بک‌آپ...")
    path = await backup.send_backup(bot, c.from_user.id, admin_id=c.from_user.id)
    await c.message.answer(f"✅ بک‌آپ ارسال و ذخیره شد.\nمسیر محلی: {path}", reply_markup=backup_menu_kb())


async def cb_restore_start(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    if not is_owner(c.from_user.id):
        return await c.answer("فقط مالک اصلی اجازه ری‌استور دارد.", show_alert=True)

    await c.answer()
    await _replace_callback_message(
        c,
        "⚠️ فایل دیتابیس (.db) رو به‌صورت Document بفرستید.\n\n"
        "قبل از ری‌استور، فایل بررسی می‌شود و از دیتابیس فعلی بک‌آپ اضطراری گرفته می‌شود.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_restore_file.set()


async def process_restore_file(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if not is_owner(m.from_user.id):
        await state.finish()
        return await m.answer("فقط مالک اصلی اجازه ری‌استور دارد.", reply_markup=backup_menu_kb())

    if m.content_type != "document":
        return await m.answer("لطفا فایل دیتابیس رو به‌صورت Document بفرستید.", reply_markup=cancel_kb())

    await m.answer("⏳ در حال دانلود و بررسی فایل...")
    tmp_path = f"/tmp/restore_upload_{m.document.file_unique_id}.db"
    await m.document.download(destination_file=tmp_path)

    info = backup.inspect_sqlite_file(tmp_path)
    if not info.get("ok"):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        await state.finish()
        return await m.answer("❌ این فایل برای ری‌استور معتبر نیست.\n\n" + backup.format_backup_info(info), reply_markup=backup_menu_kb())

    await state.update_data(restore_path=tmp_path)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ بله، ری‌استور انجام شود", callback_data="restore_confirm"),
        InlineKeyboardButton("❌ لغو", callback_data="restore_cancel"),
    )
    await m.answer(
        backup.format_backup_info(info)
        + "\n\n⚠️ مطمئن هستید می‌خواهید دیتابیس فعلی جایگزین شود؟",
        reply_markup=kb,
    )
    await AdminStates.waiting_restore_confirm.set()


async def cb_restore_cancel(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer("لغو شد")
    data = await state.get_data()
    tmp_path = data.get("restore_path")
    if tmp_path:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    await state.finish()
    await c.message.answer("❌ ری‌استور لغو شد.", reply_markup=backup_menu_kb())


async def cb_restore_confirm(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()

    if not is_owner(c.from_user.id):
        return await c.answer("فقط مالک اصلی اجازه ری‌استور دارد.", show_alert=True)

    await c.answer()
    data = await state.get_data()
    tmp_path = data.get("restore_path")
    if not tmp_path or not os.path.exists(tmp_path):
        await state.finish()
        return await c.message.answer("فایل موقت ری‌استور پیدا نشد. دوباره تلاش کنید.", reply_markup=backup_menu_kb())

    await c.message.answer("⏳ در حال ری‌استور... ابتدا بک‌آپ اضطراری از دیتابیس فعلی گرفته می‌شود.")

    # perform_restore دیتابیس فعلی را می‌بندد و فایل DB را جایگزین می‌کند.
    # پس پاک‌کردن state باید قبل از بسته‌شدن connection انجام شود.
    await state.finish()

    try:
        safety_path = backup.perform_restore(tmp_path, admin_id=c.from_user.id)
    except Exception as exc:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return await c.message.answer(f"❌ ری‌استور انجام نشد: {exc}", reply_markup=backup_menu_kb())

    try:
        os.remove(tmp_path)
    except OSError:
        pass

    await c.message.answer(f"✅ بازگردانی انجام شد.\nنسخه امن قبلی:\n{safety_path}\n\nربات الان ری‌استارت میشه...")
    logging.getLogger(__name__).warning("Database restored by admin %s, restarting process.", c.from_user.id)
    sys.exit(1)

def register(dp):
    dp.register_message_handler(cmd_admin, commands=[ADMIN_COMMAND])
    dp.register_callback_query_handler(cb_open_panel, lambda c: c.data == "open_admin_panel")
    dp.register_callback_query_handler(cb_back, lambda c: c.data == "adm_back")
    dp.register_callback_query_handler(cb_section_users, lambda c: c.data == "adm_section_users")
    dp.register_callback_query_handler(cb_section_services, lambda c: c.data == "adm_section_services")
    dp.register_callback_query_handler(cb_section_finance, lambda c: c.data == "adm_section_finance")
    dp.register_callback_query_handler(cb_section_personalize, lambda c: c.data == "adm_section_personalize")
    dp.register_callback_query_handler(cb_section_reports, lambda c: c.data == "adm_section_reports")

    dp.register_callback_query_handler(cb_users, lambda c: c.data == "adm_users")
    dp.register_callback_query_handler(cb_user_profile_info, lambda c: c.data.startswith("adm_user_profile_"))
    dp.register_callback_query_handler(cb_user_detail, lambda c: c.data.startswith("adm_user_") and not c.data.startswith(("adm_user_note_", "adm_user_test_", "adm_user_profile_")))
    dp.register_callback_query_handler(cb_user_note, lambda c: c.data.startswith("adm_user_note_"))
    dp.register_message_handler(process_user_note, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_user_note)
    dp.register_callback_query_handler(cb_user_test_toggle, lambda c: c.data.startswith("adm_user_test_"))
    dp.register_callback_query_handler(cb_direct_message_start, lambda c: c.data.startswith("adm_msg_user_"))
    dp.register_message_handler(process_direct_message, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_direct_message)
    dp.register_callback_query_handler(cb_resend_link, lambda c: c.data.startswith("adm_resend_link_"))
    dp.register_callback_query_handler(cb_resend_qr, lambda c: c.data.startswith("adm_resend_qr_"))

    dp.register_callback_query_handler(cb_search, lambda c: c.data == "adm_search")
    dp.register_message_handler(process_search, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_search)

    dp.register_callback_query_handler(cb_addbal, lambda c: c.data == "adm_addbal")
    dp.register_message_handler(process_balance_id, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_balance_id)
    dp.register_message_handler(process_balance_amount, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_balance_amount)

    dp.register_callback_query_handler(cb_ban, lambda c: c.data == "adm_ban")
    dp.register_message_handler(process_ban, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_ban_id)

    dp.register_callback_query_handler(cb_unban, lambda c: c.data == "adm_unban")
    dp.register_message_handler(process_unban, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_unban_id)

    dp.register_callback_query_handler(cb_links, lambda c: c.data == "adm_links")
    dp.register_callback_query_handler(cb_links_available, lambda c: c.data == "adm_links_available")
    dp.register_callback_query_handler(cb_links_delivered, lambda c: c.data == "adm_links_delivered")
    dp.register_callback_query_handler(cb_link_detail, lambda c: c.data.startswith("adm_link_detail_"))
    dp.register_callback_query_handler(cb_link_delete_ask, lambda c: c.data.startswith("adm_link_delete_ask_"))
    dp.register_callback_query_handler(cb_link_delete_confirm, lambda c: c.data.startswith("adm_link_delete_confirm_"))
    dp.register_callback_query_handler(cb_link_repool_ask, lambda c: c.data.startswith("adm_link_repool_ask_"))
    dp.register_callback_query_handler(cb_link_repool_confirm, lambda c: c.data.startswith("adm_link_repool_confirm_"))
    dp.register_callback_query_handler(cb_link_add, lambda c: c.data == "adm_link_add")
    dp.register_callback_query_handler(cb_link_search, lambda c: c.data == "adm_link_search")
    dp.register_message_handler(process_link_search, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_link_search)
    dp.register_callback_query_handler(cb_link_delete_manual, lambda c: c.data == "adm_link_delete_manual")
    dp.register_message_handler(process_link_delete_id, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_link_delete_id)

    dp.register_callback_query_handler(cb_addsub, lambda c: c.data == "adm_addsub")
    dp.register_callback_query_handler(cb_addsub_plan, lambda c: c.data.startswith("adm_addsub_plan_"))
    dp.register_message_handler(process_addsub, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_add_sub)

    dp.register_callback_query_handler(cb_topups, lambda c: c.data == "adm_topups")
    dp.register_callback_query_handler(cb_stats, lambda c: c.data == "adm_stats")
    dp.register_callback_query_handler(cb_sales_report, lambda c: c.data == "adm_sales_report")
    dp.register_callback_query_handler(cb_admin_logs, lambda c: c.data == "adm_admin_logs")

    dp.register_callback_query_handler(cb_plans, lambda c: c.data == "adm_plans")
    dp.register_callback_query_handler(cb_plan_create, lambda c: c.data == "plan_create")
    dp.register_callback_query_handler(cb_plan_detail, lambda c: c.data.startswith("plan_detail_"))
    dp.register_callback_query_handler(cb_plan_edit, lambda c: c.data.startswith("plan_edit_"))
    dp.register_callback_query_handler(cb_plan_toggle, lambda c: c.data.startswith("plan_toggle_"))
    dp.register_message_handler(process_plan_form, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_plan_form)

    dp.register_callback_query_handler(cb_settings, lambda c: c.data == "adm_settings")
    dp.register_callback_query_handler(cb_setkey, lambda c: c.data.startswith("setkey_"))
    dp.register_message_handler(process_setting_value, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_setting_value)

    dp.register_callback_query_handler(cb_messages, lambda c: c.data == "adm_messages")
    dp.register_callback_query_handler(cb_msg_category, lambda c: c.data.startswith("msgcat_"))
    dp.register_callback_query_handler(cb_msgkey, lambda c: c.data.startswith("msgkey_"))
    dp.register_callback_query_handler(cb_msg_default, lambda c: c.data.startswith("msg_default_"))
    dp.register_callback_query_handler(cb_msg_copy_default, lambda c: c.data.startswith("msg_copy_default_"))
    dp.register_callback_query_handler(cb_msg_edit_start, lambda c: c.data.startswith("msg_edit_"))
    dp.register_callback_query_handler(cb_msg_prefix, lambda c: c.data.startswith("msg_prefix_"))
    dp.register_callback_query_handler(cb_msg_suffix, lambda c: c.data.startswith("msg_suffix_"))
    dp.register_callback_query_handler(cb_msg_preview, lambda c: c.data.startswith("msg_preview_"))
    dp.register_callback_query_handler(cb_msg_publish, lambda c: c.data.startswith("msg_publish_"))
    dp.register_callback_query_handler(cb_msg_clear_draft, lambda c: c.data.startswith("msg_clear_draft_"))
    dp.register_callback_query_handler(cb_msg_clear_published, lambda c: c.data.startswith("msg_clear_published_"))
    dp.register_message_handler(process_message_edit, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_message_edit)

    dp.register_callback_query_handler(cb_buttons, lambda c: c.data == "adm_buttons")
    dp.register_callback_query_handler(cb_button_create, lambda c: c.data == "btn_create")
    dp.register_callback_query_handler(cb_button_create_advanced, lambda c: c.data == "btn_create_advanced")
    dp.register_callback_query_handler(cb_button_wizard_type, lambda c: c.data.startswith("btn_wizard_type_"))
    dp.register_message_handler(process_custom_button_title, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_custom_button_title)
    dp.register_message_handler(process_custom_button_payload, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_custom_button_payload)
    dp.register_callback_query_handler(cb_system_buttons, lambda c: c.data == "sysbtn_list")
    dp.register_callback_query_handler(cb_system_button_detail, lambda c: c.data.startswith("sysbtn_detail_"))
    dp.register_callback_query_handler(cb_system_button_title, lambda c: c.data.startswith("sysbtn_title_"))
    dp.register_message_handler(process_system_button_title, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_system_button_title)
    dp.register_callback_query_handler(cb_system_button_order, lambda c: c.data.startswith("sysbtn_order_"))
    dp.register_message_handler(process_system_button_order, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_system_button_order)
    dp.register_callback_query_handler(cb_system_button_location, lambda c: c.data.startswith("sysbtn_location_"))
    dp.register_message_handler(process_system_button_location, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_system_button_location)
    dp.register_callback_query_handler(cb_system_button_toggle, lambda c: c.data.startswith("sysbtn_toggle_"))
    dp.register_callback_query_handler(cb_system_button_reset, lambda c: c.data.startswith("sysbtn_reset_"))
    dp.register_callback_query_handler(cb_button_list, lambda c: c.data == "btn_list")
    dp.register_callback_query_handler(cb_button_detail, lambda c: c.data.startswith("btn_detail_"))
    dp.register_callback_query_handler(cb_button_edit, lambda c: c.data.startswith("btn_edit_"))
    dp.register_message_handler(process_custom_button_form, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_custom_button_form)
    dp.register_callback_query_handler(cb_button_delete_confirm, lambda c: c.data.startswith("btn_delete_confirm_"))
    dp.register_callback_query_handler(cb_button_delete, lambda c: c.data.startswith("btn_delete_"))
    dp.register_callback_query_handler(cb_button_toggle, lambda c: c.data.startswith("btn_toggle_"))
    dp.register_callback_query_handler(cb_button_order, lambda c: c.data.startswith("btn_order_"))
    dp.register_message_handler(process_button_order, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_button_order)
    dp.register_callback_query_handler(cb_button_location, lambda c: c.data.startswith("btn_location_"))
    dp.register_message_handler(process_button_location, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_button_location)
    dp.register_callback_query_handler(cb_button_preview_main, lambda c: c.data == "btn_preview_main")
    dp.register_callback_query_handler(cb_button_preview, lambda c: c.data.startswith("btn_preview_"))
    dp.register_callback_query_handler(cb_button_publish, lambda c: c.data.startswith("btn_publish_"))
    dp.register_callback_query_handler(lambda c: c.answer("این فقط پیش‌نمایش است."), lambda c: c.data == "btn_preview_noop")

    dp.register_callback_query_handler(cb_broadcast_menu, lambda c: c.data == "adm_broadcast")
    dp.register_callback_query_handler(cb_broadcast_scope, lambda c: c.data.startswith("broadcast_scope_"))
    dp.register_message_handler(process_broadcast_content, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_broadcast_content)
    dp.register_callback_query_handler(cb_broadcast_confirm, lambda c: c.data == "broadcast_confirm", state=AdminStates.waiting_broadcast_confirm)
    dp.register_callback_query_handler(cb_broadcast_cancel, lambda c: c.data == "broadcast_cancel", state=AdminStates.waiting_broadcast_confirm)

    dp.register_callback_query_handler(cb_backup_menu, lambda c: c.data == "adm_backup_menu")
    dp.register_callback_query_handler(cb_backup, lambda c: c.data == "adm_backup")
    dp.register_callback_query_handler(cb_backup_health, lambda c: c.data == "adm_backup_health")
    dp.register_callback_query_handler(cb_backup_files, lambda c: c.data == "adm_backup_files")
    dp.register_callback_query_handler(cb_backup_logs, lambda c: c.data == "adm_backup_logs")
    dp.register_callback_query_handler(cb_restore_start, lambda c: c.data == "adm_restore")
    dp.register_message_handler(process_restore_file, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_restore_file)
    dp.register_callback_query_handler(cb_restore_confirm, lambda c: c.data == "restore_confirm", state=AdminStates.waiting_restore_confirm)
    dp.register_callback_query_handler(cb_restore_cancel, lambda c: c.data == "restore_cancel", state=AdminStates.waiting_restore_confirm)
