import asyncio
import logging
import os
import sys

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
from config import ADMIN_COMMAND, ADMIN_IDS, BROADCAST_DELAY
from utils import cleanup_qr, make_qr


def is_admin(user_id) -> bool:
    return int(user_id) in ADMIN_IDS


class AdminStates(StatesGroup):
    waiting_search = State()
    waiting_balance_id = State()
    waiting_balance_amount = State()
    waiting_ban_id = State()
    waiting_unban_id = State()
    waiting_add_sub = State()
    waiting_setting_value = State()
    waiting_message_edit = State()
    waiting_restore_file = State()
    waiting_broadcast_content = State()
    waiting_broadcast_confirm = State()


def cancel_kb():
    return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))


def admin_back_kb():
    return menus.admin_back_inline()


def admin_menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👥 کاربران", callback_data="adm_users"),
        InlineKeyboardButton("🔎 جستجو", callback_data="adm_search"),
        InlineKeyboardButton("➕ موجودی دستی", callback_data="adm_addbal"),
        InlineKeyboardButton("⛔ بن", callback_data="adm_ban"),
        InlineKeyboardButton("✅ آنبن", callback_data="adm_unban"),
        InlineKeyboardButton("➕ افزودن لینک", callback_data="adm_addsub"),
        InlineKeyboardButton("💳 شارژهای در انتظار", callback_data="adm_topups"),
        InlineKeyboardButton("🎫 تیکت‌های باز", callback_data="adm_tickets"),
        InlineKeyboardButton("📊 آمار و درآمد", callback_data="adm_stats"),
        InlineKeyboardButton("📢 پیام همگانی", callback_data="adm_broadcast"),
        InlineKeyboardButton("⚙️ تنظیمات", callback_data="adm_settings"),
        InlineKeyboardButton("📝 ویرایش پیام‌ها", callback_data="adm_messages"),
        InlineKeyboardButton("💾 دریافت بک‌آپ", callback_data="adm_backup"),
        InlineKeyboardButton("♻️ بارگذاری بک‌آپ", callback_data="adm_restore"),
    )
    kb.add(InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def _fmt_money(amount):
    return f"{int(amount or 0):,} تومان"


def _short(value, size=45):
    value = value or "-"
    if len(value) <= size:
        return value
    return value[:size] + "..."


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
    kb.add(InlineKeyboardButton("🔄 بروزرسانی جزئیات", callback_data=f"adm_user_{user_id}"))
    kb.add(InlineKeyboardButton("💳 افزایش / کاهش موجودی", callback_data="adm_addbal"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به کاربران", callback_data="adm_users"))
    kb.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm_back"))
    return kb


def user_services_kb(user_id):
    rows = subs.user_subs(user_id, limit=8)
    kb = InlineKeyboardMarkup(row_width=2)

    for row in rows:
        label = row["account_name"] or f"Sub #{row['id']}"
        kb.add(
            InlineKeyboardButton(f"🔗 ارسال لینک {label}", callback_data=f"adm_resend_link_{row['id']}_{user_id}"),
            InlineKeyboardButton(f"🔳 QR {label}", callback_data=f"adm_resend_qr_{row['id']}_{user_id}"),
        )

    kb.add(InlineKeyboardButton("⬅️ بازگشت به جزئیات", callback_data=f"adm_user_{user_id}"))
    return kb


def _fmt_user_detail(user_id):
    user = db.get_user(user_id)
    if not user:
        return "کاربر پیدا نشد."

    status = "⛔ بن شده" if user["banned"] else "✅ فعال"
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
        f"وضعیت: {status}\n"
        f"موجودی کیف پول: {_fmt_money(user['balance'])}\n"
        f"تعداد خرید ثبت‌شده روی کاربر: {user['purchased']}\n"
        f"تعداد سرویس تحویل‌شده: {db.delivered_sub_count_by_user(user_id)}\n"
        f"معرف: {referrer_text}\n"
        f"عضویت: {user['joined_at']}\n"
        f"آخرین فعالیت: {user['last_active']}\n"
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
            text += f"• {row['id']} @{row['username'] or '-'} | {mark} | خرید: {row['purchased']}\n"

    text += "\n🧾 خریدها\n"
    if purchases:
        for p in purchases[:7]:
            text += (
                f"• خرید #{p['id']} | تعداد {p['quantity']} | "
                f"مبلغ {_fmt_money(p['amount'])} | قیمت واحد {_fmt_money(p['unit_price'])} | {p['created_at']}\n"
            )
    else:
        text += "خرید ثبت نشده.\n"

    text += "\n📦 سرویس‌های تحویل‌شده\n"
    if owned:
        for s in owned[:12]:
            text += (
                f"• Sub #{s['id']} | {s['account_name'] or '-'}\n"
                f"  خرید/تحویل: {s['assigned_at'] or '-'} | مبلغ: {_fmt_money(s['price_paid'])}\n"
                f"  وضعیت: {s['status'] or 'delivered'} | خرید #{s['purchase_id'] or '-'}\n"
                f"  لینک کوتاه: {_short(s['link'])}\n"
            )
    else:
        text += "سرویسی به این کاربر تحویل نشده.\n"

    text += "\n💳 شارژهای کیف پول\n"
    if topups:
        for t in topups:
            text += f"• شارژ #{t['id']} | {_fmt_money(t['amount'])} | {t['status']} | {t['created_at']}\n"
    else:
        text += "شارژ ثبت نشده.\n"

    text += "\n📒 آخرین تراکنش‌های کیف پول\n"
    if ledger:
        for l in ledger:
            text += (
                f"• #{l['id']} | {l['action']} | {_fmt_money(l['amount'])}\n"
                f"  قبل: {_fmt_money(l['balance_before'])} | بعد: {_fmt_money(l['balance_after'])} | {l['created_at']}\n"
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
            text += f"• تیکت #{t['id']} | {t['status']} | {t['created_at']}\n"

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


async def cb_users(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    rows = db.list_users(limit=15)

    if not rows:
        return await _replace_callback_message(c, "هیچ کاربری ثبت نشده.", reply_markup=admin_back_kb())

    lines = ["👥 آخرین ۱۵ کاربر:\n"]
    kb = InlineKeyboardMarkup(row_width=1)

    for r in rows:
        flag = "⛔" if r["banned"] else "✅"
        username = f"@{r['username']}" if r["username"] else "بدون یوزرنیم"
        delivered = db.delivered_sub_count_by_user(r["id"])
        lines.append(
            f"{flag} {r['id']} | {username} | خرید: {r['purchased']} | سرویس: {delivered} | موجودی: {_fmt_money(r['balance'])}"
        )
        kb.add(InlineKeyboardButton(f"👤 جزئیات {username} | {r['id']}", callback_data=f"adm_user_{r['id']}"))

    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    await _replace_callback_message(c, "\n".join(lines), reply_markup=kb)


async def cb_user_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_", 1)[1]
    await _send_long(c.message, _fmt_user_detail(user_id), reply_markup=user_detail_kb(user_id))
    if subs.user_subs(user_id, limit=1):
        await c.message.answer("🔁 عملیات سریع روی سرویس‌های این کاربر:", reply_markup=user_services_kb(user_id))


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
    await state.finish()
    await m.answer(f"✅ کاربر {target} آنبن شد.", reply_markup=admin_back_kb())


async def cb_addsub(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(
        c,
        "لینک(های) ساب رو بفرستید. برای افزودن چند لینک هم‌زمان، هرکدوم رو در یک خط جدا بنویسید.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_add_sub.set()


async def process_addsub(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفا لینک(ها) رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())
    count = subs.add_subs_bulk(m.text.splitlines())
    await state.finish()
    await m.answer(f"✅ {count} لینک اضافه شد.\nموجودی فعلی: {subs.stock_count()}", reply_markup=admin_back_kb())


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


def messages_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    for key, label in messages.MESSAGE_KEYS:
        kb.add(InlineKeyboardButton(label, callback_data=f"msgkey_{key}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت", callback_data="adm_back"))
    return kb


async def cb_messages(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(
        c,
        "📝 کدوم پیام رو می‌خواید ویرایش کنید؟",
        reply_markup=messages_menu_kb(),
    )


async def cb_msgkey(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("msgkey_", 1)[1]
    label = dict(messages.MESSAGE_KEYS).get(key, key)
    current_text, current_photo = messages.get(key)
    await state.update_data(message_key=key)
    info = f"وضعیت فعلی «{label}»:\nمتن بنر: {current_text or '(چیزی تنظیم نشده)'}\nعکس: {'دارد' if current_photo else '(چیزی تنظیم نشده)'}"
    await c.message.answer(info + "\n\nمتن یا عکس جدید را بفرستید. برای پاک کردن /clear بفرستید.", reply_markup=cancel_kb())
    await AdminStates.waiting_message_edit.set()


async def process_message_edit(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    key = data["message_key"]
    if m.content_type == "text":
        if m.text.strip() == "/clear":
            messages.clear(key)
            await state.finish()
            return await m.answer("✅ بنر پاک شد.", reply_markup=messages_menu_kb())
        messages.set_text(key, m.text)
        await state.finish()
        return await m.answer("✅ متن بنر به‌روزرسانی شد.", reply_markup=messages_menu_kb())
    if m.content_type == "photo":
        messages.set_photo(key, m.photo[-1].file_id)
        if m.caption:
            messages.set_text(key, m.caption)
        await state.finish()
        return await m.answer("✅ عکس/متن به‌روزرسانی شد.", reply_markup=messages_menu_kb())
    await m.answer("لطفا فقط متن یا عکس بفرستید.", reply_markup=cancel_kb())


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
    await backup.send_backup(bot, c.from_user.id)
    await c.message.answer("✅ بک‌آپ ارسال شد.", reply_markup=admin_back_kb())


async def cb_restore_start(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await _replace_callback_message(c, "⚠️ فایل دیتابیس (.db) رو بفرستید.", reply_markup=cancel_kb())
    await AdminStates.waiting_restore_file.set()


async def process_restore_file(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "document":
        return await m.answer("لطفا فایل دیتابیس رو به‌صورت Document بفرستید.", reply_markup=cancel_kb())
    await state.finish()
    await m.answer("⏳ در حال بررسی و بازگردانی فایل...")
    tmp_path = f"/tmp/restore_upload_{m.document.file_unique_id}.db"
    await m.document.download(destination_file=tmp_path)
    if not backup.validate_sqlite_file(tmp_path):
        os.remove(tmp_path)
        return await m.answer("❌ این فایل دیتابیس معتبر نیست.", reply_markup=admin_back_kb())
    safety_path = backup.perform_restore(tmp_path)
    os.remove(tmp_path)
    await m.answer(f"✅ بازگردانی انجام شد.\nنسخه امن قبلی:\n{safety_path}\n\nربات الان ری‌استارت میشه...")
    logging.getLogger(__name__).warning("Database restored by admin %s, restarting process.", m.from_user.id)
    sys.exit(1)


def register(dp):
    dp.register_message_handler(cmd_admin, commands=[ADMIN_COMMAND])
    dp.register_callback_query_handler(cb_open_panel, lambda c: c.data == "open_admin_panel")
    dp.register_callback_query_handler(cb_back, lambda c: c.data == "adm_back")

    dp.register_callback_query_handler(cb_users, lambda c: c.data == "adm_users")
    dp.register_callback_query_handler(cb_user_detail, lambda c: c.data.startswith("adm_user_"))
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

    dp.register_callback_query_handler(cb_addsub, lambda c: c.data == "adm_addsub")
    dp.register_message_handler(process_addsub, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_add_sub)

    dp.register_callback_query_handler(cb_topups, lambda c: c.data == "adm_topups")
    dp.register_callback_query_handler(cb_stats, lambda c: c.data == "adm_stats")

    dp.register_callback_query_handler(cb_settings, lambda c: c.data == "adm_settings")
    dp.register_callback_query_handler(cb_setkey, lambda c: c.data.startswith("setkey_"))
    dp.register_message_handler(process_setting_value, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_setting_value)

    dp.register_callback_query_handler(cb_messages, lambda c: c.data == "adm_messages")
    dp.register_callback_query_handler(cb_msgkey, lambda c: c.data.startswith("msgkey_"))
    dp.register_message_handler(process_message_edit, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_message_edit)

    dp.register_callback_query_handler(cb_broadcast_menu, lambda c: c.data == "adm_broadcast")
    dp.register_callback_query_handler(cb_broadcast_scope, lambda c: c.data.startswith("broadcast_scope_"))
    dp.register_message_handler(process_broadcast_content, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_broadcast_content)
    dp.register_callback_query_handler(cb_broadcast_confirm, lambda c: c.data == "broadcast_confirm", state=AdminStates.waiting_broadcast_confirm)
    dp.register_callback_query_handler(cb_broadcast_cancel, lambda c: c.data == "broadcast_cancel", state=AdminStates.waiting_broadcast_confirm)

    dp.register_callback_query_handler(cb_backup, lambda c: c.data == "adm_backup")
    dp.register_callback_query_handler(cb_restore_start, lambda c: c.data == "adm_restore")
    dp.register_message_handler(process_restore_file, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_restore_file)
