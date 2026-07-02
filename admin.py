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
        InlineKeyboardButton("⚙️ تنظیمات", callback_data="adm_settings"),
        InlineKeyboardButton("📝 ویرایش پیام‌ها", callback_data="adm_messages"),
        InlineKeyboardButton("💾 دریافت بک‌آپ", callback_data="adm_backup"),
        InlineKeyboardButton("♻️ بارگذاری بک‌آپ", callback_data="adm_restore"),
    )
    kb.add(InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def user_detail_kb(user_id):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔄 بروزرسانی جزئیات", callback_data=f"adm_user_{user_id}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به کاربران", callback_data="adm_users"))
    kb.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm_back"))
    return kb


def _fmt_money(amount):
    return f"{int(amount or 0):,} تومان"


def _fmt_user_detail(user_id):
    user = db.get_user(user_id)
    if not user:
        return "کاربر پیدا نشد."

    status = "⛔ بن شده" if user["banned"] else "✅ فعال"
    refs = db.referral_count(user_id)
    owned = subs.user_subs(user_id, limit=20)
    purchases = db.list_user_purchases(user_id, limit=10)

    text = (
        f"👤 جزئیات کاربر\n\n"
        f"User ID: {user['id']}\n"
        f"Username: @{user['username'] or '-'}\n"
        f"وضعیت: {status}\n"
        f"موجودی: {_fmt_money(user['balance'])}\n"
        f"تعداد سرویس خریداری‌شده: {user['purchased']}\n"
        f"تعداد خرید ثبت‌شده: {len(purchases)}\n"
        f"زیرمجموعه‌ها: {refs}\n"
        f"عضویت: {user['joined_at']}\n"
        f"آخرین فعالیت: {user['last_active']}\n"
    )

    if purchases:
        text += "\n🧾 آخرین خریدها:\n"
        for p in purchases[:5]:
            text += f"• #{p['id']} | تعداد {p['quantity']} | مبلغ {_fmt_money(p['amount'])} | {p['created_at']}\n"

    if owned:
        text += "\n📦 ساب‌لینک‌های کاربر:\n"
        for s in owned[:10]:
            text += (
                f"• {s['account_name'] or '-'}\n"
                f"  تاریخ: {s['assigned_at'] or '-'} | مبلغ: {_fmt_money(s['price_paid'])}\n"
                f"  وضعیت: {s['status'] or 'delivered'} | لینک: {subs.short_link(s['link'])}\n"
            )
    else:
        text += "\n📦 این کاربر هنوز سرویسی دریافت نکرده."

    return text


async def cmd_admin(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("⚙️ پنل مدیریت Berserk VPN", reply_markup=admin_menu_kb())


async def cb_open_panel(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await c.message.answer("⚙️ پنل مدیریت Berserk VPN", reply_markup=admin_menu_kb())


async def cb_back(c: types.CallbackQuery):
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
    kb = InlineKeyboardMarkup(row_width=1)
    for r in rows:
        flag = "⛔" if r["banned"] else "✅"
        username = f"@{r['username']}" if r["username"] else "بدون یوزرنیم"
        lines.append(f"{flag} {r['id']} | {username} | خرید: {r['purchased']} | موجودی: {_fmt_money(r['balance'])}")
        kb.add(InlineKeyboardButton(f"👤 جزئیات {username} | {r['id']}", callback_data=f"adm_user_{r['id']}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    await c.message.answer("\n".join(lines), reply_markup=kb)


async def cb_user_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_", 1)[1]
    await c.message.answer(_fmt_user_detail(user_id), reply_markup=user_detail_kb(user_id))


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
        return await m.answer("لطفا آیدی یا یوزرنیم رو به‌صورت متن بفرستید.", reply_markup=cancel_kb())
    rows = db.search_users(m.text)
    await state.finish()
    if not rows:
        return await m.answer("چیزی پیدا نشد.", reply_markup=admin_back_kb())
    for r in rows[:10]:
        await m.answer(_fmt_user_detail(r["id"]), reply_markup=user_detail_kb(r["id"]))


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
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("لطفا فقط عدد بفرستید.", reply_markup=cancel_kb())
    db.add_balance(data["target_id"], amount, action="admin_adjustment", note=f"admin_id={m.from_user.id}")
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
        text = f"💳 درخواست شارژ #{r['id']}\nکاربر: @{uname or '-'} | ID: {r['user_id']}\nمبلغ: {r['amount']:,} تومان\nثبت: {r['created_at']}"
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("✅ تایید", callback_data=f"topup_confirm_{r['id']}"), InlineKeyboardButton("❌ رد", callback_data=f"topup_reject_{r['id']}"))
        kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
        await c.message.answer(text, reply_markup=kb)


async def cb_stats(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    total_users = db.count_users()
    active7 = db.active_users_count(7)
    sold = subs.sold_count()
    stock = subs.stock_count()
    rewards = db.total_referral_rewards()
    revenue = db.sum_approved_topups()
    wallets_total = db.sum_all_balances()
    pending_topups = db.count_pending_topups()
    lines = [
        "📊 آمار کلی Berserk VPN",
        "",
        "👥 کاربران",
        f"کل کاربران: {total_users}",
        f"فعال (۷ روز اخیر): {active7}",
        "",
        "💰 درآمد و مالی",
        f"مجموع شارژهای تایید‌شده: {revenue:,} تومان",
        f"مجموع موجودی فعلی همه کیف‌پول‌ها: {wallets_total:,} تومان",
        f"مجموع پاداش رفرال پرداختی: {rewards:,} تومان",
        f"شارژهای در انتظار بررسی: {pending_topups}",
        "",
        "📦 سرویس",
        f"فروخته‌شده: {sold}",
        f"موجودی فعلی: {stock}",
        "",
        "📈 روند روزانه (۷ روز اخیر):",
    ]
    for row in db.recent_daily_stats(7):
        lines.append(f"{row['day']}: کاربر جدید {row['new_users']} | فروش {row['sales']} | رفرال {row['referral_rewards']:,}")
    await c.message.answer("\n".join(lines), reply_markup=admin_back_kb())


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
    await c.message.answer("⚙️ تنظیمات — روی هر مورد بزنید تا مقدارش رو تغییر بدید:", reply_markup=settings_menu_kb())


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
        if not value.lstrip("-").isdigit():
            return await m.answer("لطفا فقط عدد بفرستید.", reply_markup=cancel_kb())
        value = int(value)
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
    await c.message.answer("📝 کدوم پیام رو می‌خواید ویرایش کنید؟", reply_markup=messages_menu_kb())


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
    await c.message.answer("⚠️ فایل دیتابیس (.db) رو بفرستید.", reply_markup=cancel_kb())
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
