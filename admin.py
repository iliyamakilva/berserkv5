"""
پنل مدیریت متنی داخل تلگرام.

Patch mode:
- فرمت فایل درست شد.
- دکمه بازگشت به پنل مدیریت در خروجی‌های اصلی وجود دارد.
- منطق فعلی حذف نشده؛ مسیرها به همان db/settings/messages/subs وصل هستند.
"""

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
from config import ADMIN_COMMAND, ADMIN_IDS


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


def cancel_kb():
    return InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm")
    )


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
        InlineKeyboardButton("⚙️ تنظیمات", callback_data="adm_settings"),
        InlineKeyboardButton("📝 ویرایش پیام‌ها", callback_data="adm_messages"),
        InlineKeyboardButton("💾 دریافت بک‌آپ", callback_data="adm_backup"),
        InlineKeyboardButton("♻️ بارگذاری بک‌آپ", callback_data="adm_restore"),
    )
    kb.add(InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def admin_back_kb():
    return menus.admin_back_inline()


async def cmd_admin(m: types.Message):
    if not is_admin(m.from_user.id):
        return

    await m.answer("⚙️ پنل مدیریت Berserk VPN", reply_markup=admin_menu_kb())


async def cb_open_panel(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await c.message.answer("⚙️ پنل مدیریت Berserk VPN", reply_markup=admin_menu_kb())


async def cb_users(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    rows = db.list_users(limit=15)

    if not rows:
        return await c.message.answer("هیچ کاربری ثبت نشده.", reply_markup=admin_back_kb())

    lines = ["👥 آخرین ۱۵ کاربر:\n"]

    for r in rows:
        flag = "⛔" if r["banned"] else "✅"
        lines.append(
            f"{flag} `{r['id']}` @{r['username'] or '-'} | "
            f"موجودی: {r['balance']:,} | خرید: {r['purchased']}"
        )

    await c.message.answer("\n".join(lines), parse_mode="Markdown", reply_markup=admin_back_kb())


async def cb_search(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await c.message.answer("آیدی عددی یا یوزرنیم کاربر رو بفرستید:", reply_markup=cancel_kb())
    await AdminStates.waiting_search.set()


async def process_search(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text":
        return await m.answer("لطفاً آیدی یا یوزرنیم رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())

    rows = db.search_users(m.text)
    await state.finish()

    if not rows:
        return await m.answer("چیزی پیدا نشد.", reply_markup=admin_back_kb())

    for r in rows[:10]:
        flag = "⛔ بن شده" if r["banned"] else "✅ فعال"
        refs = db.referral_count(r["id"])
        owned = subs.user_subs(r["id"])

        text = (
            f"👤 `{r['id']}` @{r['username'] or '-'}\n"
            f"وضعیت: {flag}\n"
            f"موجودی: {r['balance']:,}\n"
            f"تعداد خرید: {r['purchased']}\n"
            f"تعداد ساب اختصاص‌یافته: {len(owned)}\n"
            f"زیرمجموعه: {refs} نفر\n"
            f"عضویت: {r['joined_at']}"
        )

        if owned:
            text += "\n\n📦 سرویس‌های این کاربر:\n"
            for s in owned[:15]:
                text += (
                    f"• {s['account_name'] or '-'} | "
                    f"{s['price_paid'] or 0:,} تومان | "
                    f"{s['assigned_at'] or '-'}\n"
                )

        await m.answer(text, parse_mode="Markdown", reply_markup=admin_back_kb())


async def cb_addbal(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await c.message.answer("آیدی کاربر رو بفرستید:", reply_markup=cancel_kb())
    await AdminStates.waiting_balance_id.set()


async def process_balance_id(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text":
        return await m.answer("لطفاً آیدی کاربر رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())

    target = m.text.strip()

    if not db.get_user(target):
        return await m.answer("این کاربر پیدا نشد. دوباره بفرستید یا لغو کنید:", reply_markup=cancel_kb())

    await state.update_data(target_id=target)
    await m.answer(
        "چه مبلغی اضافه/کم بشه؟ برای کسر، عدد منفی بفرستید مثل -10000",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_balance_amount.set()


async def process_balance_amount(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text":
        return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())

    data = await state.get_data()

    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())

    db.add_balance(data["target_id"], amount)
    await state.finish()
    await m.answer(f"✅ موجودی کاربر {data['target_id']} به‌روزرسانی شد.", reply_markup=admin_back_kb())


async def cb_ban(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await c.message.answer("آیدی کاربری که باید بن بشه رو بفرستید:", reply_markup=cancel_kb())
    await AdminStates.waiting_ban_id.set()


async def process_ban(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text":
        return await m.answer("لطفاً آیدی رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())

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
    await c.message.answer("آیدی کاربری که باید آنبن بشه رو بفرستید:", reply_markup=cancel_kb())
    await AdminStates.waiting_unban_id.set()


async def process_unban(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text":
        return await m.answer("لطفاً آیدی رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())

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
    await c.message.answer(
        "لینک(های) ساب رو بفرستید.\nبرای چند لینک، هرکدوم در یک خط جدا.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_add_sub.set()


async def process_addsub(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text":
        return await m.answer("لطفاً لینک(ها) رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())

    count = subs.add_subs_bulk(m.text.splitlines())

    await state.finish()
    await m.answer(
        f"✅ {count} لینک اضافه شد.\nموجودی فعلی: {subs.stock_count()}",
        reply_markup=admin_back_kb(),
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
        f"کل کاربران: {db.count_users()}",
        f"فعال ۷ روز اخیر: {db.active_users_count(7)}",
        f"شارژهای تایید‌شده: {db.sum_approved_topups():,} تومان",
        f"مجموع موجودی کیف‌پول‌ها: {db.sum_all_balances():,} تومان",
        f"پاداش رفرال پرداختی: {db.total_referral_rewards():,} تومان",
        f"شارژهای در انتظار: {db.count_pending_topups()}",
        f"تیکت‌های باز: {db.count_open_tickets()}",
        f"فروش ساب: {subs.sold_count()}",
        f"موجودی ساب: {subs.stock_count()}",
        "",
        "📈 روند روزانه:",
    ]

    for row in db.recent_daily_stats(7):
        lines.append(
            f"{row['day']}: کاربر جدید {row['new_users']} | فروش {row['sales']} | رفرال {row['referral_rewards']:,}"
        )

    await c.message.answer("\n".join(lines), reply_markup=admin_back_kb())


SETTING_FIELDS = [
    ("plan_title", "عنوان پلن", settings.plan_title, "text"),
    ("plan_duration_label", "مدت پلن", settings.plan_duration_label, "text"),
    ("plan_price", "قیمت پلن", settings.plan_price, "int"),
    ("ref_reward", "پاداش رفرال", settings.ref_reward, "int"),
    ("card_number", "شماره کارت", settings.card_number, "text"),
    ("card_holder", "نام صاحب کارت", settings.card_holder, "text"),
    ("min_topup", "حداقل شارژ", settings.min_topup, "int"),
    ("low_stock_threshold", "هشدار موجودی کم", settings.low_stock_threshold, "int"),
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
    await c.message.answer("⚙️ تنظیمات:", reply_markup=settings_menu_kb())


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
    await c.message.answer(
        f"مقدار جدید برای «{label}»{hint} رو بفرستید.\nمقدار فعلی: {current}",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_setting_value.set()


async def process_setting_value(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "text":
        return await m.answer("لطفاً مقدار جدید رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())

    data = await state.get_data()
    key, ftype = data["setting_key"], data["setting_type"]
    value = m.text.strip()

    if ftype == "int":
        if not value.lstrip("-").isdigit():
            return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())
        value = int(value)

    db.set_setting(key, value)
    await state.finish()

    label = _FIELDS_BY_KEY[key][1]
    await m.answer(f"✅ «{label}» به‌روزرسانی شد.", reply_markup=settings_menu_kb())


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
    await c.message.answer("📝 کدوم پیام رو می‌خواید ویرایش کنید؟", reply_markup=messages_menu_kb())


async def cb_msgkey(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    key = c.data.split("msgkey_", 1)[1]
    label = dict(messages.MESSAGE_KEYS).get(key, key)
    current_text, current_photo = messages.get(key)

    await state.update_data(message_key=key)

    info = (
        f"وضعیت فعلی «{label}»:\n"
        f"متن بنر: {current_text or '(تنظیم نشده)'}\n"
        f"عکس: {'دارد' if current_photo else '(تنظیم نشده)'}"
    )

    await c.message.answer(
        info + "\n\nمتن یا عکس جدید را بفرستید. برای پاک کردن /clear را بفرستید.",
        reply_markup=cancel_kb(),
    )
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

    await m.answer("لطفاً فقط متن یا عکس بفرستید.", reply_markup=cancel_kb())


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
    await c.message.answer(
        "⚠️ فایل دیتابیس (.db) رو بفرستید تا جایگزین دیتابیس فعلی بشه.",
        reply_markup=cancel_kb(),
    )
    await AdminStates.waiting_restore_file.set()


async def process_restore_file(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return

    if m.content_type != "document":
        return await m.answer("لطفاً فایل دیتابیس رو به‌صورت Document بفرستید.", reply_markup=cancel_kb())

    await state.finish()
    await m.answer("⏳ در حال بررسی و بازگردانی فایل...")

    tmp_path = f"/tmp/restore_upload_{m.document.file_unique_id}.db"
    await m.document.download(destination_file=tmp_path)

    if not backup.validate_sqlite_file(tmp_path):
        os.remove(tmp_path)
        return await m.answer("❌ این فایل دیتابیس معتبر نیست.", reply_markup=admin_back_kb())

    safety_path = backup.perform_restore(tmp_path)
    os.remove(tmp_path)

    await m.answer(
        f"✅ بازگردانی انجام شد.\nنسخه امن قبلی:\n{safety_path}\n\n"
        "ربات الان ری‌استارت میشه..."
    )

    logging.getLogger(__name__).warning("Database restored by admin %s, restarting process.", m.from_user.id)
    sys.exit(1)


async def cb_back(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    await c.message.answer("⚙️ پنل مدیریت Berserk VPN", reply_markup=admin_menu_kb())


def register(dp):
    dp.register_message_handler(cmd_admin, commands=[ADMIN_COMMAND])
    dp.register_callback_query_handler(cb_open_panel, lambda c: c.data == "open_admin_panel")

    dp.register_callback_query_handler(cb_users, lambda c: c.data == "adm_users")
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

    dp.register_callback_query_handler(cb_backup, lambda c: c.data == "adm_backup")
    dp.register_callback_query_handler(cb_restore_start, lambda c: c.data == "adm_restore")
    dp.register_message_handler(process_restore_file, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_restore_file)

    dp.register_callback_query_handler(cb_back, lambda c: c.data == "adm_back")
