import asyncio
import re
import logging
import os
import sys
import tempfile
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
from utils import cleanup_qr, format_dual_datetime, make_qr, parse_int

logger = logging.getLogger(__name__)


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
    waiting_plan_setting_value = State()
    waiting_category_form = State()
    waiting_category_setting = State()
    waiting_broadcast_content = State()
    waiting_broadcast_confirm = State()


def cancel_kb(back_callback="fsm_back", back_label="⬅️ برگشت"):
    """
    کیبورد مشترک فرم‌ها و ویزاردهای ادمین.
    - برگشت: تلاش می‌کند به مرحله/بخش قبلی همان جریان برگردد.
    - لغو: کل state جاری را می‌بندد.
    """
    kb = InlineKeyboardMarkup(row_width=1)
    if back_callback:
        kb.add(InlineKeyboardButton(back_label, callback_data=back_callback))
    kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    return kb


def _button_type_select_kb():
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
    return kb


def _plan_wizard_step_text(step):
    prompts = {
        "title": "➕ ساخت پلن جدید\n\nعنوان پلن را بفرستید.\nمثال: اقتصادی ۵۰ گیگ",
        "volume": "حجم پلن را بفرستید.\nمثال: 50GB یا 200MB",
        "duration": "مدت پلن را بفرستید.\nمثال: 30 روز",
        "price": "قیمت فروش را فقط عددی بفرستید.\nمثال: 180000",
        "description": "توضیح کوتاه پلن را بفرستید.\nبرای خالی بودن، - بفرستید.",
    }
    return prompts.get(step, prompts["title"])


def _plan_category_select_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    for category in db.list_plan_categories(active_only=False):
        mark = "✅" if int(category["is_active"] or 0) else "🚫"
        kb.add(InlineKeyboardButton(f"{mark} {category['emoji'] or '📦'} {category['title']}", callback_data=f"plan_wizard_category_{category['id']}"))
    kb.add(InlineKeyboardButton("➕ ساخت دسته جدید", callback_data="category_create"))
    kb.add(InlineKeyboardButton("⬅️ مدیریت پلن‌ها", callback_data="adm_plans"))
    kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    return kb


def _plan_purchase_mode_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🛒 خرید مستقیم یک سرویس", callback_data="plan_wizard_mode_direct"))
    kb.add(InlineKeyboardButton("🔢 انتخاب تعداد", callback_data="plan_wizard_mode_quantity"))
    kb.add(InlineKeyboardButton("📦 فقط خرید عمده", callback_data="plan_wizard_mode_wholesale"))
    kb.add(InlineKeyboardButton("⬅️ برگشت", callback_data="fsm_back"))
    kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    return kb


def _plan_delivery_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📦 تحویل از استخر لینک", callback_data="plan_wizard_delivery_pool"))
    kb.add(InlineKeyboardButton("🔌 ساخت خودکار توسط تأمین‌کننده", callback_data="plan_wizard_delivery_provider"))
    kb.add(InlineKeyboardButton("⬅️ برگشت", callback_data="fsm_back"))
    kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    return kb


def _plan_provider_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    providers = subs.list_provider_adapters(configured_only=False)
    for provider in providers:
        status = "✅" if provider.configured() else "⚠️"
        kb.add(InlineKeyboardButton(f"{status} {provider.label}", callback_data=f"plan_wizard_provider_{provider.key}"))
    kb.add(InlineKeyboardButton("⬅️ روش تحویل", callback_data="fsm_back"))
    kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    return kb


def _plan_start_mode_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⏳ شروع از اولین اتصال", callback_data="plan_wizard_start_on_hold"))
    kb.add(InlineKeyboardButton("▶️ شروع از زمان ساخت", callback_data="plan_wizard_start_active"))
    kb.add(InlineKeyboardButton("⬅️ برگشت", callback_data="fsm_back"))
    kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    return kb


def _plan_device_limit_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("1️⃣ یک دستگاه", callback_data="plan_wizard_devices_1"),
        InlineKeyboardButton("2️⃣ دو دستگاه", callback_data="plan_wizard_devices_2"),
        InlineKeyboardButton("3️⃣ سه دستگاه", callback_data="plan_wizard_devices_3"),
    )
    kb.add(InlineKeyboardButton("♾ بدون محدودیت", callback_data="plan_wizard_devices_unlimited"))
    kb.add(InlineKeyboardButton("⬅️ برگشت", callback_data="fsm_back"))
    kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    return kb


def admin_back_kb():
    return menus.admin_back_inline()


def admin_menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    for item in db.list_admin_menu_items(active_only=True):
        kb.insert(InlineKeyboardButton(item["title"] or item["default_title"], callback_data=item["callback_data"]))
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
        InlineKeyboardButton("🗂 دسته‌ها", callback_data="adm_categories"),
        InlineKeyboardButton("🏷 پلن‌ها", callback_data="adm_plans"),
        InlineKeyboardButton("🔗 استخر لینک‌ها", callback_data="adm_links"),
        InlineKeyboardButton("🔌 تأمین‌کننده‌ها", callback_data="adm_providers"),
        InlineKeyboardButton("🧪 اکانت‌های تست", callback_data="adm_trials"),
    )
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def admin_finance_section_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("💳 شارژهای در انتظار", callback_data="adm_topups"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    return kb


def admin_personalize_section_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🎛 مدیریت دکمه‌ها", callback_data="adm_buttons"))
    kb.add(InlineKeyboardButton("🧭 چیدمان پنل مدیریت", callback_data="adm_menu_layout"))
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


ADMIN_CLEANUP_KINDS = ("menu", "temp", "preview", "list")


async def _admin_cleanup_tracked(chat_id, user_id, keep_message_id=None, kinds=ADMIN_CLEANUP_KINDS):
    """
    پاک‌سازی هوشمند پنل ادمین:
    فقط پیام‌های موقت/لیستی/منویی که خود ربات ثبت کرده پاک می‌شوند.
    پیام‌های مهم مثل رسید، فایل بک‌آپ یا لینک تحویل‌شده ثبت‌شده با kind مهم پاک نمی‌شوند.
    """
    try:
        rows = db.list_tracked_bot_messages(chat_id, user_id, limit=60, kinds=kinds)
    except TypeError:
        rows = db.list_tracked_bot_messages(chat_id, user_id, limit=60)
    for row in rows:
        message_id = int(row["message_id"])
        if keep_message_id is not None and message_id == int(keep_message_id):
            continue
        try:
            bot = Bot.get_current()
            await bot.delete_message(int(row["chat_id"]), message_id)
        except Exception:
            try:
                bot = Bot.get_current()
                await bot.edit_message_reply_markup(int(row["chat_id"]), message_id, reply_markup=None)
            except Exception as exc:
                logger.debug("could not remove inline keyboard for tracked message %s: %s", message_id, exc)
        finally:
            try:
                db.clear_tracked_bot_message(row["chat_id"], message_id)
            except Exception as exc:
                logger.debug("could not clear tracked message %s: %s", message_id, exc)


async def _track_admin_sent(user_id, sent, context="admin", kind="menu"):
    if sent is None:
        return
    if not isinstance(sent, (list, tuple)):
        sent = [sent]
    for msg in sent:
        try:
            db.track_bot_message(msg.chat.id, user_id, msg.message_id, context, kind=kind)
        except TypeError:
            db.track_bot_message(msg.chat.id, user_id, msg.message_id, context)
        except Exception as exc:
            logger.debug("could not track admin message: %s", exc)


async def _send_long(message, text, reply_markup=None, owner_user_id=None, context="admin_long", kind="list", cleanup=False):
    if cleanup:
        await _admin_cleanup_tracked(message.chat.id, owner_user_id or message.chat.id)
    chunks = []
    while len(text) > 3900:
        split_at = text.rfind("\n", 0, 3900)
        if split_at == -1:
            split_at = 3900
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    chunks.append(text)

    sent_messages = []
    for index, chunk in enumerate(chunks):
        sent = await message.answer(chunk, reply_markup=reply_markup if index == len(chunks) - 1 else None)
        sent_messages.append(sent)
    await _track_admin_sent(owner_user_id or message.chat.id, sent_messages, context=context, kind=kind)
    return sent_messages


async def _safe_remove_inline_keyboard(message):
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.debug("could not remove inline keyboard: %s", exc)


async def _safe_delete_message(message):
    try:
        await message.delete()
        return True
    except Exception:
        return False


async def _replace_callback_message(c: types.CallbackQuery, text: str, reply_markup=None, parse_mode=None, context="admin_view", kind="menu", cleanup=True):
    """
    برای جلوگیری از شلوغ شدن پنل:
    - قبل از باز شدن پنجره جدید، پیام‌های موقت/لیستی قبلی همان ادمین جمع می‌شوند.
    - اگر پیام فعلی قابل ویرایش باشد، همان پیام edit می‌شود.
    - اگر قابل edit نباشد، پیام قبلی حذف/کیبوردش پاک می‌شود و پیام جدید ارسال می‌شود.
    """
    if cleanup:
        await _admin_cleanup_tracked(c.message.chat.id, c.from_user.id, keep_message_id=c.message.message_id)
    try:
        if getattr(c.message, "content_type", None) == types.ContentType.TEXT:
            await c.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            try:
                db.track_bot_message(c.message.chat.id, c.from_user.id, c.message.message_id, context, kind=kind)
            except TypeError:
                db.track_bot_message(c.message.chat.id, c.from_user.id, c.message.message_id, context)
            except Exception as track_exc:
                logger.debug("could not track edited admin message: %s", track_exc)
            return
    except Exception as exc:
        if "message is not modified" in str(exc).lower():
            return

    deleted = await _safe_delete_message(c.message)

    if not deleted:
        await _safe_remove_inline_keyboard(c.message)

    sent = await c.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    await _track_admin_sent(c.from_user.id, sent, context=context, kind=kind)


async def cb_fsm_back(c: types.CallbackQuery, state: FSMContext):
    """
    برگشت هوشمند داخل فرم‌ها/ویزاردهای ادمین.
    برخلاف لغو، تلاش می‌کند کاربر را به مرحله یا منوی قبلی همان بخش برگرداند.
    """
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    current_state = await state.get_state()
    data = await state.get_data()

    # ویزارد ساخت پلن: برگشت مرحله‌ای
    if current_state and current_state.endswith("waiting_plan_form") and data.get("plan_action") == "create_wizard":
        step = data.get("plan_step") or "category"
        plan_data = data.get("plan_data") or {}
        if step == "category":
            await state.finish()
            return await _replace_callback_message(c, "🏷 مدیریت پلن‌ها", reply_markup=plans_menu_kb())
        if step == "title":
            await state.update_data(plan_step="category")
            return await _replace_callback_message(c, "دسته پلن را انتخاب کنید:", reply_markup=_plan_category_select_kb(), cleanup=False)
        back_map = {
            "volume": "title",
            "duration": "volume",
            "price": "duration",
            "purchase_mode": "price",
            "delivery": "purchase_mode",
            "provider": "delivery",
            "panel_devices": "provider",
            "panel_start": "panel_devices",
            "description": "panel_start" if plan_data.get("provider_key") not in (None, "pool") else "delivery",
            "confirm": "description",
        }
        prev_step = back_map.get(step, "category")
        await state.update_data(plan_step=prev_step)
        if prev_step == "category":
            return await _replace_callback_message(c, "دسته پلن را انتخاب کنید:", reply_markup=_plan_category_select_kb(), cleanup=False)
        if prev_step == "purchase_mode":
            return await _replace_callback_message(c, "نحوه خرید را انتخاب کنید:", reply_markup=_plan_purchase_mode_kb(), cleanup=False)
        if prev_step == "delivery":
            return await _replace_callback_message(c, "روش تحویل را انتخاب کنید:", reply_markup=_plan_delivery_kb(), cleanup=False)
        if prev_step == "provider":
            return await _replace_callback_message(c, "تأمین‌کننده را انتخاب کنید:", reply_markup=_plan_provider_kb(), cleanup=False)
        if prev_step == "panel_devices":
            return await _replace_callback_message(c, "تعداد دستگاه را انتخاب کنید:", reply_markup=_plan_device_limit_kb(), cleanup=False)
        if prev_step == "panel_start":
            return await _replace_callback_message(c, "زمان شروع اعتبار را انتخاب کنید:", reply_markup=_plan_start_mode_kb(), cleanup=False)
        return await _replace_callback_message(c, _plan_wizard_step_text(prev_step), reply_markup=cancel_kb(), cleanup=False)

    # ویرایش فرم کامل پلن
    if current_state and current_state.endswith("waiting_plan_form") and data.get("plan_action") == "edit":
        plan_id = data.get("plan_id")
        await state.finish()
        plan = db.get_plan(plan_id) if plan_id else None
        if plan:
            return await _replace_callback_message(c, _fmt_plan(plan), reply_markup=plan_detail_kb(plan_id))
        return await _replace_callback_message(c, "🏷 مدیریت پلن‌ها", reply_markup=plans_menu_kb())

    if current_state and current_state.endswith("waiting_plan_setting_value"):
        plan_id = data.get("plan_setting_plan_id")
        await state.finish()
        if plan_id and db.get_plan(plan_id):
            return await _replace_callback_message(c, _fmt_plan(db.get_plan(plan_id)), reply_markup=plan_settings_kb(plan_id))
        return await _replace_callback_message(c, "🏷 مدیریت پلن‌ها", reply_markup=plans_menu_kb())

    # یادداشت و پیام مستقیم کاربر
    if current_state and current_state.endswith("waiting_user_note"):
        user_id = data.get("note_user_id")
        await state.finish()
        if user_id:
            return await _replace_callback_message(c, _fmt_user_summary(user_id), reply_markup=user_detail_kb(user_id))

    if current_state and current_state.endswith("waiting_direct_message"):
        user_id = data.get("direct_user_id")
        await state.finish()
        if user_id:
            return await _replace_callback_message(c, _fmt_user_summary(user_id), reply_markup=user_detail_kb(user_id))

    # تغییر موجودی: اگر در مرحله مبلغ هستیم، به مرحله ورود کاربر برگردد.
    if current_state and current_state.endswith("waiting_balance_amount"):
        await state.set_state(AdminStates.waiting_balance_id.state)
        data.pop("target_id", None)
        await state.set_data(data)
        return await _replace_callback_message(c, "آیدی کاربر رو بفرستید:", reply_markup=cancel_kb(), cleanup=False)

    if current_state and current_state.endswith(("waiting_search", "waiting_balance_id", "waiting_ban_id", "waiting_unban_id")):
        await state.finish()
        return await _replace_callback_message(c, "👥 مدیریت کاربران", reply_markup=admin_users_section_kb())

    # مدیریت لینک‌ها و افزودن لینک به پلن
    if current_state and current_state.endswith("waiting_add_sub"):
        plan_id = data.get("add_sub_plan_id")
        await state.finish()
        if plan_id and db.get_plan(plan_id):
            return await _replace_callback_message(c, _fmt_plan(db.get_plan(plan_id)), reply_markup=plan_detail_kb(plan_id))
        return await _replace_callback_message(c, "🔗 استخر لینک‌ها", reply_markup=link_manager_kb())

    if current_state and current_state.endswith(("waiting_link_search", "waiting_link_delete_id")):
        await state.finish()
        return await _replace_callback_message(c, "🔗 استخر لینک‌ها", reply_markup=link_manager_kb())

    # تنظیمات عمومی
    if current_state and current_state.endswith("waiting_setting_value"):
        await state.finish()
        return await _replace_callback_message(c, "⚙️ تنظیمات کل ربات", reply_markup=settings_menu_kb())

    # مدیریت پیام‌ها
    if current_state and current_state.endswith("waiting_message_edit"):
        key = data.get("message_key")
        await state.finish()
        if key and messages.is_valid_key(key):
            label = dict(messages.MESSAGE_KEYS).get(key, key)
            return await _replace_callback_message(c, f"📝 مدیریت متن: {label}", reply_markup=message_action_kb(key))
        return await _replace_callback_message(c, "📝 مدیریت پیام‌ها", reply_markup=messages_menu_kb())

    # دکمه‌های سیستمی
    if current_state and current_state.endswith(("waiting_system_button_title", "waiting_system_button_order", "waiting_system_button_location")):
        key = data.get("sys_button_key")
        await state.finish()
        row = db.get_system_button(key) if key else None
        if row:
            return await _replace_callback_message(c, _fmt_system_button(row), reply_markup=system_button_detail_kb(key))
        return await _replace_callback_message(c, "🧩 دکمه‌های فعلی ربات", reply_markup=system_buttons_list_kb())

    # ویزارد ساخت دکمه اختصاصی
    if current_state and current_state.endswith("waiting_custom_button_payload"):
        await state.set_state(AdminStates.waiting_custom_button_title.state)
        await state.update_data(button_title=None)
        return await _replace_callback_message(c, "عنوان دکمه را بفرستید.\nمثال: 📚 آموزش آیفون", reply_markup=cancel_kb(), cleanup=False)

    if current_state and current_state.endswith("waiting_custom_button_title"):
        await state.finish()
        return await _replace_callback_message(c, "➕ ساخت دکمه جدید\n\nاول نوع دکمه را انتخاب کنید:", reply_markup=_button_type_select_kb())

    if current_state and current_state.endswith("waiting_custom_button_form"):
        button_id = data.get("button_id")
        await state.finish()
        row = db.get_custom_button(button_id) if button_id else None
        if row:
            return await _replace_callback_message(c, _fmt_custom_button(row), reply_markup=custom_button_detail_kb(button_id))
        return await _replace_callback_message(c, "🎛 مدیریت دکمه‌ها", reply_markup=custom_buttons_menu_kb())

    if current_state and current_state.endswith(("waiting_button_order", "waiting_button_location")):
        button_id = data.get("button_id")
        await state.finish()
        row = db.get_custom_button(button_id) if button_id else None
        if row:
            return await _replace_callback_message(c, _fmt_custom_button(row), reply_markup=custom_button_detail_kb(button_id))
        return await _replace_callback_message(c, "🎛 مدیریت دکمه‌ها", reply_markup=custom_buttons_menu_kb())

    # پیام همگانی و ری‌استور
    if current_state and current_state.endswith("waiting_broadcast_content"):
        await state.finish()
        return await _replace_callback_message(c, "📣 پیام همگانی", reply_markup=broadcast_scope_menu_kb())

    if current_state and current_state.endswith("waiting_restore_file"):
        await state.finish()
        return await _replace_callback_message(c, "💾 بک‌آپ و امنیت", reply_markup=backup_menu_kb())

    # fallback امن
    await state.finish()
    return await _replace_callback_message(c, "🏠 پنل مدیریت", reply_markup=admin_menu_kb())


def user_detail_kb(user_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📦 سرویس‌ها و خریدها", callback_data=f"adm_user_history_{user_id}"),
        InlineKeyboardButton("💰 مالی و کیف پول", callback_data=f"adm_user_finance_{user_id}"),
        InlineKeyboardButton("🎫 تیکت‌ها", callback_data=f"adm_user_tickets_{user_id}"),
        InlineKeyboardButton("👥 رفرال", callback_data=f"adm_user_referral_{user_id}"),
    )
    kb.add(
        InlineKeyboardButton("👁 اطلاعات پروفایل", callback_data=f"adm_user_profile_{user_id}"),
        InlineKeyboardButton("💬 ارسال پیام", callback_data=f"adm_msg_user_{user_id}"),
    )
    kb.add(
        InlineKeyboardButton("📝 یادداشت ادمین", callback_data=f"adm_user_note_{user_id}"),
        InlineKeyboardButton("🧪 وضعیت تست", callback_data=f"adm_user_test_{user_id}"),
    )
    kb.add(InlineKeyboardButton("💳 افزایش / کاهش موجودی", callback_data="adm_addbal"))
    kb.add(InlineKeyboardButton("🔄 بروزرسانی خلاصه", callback_data=f"adm_user_{user_id}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به بخش کاربران", callback_data="adm_section_users"))
    kb.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm_back"))
    return kb


def user_history_kb(user_id, purchases, owned):
    kb = InlineKeyboardMarkup(row_width=1)
    for p in purchases[:12]:
        kb.add(InlineKeyboardButton(f"🧾 خرید #{p['id']} | {p['quantity']} عدد | {_fmt_money(p['amount'])}", callback_data=f"adm_user_purchase_{p['id']}_{user_id}"))
    orphan_items = [s for s in owned if not s["purchase_id"]]
    for s in orphan_items[:8]:
        label = s["account_name"] or f"Sub #{s['id']}"
        kb.add(InlineKeyboardButton(f"🔗 {label} | جزئیات سرویس", callback_data=f"adm_user_sub_detail_{s['id']}_{user_id}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به جزئیات کاربر", callback_data=f"adm_user_{user_id}"))
    return kb


def purchase_detail_kb(user_id, purchase_id, services):
    kb = InlineKeyboardMarkup(row_width=1)
    for index, s in enumerate(services, start=1):
        label = s["account_name"] or f"Sub #{s['id']}"
        kb.add(InlineKeyboardButton(f"{index}️⃣ {label} | جزئیات سرویس", callback_data=f"adm_user_sub_detail_{s['id']}_{user_id}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به سرویس‌ها و خریدها", callback_data=f"adm_user_history_{user_id}"))
    kb.add(InlineKeyboardButton("👤 بازگشت به جزئیات کاربر", callback_data=f"adm_user_{user_id}"))
    return kb


def service_detail_kb(user_id, sub_id, purchase_id=None):
    kb = InlineKeyboardMarkup(row_width=2)
    item = subs.get_sub_detail(sub_id)
    source_type = (item["source_type"] or "pool") if item and "source_type" in item.keys() else "pool"
    kb.add(
        InlineKeyboardButton("🔗 ارسال لینک به کاربر", callback_data=f"adm_resend_link_{sub_id}_{user_id}"),
        InlineKeyboardButton("🔳 ارسال QR به کاربر", callback_data=f"adm_resend_qr_{sub_id}_{user_id}"),
    )
    if source_type != "pool":
        kb.add(
            InlineKeyboardButton("📊 مصرف تأمین‌کننده", callback_data=f"adm_panel_usage_{sub_id}_{user_id}"),
            InlineKeyboardButton("♻️ صفرکردن مصرف", callback_data=f"adm_panel_reset_ask_{sub_id}_{user_id}"),
            InlineKeyboardButton("🔄 تعویض لینک", callback_data=f"adm_panel_revoke_ask_{sub_id}_{user_id}"),
            InlineKeyboardButton("🗑 حذف از تأمین‌کننده", callback_data=f"adm_panel_delete_ask_{sub_id}_{user_id}"),
        )
    else:
        kb.add(InlineKeyboardButton("↩️ بازگردانی به استخر", callback_data=f"adm_link_repool_ask_{sub_id}"))
    if purchase_id:
        kb.add(InlineKeyboardButton("⬅️ بازگشت به جزئیات خرید", callback_data=f"adm_user_purchase_{purchase_id}_{user_id}"))
    kb.add(InlineKeyboardButton("📦 بازگشت به سرویس‌ها و خریدها", callback_data=f"adm_user_history_{user_id}"))
    kb.add(InlineKeyboardButton("👤 بازگشت به جزئیات کاربر", callback_data=f"adm_user_{user_id}"))
    return kb


def user_services_kb(user_id):
    # سازگاری با نسخه‌های قبلی: عملیات سریع دیگر به صورت لیست بزرگ نمایش داده نمی‌شود.
    owned = subs.user_subs(user_id, limit=8)
    kb = InlineKeyboardMarkup(row_width=1)
    for index, row in enumerate(list(reversed(owned)), start=1):
        label = row["account_name"] or f"Sub #{row['id']}"
        kb.add(InlineKeyboardButton(f"{index}️⃣ {label} | جزئیات", callback_data=f"adm_user_sub_detail_{row['id']}_{user_id}"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به جزئیات", callback_data=f"adm_user_{user_id}"))
    return kb


def _plan_title(plan_id):
    try:
        plan = db.get_plan(plan_id)
        return plan["title"] if plan else "پلن نامشخص"
    except Exception:
        return "پلن نامشخص"


def _owned_services(user_id, limit=None):
    rows = list(subs.user_subs(user_id, limit=limit))
    return sorted(rows, key=lambda r: ((r["assigned_at"] or ""), int(r["id"] or 0)))


def _services_for_purchase(user_id, purchase_id):
    return [s for s in _owned_services(user_id) if str(s["purchase_id"] or "") == str(purchase_id)]


def _fmt_user_summary(user_id):
    user = db.get_user(user_id)
    if not user:
        return "کاربر پیدا نشد."

    status = "⛔ بن شده" if user["banned"] else "✅ فعال"
    username_text = _display_username(user)
    test_text = "🧪 کاربر تست" if "is_test" in user.keys() and int(user["is_test"] or 0) else "عادی"
    admin_note = user["admin_note"] if "admin_note" in user.keys() and user["admin_note"] else ""
    purchase_count = db.purchase_count_by_user(user_id)
    delivered_count = db.delivered_sub_count_by_user(user_id)
    approved_topup_count = db.user_approved_topup_count(user_id)
    ticket_counts = db.user_ticket_counts(user_id)

    return (
        "👤 خلاصه کاربر\n\n"
        f"User ID: {user['id']}\n"
        f"Username: @{user['username'] or '-'}\n"
        f"نمایش: {username_text}\n"
        f"نوع کاربر: {test_text}\n"
        f"وضعیت: {status}\n"
        f"موجودی کیف پول: {_fmt_money(user['balance'])}\n\n"
        f"خریدهای ثبت‌شده: {purchase_count}\n"
        f"سرویس‌های تحویل‌شده: {delivered_count}\n"
        f"شارژهای موفق: {approved_topup_count}\n"
        f"تیکت‌ها: {ticket_counts['total']} | باز: {ticket_counts['open']}\n"
        f"معرف: {user['ref'] or '-'}\n\n"
        f"عضویت: {_dual(user['joined_at'])}\n"
        f"آخرین فعالیت: {_dual(user['last_active'])}\n"
        f"یادداشت ادمین: {'دارد' if admin_note else 'ندارد'}\n\n"
        "برای جزئیات بیشتر از دکمه‌های پایین استفاده کنید."
    )


def _fmt_user_history(user_id):
    user = db.get_user(user_id)
    if not user:
        return "کاربر پیدا نشد.", [], []
    purchases = list(reversed(db.list_user_purchases(user_id, limit=20)))
    owned = _owned_services(user_id)
    services_by_purchase = {}
    for s in owned:
        services_by_purchase.setdefault(str(s["purchase_id"] or ""), []).append(s)

    lines = ["📦 سرویس‌ها و خریدهای کاربر\n"]
    lines.append(f"کاربر: {_display_username(user)} | ID: {user_id}")
    lines.append(f"تعداد خریدها: {len(purchases)} | سرویس‌ها: {len(owned)}\n")

    if purchases:
        for index, p in enumerate(purchases, start=1):
            items = services_by_purchase.get(str(p["id"]), [])
            lines.append(f"{index}️⃣ خرید #{p['id']} | {_dual(p['created_at'])}")
            lines.append(f"تعداد: {p['quantity']} | مبلغ: {_fmt_money(p['amount'])} | واحد: {_fmt_money(p['unit_price'])}")
            if p["plan_id"]:
                lines.append(f"پلن: {_plan_title(p['plan_id'])}")
            if items:
                labels = ", ".join([(s["account_name"] or f"Sub #{s['id']}") for s in items[:4]])
                more = f" و {len(items) - 4} مورد دیگر" if len(items) > 4 else ""
                lines.append(f"سرویس‌ها: {labels}{more}")
            else:
                lines.append("سرویس ثبت‌شده برای این خرید پیدا نشد.")
            lines.append("")
    else:
        lines.append("خریدی برای این کاربر ثبت نشده.")

    orphan_items = [s for s in owned if not s["purchase_id"]]
    if orphan_items:
        lines.append("🔗 سرویس‌های بدون شناسه خرید:")
        for i, s in enumerate(orphan_items[:8], start=1):
            label = s["account_name"] or f"Sub #{s['id']}"
            lines.append(f"{i}. {label} | {_dual(s['assigned_at'])} | {_fmt_money(s['price_paid'])}")

    return "\n".join(lines).strip(), purchases, owned


def _fmt_purchase_detail(user_id, purchase_id):
    p = db.get_purchase(purchase_id)
    if not p or str(p["user_id"]) != str(user_id):
        return "این خرید برای این کاربر پیدا نشد.", []
    services = _services_for_purchase(user_id, purchase_id)
    lines = [f"🧾 جزئیات خرید #{purchase_id}\n"]
    lines.append(f"کاربر: {user_id}")
    lines.append(f"تاریخ: {_dual(p['created_at'])}")
    lines.append(f"تعداد: {p['quantity']}")
    lines.append(f"قیمت واحد: {_fmt_money(p['unit_price'])}")
    lines.append(f"مبلغ کل: {_fmt_money(p['amount'])}")
    lines.append(f"وضعیت: {p['status']}")
    if p["plan_id"]:
        lines.append(f"پلن: {_plan_title(p['plan_id'])}")
    if p["note"]:
        lines.append(f"یادداشت: {p['note']}")

    lines.append("\n📦 سرویس‌های این خرید")
    if services:
        for i, s in enumerate(services, start=1):
            label = s["account_name"] or f"Sub #{s['id']}"
            lines.append(
                f"{i}️⃣ {label} | "
                f"وضعیت: {s['status'] or 'delivered'} | تحویل: {_dual(s['assigned_at'])} | مبلغ: {_fmt_money(s['price_paid'])}"
            )
    else:
        lines.append("سرویسی برای این خرید پیدا نشد.")
    return "\n".join(lines), services


def _fmt_service_detail(user_id, sub_id):
    s = subs.get_sub_detail(sub_id)
    if not s or str(s["owner"] or "") != str(user_id):
        return "این سرویس برای این کاربر پیدا نشد.", None
    plan = db.get_plan(s["plan_id"]) if s["plan_id"] else None
    lines = [f"📦 جزئیات سرویس #{s['id']}\n"]
    lines.append(f"شناسه سرویس: {s['account_name'] or '-'}")
    lines.append(f"کاربر: {user_id}")
    lines.append(f"پلن: {plan['title'] if plan else 'نامشخص'}")
    lines.append(f"خرید: #{s['purchase_id'] or '-'}")
    lines.append(f"تاریخ خرید/تحویل: {_dual(s['assigned_at'])}")
    lines.append(f"مبلغ: {_fmt_money(s['price_paid'])}")
    lines.append(f"وضعیت: {s['status'] or 'delivered'}")
    source_type = s["source_type"] if "source_type" in s.keys() else "pool"
    provider_name = subs.provider_label(source_type) if source_type != "pool" else "استخر لینک"
    lines.append(f"منبع سرویس: {provider_name}")
    if source_type != "pool":
        lines.append(f"کاربر تأمین‌کننده: {s['panel_username'] or '-'}")
        lines.append(f"وضعیت پنل: {s['panel_status'] or '-'}")
        lines.append(f"حجم پنل: {_fmt_bytes(s['panel_data_limit'])}")
        lines.append(f"مصرف ثبت‌شده: {_fmt_bytes(s['panel_used_traffic'])}")
        lines.append(f"نوع: {'🧪 تست' if int(s['is_trial'] or 0) else 'فروش خودکار'}")
    lines.append("\n🔗 لینک کامل:")
    lines.append(s["link"] or "-")
    return "\n".join(lines), s


def _fmt_user_finance(user_id):
    user = db.get_user(user_id)
    if not user:
        return "کاربر پیدا نشد."
    ledger = db.list_user_ledger(user_id, limit=20)
    topups = db.list_user_topups(user_id, limit=50)
    purchases = db.list_user_purchases(user_id, limit=50)
    approved_total = sum(int(t["amount"] or 0) for t in topups if (t["status"] or "") == "approved")
    pending_count = sum(1 for t in topups if (t["status"] or "") in ("awaiting_receipt", "pending_review"))
    purchase_total = sum(int(p["amount"] or 0) for p in purchases if (p["status"] or "") == "completed")

    lines = ["💰 مالی و کیف پول\n"]
    lines.append(f"کاربر: {_display_username(user)} | ID: {user_id}")
    lines.append(f"موجودی فعلی: {_fmt_money(user['balance'])}")
    lines.append(f"کل شارژهای تأییدشده: {_fmt_money(approved_total)}")
    lines.append(f"کل خریدهای ثبت‌شده: {_fmt_money(purchase_total)}")
    lines.append(f"شارژهای در انتظار/بررسی: {pending_count}\n")
    lines.append("📒 خط زمان مالی اخیر")
    if ledger:
        for i, entry in enumerate(ledger, start=1):
            amount = int(entry["amount"] or 0)
            sign = "+" if amount > 0 else ""
            action_label = {
                "purchase": "خرید سرویس",
                "balance_adjustment": "تغییر دستی موجودی",
                "topup": "شارژ کیف پول",
                "referral_reward": "پاداش رفرال",
                "purchase_refund": "بازگشت مبلغ خرید پنلی",
                "admin_return_sub_to_pool": "بازگردانی سرویس به استخر",
            }.get(entry["action"], entry["action"])
            lines.append(f"{i}️⃣ {action_label} | {sign}{_fmt_money(amount)}")
            lines.append(f"قبل: {_fmt_money(entry['balance_before'])} | بعد: {_fmt_money(entry['balance_after'])} | {_dual(entry['created_at'])}")
            if entry["note"]:
                lines.append(f"یادداشت: {_short(entry['note'], 80)}")
    else:
        lines.append("تراکنشی ثبت نشده.")
    return "\n".join(lines)


def _fmt_user_referral(user_id):
    user = db.get_user(user_id)
    if not user:
        return "کاربر پیدا نشد."
    referred = db.referred_users(user_id, limit=20)
    lines = ["👥 رفرال کاربر\n"]
    lines.append(f"کاربر: {_display_username(user)} | ID: {user_id}")
    lines.append(f"معرف این کاربر: {user['ref'] or '-'}")
    real_referrals = db.referral_count(user_id)
    all_referrals = db.referral_count(user_id, include_test=True)
    test_referrals = max(0, all_referrals - real_referrals)
    lines.append(f"تعداد زیرمجموعه‌های واقعی: {real_referrals} | تست: {test_referrals}")
    lines.append(f"زیرمجموعه‌های واقعی پاداش‌داده‌شده: {db.rewarded_referral_count(user_id)}")
    lines.append(f"مجموع پاداش دریافتی: {_fmt_money(db.referral_reward_total(user_id))}\n")
    if referred:
        lines.append("آخرین زیرمجموعه‌ها:")
        for i, row in enumerate(referred, start=1):
            mark = "✅ خرید کرده" if row["rewarded"] else "⏳ بدون خرید"
            test_mark = "🧪 " if int(row["is_test"] or 0) else ""
            lines.append(f"{i}. {test_mark}{row['id']} | {_display_username(row)} | {mark} | خرید: {row['purchased']} | عضویت: {_dual(row['joined_at'])}")
    else:
        lines.append("زیرمجموعه‌ای ثبت نشده.")
    return "\n".join(lines)


def _fmt_user_tickets(user_id):
    user = db.get_user(user_id)
    if not user:
        return "کاربر پیدا نشد."
    counts = db.user_ticket_counts(user_id)
    tickets = db.list_user_tickets(user_id, limit=15)
    lines = ["🎫 تیکت‌های کاربر\n"]
    lines.append(f"کاربر: {_display_username(user)} | ID: {user_id}")
    lines.append(f"کل: {counts['total']} | باز: {counts['open']} | بسته: {counts['closed']}\n")
    if tickets:
        for i, t in enumerate(tickets, start=1):
            lines.append(f"{i}. تیکت #{t['id']} | {t['status']} | {_dual(t['created_at'])}")
    else:
        lines.append("تیکتی ثبت نشده.")
    return "\n".join(lines)


# سازگاری با اسم قبلی؛ از این به بعد جزئیات اصلی خلاصه است.
def _fmt_user_detail(user_id):
    return _fmt_user_summary(user_id)




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
    rows = db.list_users_with_stats(limit=15)

    if not rows:
        return await _replace_callback_message(c, "هیچ کاربری ثبت نشده.", reply_markup=admin_back_kb())

    lines = ["👥 کاربران بر اساس زمان عضویت؛ از قدیمی‌ترین تا جدیدترین:\n"]
    kb = InlineKeyboardMarkup(row_width=1)

    for index, r in enumerate(rows, start=1):
        flag = "⛔" if r["banned"] else "✅"
        username = _display_username(r)
        delivered = int(r["delivered_count"] or 0)
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
    await _replace_callback_message(
        c,
        _fmt_user_summary(user_id),
        reply_markup=user_detail_kb(user_id),
        context="admin_user_summary",
        kind="menu",
    )


async def cb_user_history(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_history_", 1)[1]
    text, purchases, owned = _fmt_user_history(user_id)
    await _replace_callback_message(c, text, reply_markup=user_history_kb(user_id, purchases, owned), context="admin_user_history", kind="list")


async def cb_user_purchase_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    payload = c.data.replace("adm_user_purchase_", "", 1)
    purchase_id, user_id = payload.split("_", 1)
    text, services = _fmt_purchase_detail(user_id, purchase_id)
    await _replace_callback_message(c, text, reply_markup=purchase_detail_kb(user_id, purchase_id, services), context="admin_user_purchase", kind="list")


async def cb_user_service_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    payload = c.data.replace("adm_user_sub_detail_", "", 1)
    sub_id, user_id = payload.split("_", 1)
    text, item = _fmt_service_detail(user_id, sub_id)
    if not item:
        return await _replace_callback_message(c, text, reply_markup=user_detail_kb(user_id), context="admin_service_detail", kind="list")
    purchase_id = item["purchase_id"] if item else None
    await _replace_callback_message(c, text, reply_markup=service_detail_kb(user_id, sub_id, purchase_id), context="admin_service_detail", kind="list")


async def cb_user_finance(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_finance_", 1)[1]
    await _replace_callback_message(c, _fmt_user_finance(user_id), reply_markup=user_detail_kb(user_id), context="admin_user_finance", kind="list")


async def cb_user_referral(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_referral_", 1)[1]
    await _replace_callback_message(c, _fmt_user_referral(user_id), reply_markup=user_detail_kb(user_id), context="admin_user_referral", kind="list")


async def cb_user_tickets(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_user_tickets_", 1)[1]
    await _replace_callback_message(c, _fmt_user_tickets(user_id), reply_markup=user_detail_kb(user_id), context="admin_user_tickets", kind="list")


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
    await _admin_cleanup_tracked(m.chat.id, m.from_user.id)
    sent = await m.answer("✅ یادداشت ادمین ذخیره شد.\n\n" + _fmt_user_summary(user_id), reply_markup=user_detail_kb(user_id))
    await _track_admin_sent(m.from_user.id, sent, context="admin_user_summary", kind="menu")


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
    test_state = "تست" if int(user["is_test"] or 0) else "عادی"
    await _replace_callback_message(
        c,
        f"✅ نوع کاربر به «{test_state}» تغییر کرد.\n"
        "خریدها، شارژها و تراکنش‌های همین کاربر نیز با وضعیت جدید در گزارش‌ها طبقه‌بندی شدند.\n"
        "سرویس‌های تحویل‌شده خودکار به استخر برنمی‌گردند و پاداش رفرال قبلاً پرداخت‌شده نیز خودکار معکوس نمی‌شود.\n\n"
        + _fmt_user_summary(user_id),
        reply_markup=user_detail_kb(user_id),
    )


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
        await _admin_cleanup_tracked(m.chat.id, m.from_user.id)
        sent = await m.answer("✅ پیام برای کاربر ارسال شد.", reply_markup=user_detail_kb(user_id))
        await _track_admin_sent(m.from_user.id, sent, context="admin_user_summary", kind="menu")
    except Exception as exc:
        sent = await m.answer(f"❌ ارسال پیام ناموفق بود: {exc}", reply_markup=user_detail_kb(user_id))
        await _track_admin_sent(m.from_user.id, sent, context="admin_user_summary", kind="menu")



async def cb_resend_link(c: types.CallbackQuery):
    bot = Bot.get_current()
    if not is_admin(c.from_user.id):
        return await c.answer()
    payload = c.data.replace("adm_resend_link_", "", 1)
    sub_id, user_id = payload.split("_", 1)
    item = subs.get_sub_detail(sub_id)
    if not item or str(item["owner"]) != str(user_id):
        return await c.answer("این سرویس برای این کاربر پیدا نشد.", show_alert=True)
    try:
        await bot.send_message(
            int(user_id),
            f"🔗 ارسال مجدد سرویس شما\n\n"
            f"شناسه سرویس: {item['account_name'] or '-'}\n"
            f"تاریخ خرید/تحویل: {item['assigned_at'] or '-'}\n\n"
            f"لینک:\n{item['link']}",
            reply_markup=menus.main_reply_kb(user_id),
        )
        db.log_admin_action(c.from_user.id, "resend_service_link", user_id, f"sub_id={sub_id}")
        await c.answer("✅ لینک سرویس برای کاربر ارسال شد.", show_alert=False)
    except Exception:
        await c.answer("❌ ارسال لینک به کاربر ناموفق بود.", show_alert=True)


async def cb_resend_qr(c: types.CallbackQuery):
    bot = Bot.get_current()
    if not is_admin(c.from_user.id):
        return await c.answer()
    payload = c.data.replace("adm_resend_qr_", "", 1)
    sub_id, user_id = payload.split("_", 1)
    item = subs.get_sub_detail(sub_id)
    if not item or str(item["owner"]) != str(user_id):
        return await c.answer("این سرویس برای این کاربر پیدا نشد.", show_alert=True)

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
        db.log_admin_action(c.from_user.id, "resend_service_qr", user_id, f"sub_id={sub_id}")
        await c.answer("✅ QR سرویس برای کاربر ارسال شد.", show_alert=False)
    except Exception:
        await c.answer("❌ ارسال QR به کاربر ناموفق بود.", show_alert=True)
    finally:
        cleanup_qr(qr_path)


def _panel_action_confirm_kb(action, sub_id, user_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ تأیید", callback_data=f"adm_panel_{action}_confirm_{sub_id}_{user_id}"),
        InlineKeyboardButton("❌ لغو", callback_data=f"adm_user_sub_detail_{sub_id}_{user_id}"),
    )
    return kb


async def cb_panel_usage(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer("در حال دریافت مصرف...", show_alert=False)
    payload = c.data.replace("adm_panel_usage_", "", 1)
    sub_id, user_id = payload.split("_", 1)
    item = subs.get_sub_detail(sub_id)
    if not item or (item["source_type"] or "pool") == "pool" or str(item["owner"]) != str(user_id):
        return await c.answer("سرویس تأمین‌کننده پیدا نشد.", show_alert=True)
    try:
        provider = subs.get_provider_adapter(item["source_type"] or item["panel_provider"])
        result = await provider.usage(item["panel_username"])
        usages = result.get("usages") or []
        total = sum(int(row.get("used_traffic") or 0) for row in usages if isinstance(row, dict))
        db.update_panel_sub_usage(sub_id, total)
        lines = [
            f"📊 مصرف سرویس | {subs.provider_label(item['source_type'])}",
            "",
            f"کاربر پنل: {item['panel_username']}",
            f"مصرف کل: {_fmt_bytes(total)}",
            f"حجم سرویس: {_fmt_bytes(item['panel_data_limit'])}",
            "",
            "مصرف نودها:",
        ]
        for row in usages[:20]:
            lines.append(f"• {row.get('node_name') or '-'}: {_fmt_bytes(row.get('used_traffic'))}")
        if not usages:
            lines.append("اطلاعات مصرفی ثبت نشده است.")
        db.log_admin_action(c.from_user.id, "panel_usage", user_id, f"sub_id={sub_id}; used={total}")
        await _replace_callback_message(c, "\n".join(lines), reply_markup=service_detail_kb(user_id, sub_id, item["purchase_id"]))
    except subs.ProviderError as exc:
        await c.answer(getattr(exc, "message", str(exc)), show_alert=True)


async def cb_panel_action_ask(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.replace("adm_panel_", "", 1)
    action, rest = raw.split("_ask_", 1)
    sub_id, user_id = rest.split("_", 1)
    item = subs.get_sub_detail(sub_id)
    if not item or (item["source_type"] or "pool") == "pool" or str(item["owner"]) != str(user_id):
        return await c.answer("سرویس تأمین‌کننده پیدا نشد.", show_alert=True)
    messages_map = {
        "reset": "مصرف ثبت‌شده این کاربر در پنل صفر می‌شود؛ حجم و زمان سرویس تغییر نمی‌کند.",
        "revoke": "لینک و شناسه اتصال قبلی باطل می‌شود و لینک جدید جایگزین خواهد شد.",
        "delete": "کاربر از تأمین‌کننده حذف می‌شود و سرویس از حساب مشتری مخفی خواهد شد. این عملیات قابل بازگردانی نیست.",
    }
    await _replace_callback_message(
        c,
        f"⚠️ تأیید عملیات تأمین‌کننده\n\nتأمین‌کننده: {subs.provider_label(item['source_type'])}\nکاربر: {item['panel_username']}\n{messages_map.get(action, '')}",
        reply_markup=_panel_action_confirm_kb(action, sub_id, user_id),
    )


async def cb_panel_action_confirm(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer("در حال انجام...", show_alert=False)
    raw = c.data.replace("adm_panel_", "", 1)
    action, rest = raw.split("_confirm_", 1)
    sub_id, user_id = rest.split("_", 1)
    item = subs.get_sub_detail(sub_id)
    if not item or (item["source_type"] or "pool") == "pool" or str(item["owner"]) != str(user_id):
        return await c.answer("سرویس تأمین‌کننده پیدا نشد.", show_alert=True)
    try:
        provider = subs.get_provider_adapter(item["source_type"] or item["panel_provider"])
        if action == "reset":
            result = await provider.reset_usage(item["panel_username"])
            db.update_panel_sub(sub_id, result)
            db.update_panel_sub_usage(sub_id, 0)
            message = "✅ مصرف سرویس در تأمین‌کننده صفر شد."
        elif action == "revoke":
            result = await provider.revoke_subscription(item["panel_username"])
            db.update_panel_sub(sub_id, result)
            message = "✅ لینک اشتراک باطل و لینک جدید ذخیره شد."
        elif action == "delete":
            await provider.delete_user(item["panel_username"])
            db.mark_panel_sub_deleted(sub_id)
            message = "✅ سرویس از تأمین‌کننده حذف شد."
        else:
            return await c.answer("عملیات نامعتبر است.", show_alert=True)
        db.log_admin_action(c.from_user.id, f"panel_{action}", user_id, f"sub_id={sub_id}; panel_username={item['panel_username']}")
        if action == "delete":
            return await _replace_callback_message(c, message, reply_markup=user_history_kb(user_id, db.list_user_purchases(user_id), subs.user_subs(user_id)))
        updated = subs.get_sub_detail(sub_id)
        await _replace_callback_message(c, message + "\n\n" + _fmt_service_detail(user_id, sub_id)[0], reply_markup=service_detail_kb(user_id, sub_id, updated["purchase_id"] if updated else None))
    except subs.ProviderError as exc:
        await c.answer(getattr(exc, "message", str(exc)), show_alert=True)


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
    await _admin_cleanup_tracked(m.chat.id, m.from_user.id)
    if not rows:
        sent = await m.answer("چیزی پیدا نشد.", reply_markup=admin_back_kb())
        await _track_admin_sent(m.from_user.id, sent, context="admin_search_empty", kind="menu")
        return
    for r in rows[:10]:
        await _send_long(m, _fmt_user_summary(r["id"]), reply_markup=user_detail_kb(r["id"]), owner_user_id=m.from_user.id, context="admin_user_search_result", kind="list")


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
    amount = parse_int(m.text, allow_negative=True)
    if amount is None or amount == 0:
        return await m.answer("لطفاً یک عدد غیرصفر بفرستید.", reply_markup=cancel_kb())
    try:
        new_balance = db.add_balance(
            data["target_id"],
            amount,
            action="admin_adjustment",
            note=f"admin_id={m.from_user.id}",
        )
    except ValueError as exc:
        return await m.answer(f"❌ {exc}", reply_markup=cancel_kb())
    db.log_admin_action(m.from_user.id, "balance_adjustment", data["target_id"], f"amount={amount}; balance={new_balance}")
    await state.finish()
    await m.answer(
        f"✅ موجودی کاربر {data['target_id']} به‌روزرسانی شد.\nموجودی جدید: {_fmt_money(new_balance)}",
        reply_markup=admin_back_kb(),
    )


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
    kb.add(InlineKeyboardButton("⬅️ بازگشت به کاتالوگ و فروش", callback_data="adm_section_services"))
    return kb


def link_back_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔗 بازگشت به استخر لینک‌ها", callback_data="adm_links"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به کاتالوگ و فروش", callback_data="adm_section_services"))
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

    kb.add(InlineKeyboardButton("🔗 بازگشت به استخر لینک‌ها", callback_data=back_callback))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به کاتالوگ و فروش", callback_data="adm_section_services"))
    return kb


async def cb_links(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    counts = subs.link_counts()

    text = (
        "🔗 استخر لینک‌ها\n\n"
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

    kb.add(InlineKeyboardButton("🔗 بازگشت به استخر لینک‌ها", callback_data="adm_links"))
    kb.add(InlineKeyboardButton("⬅️ بازگشت به کاتالوگ و فروش", callback_data="adm_section_services"))

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

    if m.content_type != "text":
        return await m.answer("لطفاً فقط Link ID عددی را بفرستید.", reply_markup=cancel_kb())

    link_id = parse_int(m.text)
    if link_id is None:
        return await m.answer("لطفاً فقط Link ID عددی را بفرستید.", reply_markup=cancel_kb())
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
    plans = [plan for plan in db.list_plans(active_only=True) if db.plan_provider_key(plan) == "pool"]
    if not plans:
        return await _replace_callback_message(
            c,
            "پلن استخری فعالی وجود ندارد. برای پلن‌های دارای تأمین‌کننده، لینک دستی وارد نمی‌شود.",
            reply_markup=admin_services_section_kb(),
        )
    if len(plans) > 1:
        kb = InlineKeyboardMarkup(row_width=1)
        for plan in plans:
            kb.add(InlineKeyboardButton(f"#{plan['id']} {plan['title']} | {_fmt_money(plan['price'])}", callback_data=f"adm_addsub_plan_{plan['id']}"))
        kb.add(InlineKeyboardButton("⬅️ برگشت به سرویس‌ها و پلن‌ها", callback_data="adm_section_services"))
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
    if db.plan_provider_key(plan) != "pool":
        return await _replace_callback_message(
            c,
            "این پلن توسط تأمین‌کننده به‌صورت خودکار ساخته می‌شود و استخر لینک دستی ندارد.",
            reply_markup=plan_detail_kb(plan_id),
        )
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
        f"کاربران واقعی: {db.count_real_users()}",
        f"کاربران تست: {db.count_test_users()}",
        f"کاربران واقعی فعال (۷ روز اخیر): {db.active_users_count(7)}",
        "",
        "💰 درآمد و مالی",
        f"مجموع شارژهای تأییدشده واقعی: {db.sum_approved_topups():,} تومان",
        f"موجودی کیف‌پول کاربران واقعی: {db.sum_all_balances():,} تومان",
        f"مجموع پاداش رفرال واقعی: {db.total_referral_rewards():,} تومان",
        f"شارژهای در انتظار بررسی: {db.count_pending_topups()}",
        "",
        "📦 سرویس",
        f"تحویل کل: {subs.sold_count()}",
        f"تحویل واقعی: {db.delivered_sub_counts_by_test_status()['real']}",
        f"تحویل تست: {db.delivered_sub_counts_by_test_status()['test']}",
        f"موجودی فعلی: {subs.stock_count()}",
        "",
        "🏷 موجودی پلن‌ها:",
    ]
    for plan in db.list_plans(limit=20):
        sales = db.plan_sales_by_test_status(plan["id"])
        lines.append(
            f"• #{plan['id']} {plan['title']}: موجودی {subs.stock_count(plan['id'])} | "
            f"تحویل واقعی {sales['real']} | تست {sales['test']} | قیمت {int(plan['price']):,}"
        )
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
        f"پرداخت‌های واقعی تأییدشده ۷ روز اخیر: {_fmt_money(db.approved_topups_total_for_days(7))}",
        f"موجودی کیف پول کاربران واقعی: {_fmt_money(db.sum_all_balances())}",
        f"گردش خرید تست ۳۰ روز اخیر: {_fmt_money(db.test_sales_total(30))}",
        "",
        "📦 موجودی پلن‌ها:",
    ]
    for plan in db.list_plans(limit=30):
        delivery_type = db.plan_delivery_type(plan)
        stock = subs.stock_count(plan["id"]) if delivery_type == "pool" else None
        sales = db.plan_sales_by_test_status(plan["id"])
        warn = " ⚠️" if delivery_type == "pool" and stock <= int(plan["low_stock_threshold"] or 0) else ""
        stock_label = f"موجودی {stock}" if delivery_type == "pool" else "ساخت خودکار"
        lines.append(
            f"• #{plan['id']} {plan['title']}: {stock_label} | "
            f"فروش واقعی {sales['real']} | تست {sales['test']} | "
            f"قیمت {_fmt_money(plan['price'])}{warn}"
        )
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


def categories_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("➕ ساخت دسته جدید", callback_data="category_create"))
    for category in db.list_plan_categories(active_only=False):
        active = "✅" if int(category["is_active"] or 0) else "🚫"
        kb.add(InlineKeyboardButton(
            f"{active} {category['emoji'] or '📦'} {category['title']} ({int(category['plan_count'] or 0)})",
            callback_data=f"category_detail_{category['id']}",
        ))
    kb.add(InlineKeyboardButton("⬅️ کاتالوگ و فروش", callback_data="adm_section_services"))
    return kb


def category_detail_kb(category_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ عنوان", callback_data=f"category_set_title_{category_id}"),
        InlineKeyboardButton("😀 ایموجی", callback_data=f"category_set_emoji_{category_id}"),
        InlineKeyboardButton("📝 توضیح", callback_data=f"category_set_description_{category_id}"),
        InlineKeyboardButton("👥 گروه هدف", callback_data=f"category_set_audience_{category_id}"),
        InlineKeyboardButton("🗓 شروع نمایش", callback_data=f"category_set_starts_at_{category_id}"),
        InlineKeyboardButton("⌛ پایان نمایش", callback_data=f"category_set_ends_at_{category_id}"),
        InlineKeyboardButton("👁 فعال/غیرفعال", callback_data=f"category_toggle_{category_id}"),
        InlineKeyboardButton("⬆️ بالا", callback_data=f"category_move_up_{category_id}"),
        InlineKeyboardButton("⬇️ پایین", callback_data=f"category_move_down_{category_id}"),
    )
    kb.add(InlineKeyboardButton("🗑 حذف دسته خالی", callback_data=f"category_delete_{category_id}"))
    kb.add(InlineKeyboardButton("⬅️ دسته‌ها", callback_data="adm_categories"))
    return kb


def category_audience_kb(category_id):
    kb = InlineKeyboardMarkup(row_width=2)
    options = [
        ("all", "همه"), ("buyers", "خریداران"),
        ("no_buy", "بدون خرید"), ("has_service", "دارای سرویس"),
        ("no_service", "بدون سرویس"), ("normal", "کاربر عادی"),
        ("test", "کاربر تست"), ("admins", "فقط ادمین"),
    ]
    for value, label in options:
        kb.insert(InlineKeyboardButton(label, callback_data=f"category_audience_{category_id}_{value}"))
    kb.add(InlineKeyboardButton("⬅️ جزئیات دسته", callback_data=f"category_detail_{category_id}"))
    return kb


def _fmt_category(category):
    return (
        f"🗂 دسته #{category['id']}\n\n"
        f"عنوان: {category['emoji'] or '📦'} {category['title']}\n"
        f"توضیح: {category['description'] or '-'}\n"
        f"ترتیب: {category['sort_order']}\n"
        f"وضعیت: {'فعال' if int(category['is_active'] or 0) else 'غیرفعال'}\n"
        f"گروه هدف: {BUTTON_AUDIENCE_LABELS.get(category['audience'] or 'all', category['audience'] or 'all')}\n"
        f"شروع نمایش: {category['starts_at'] or '-'}\n"
        f"پایان نمایش: {category['ends_at'] or '-'}\n"
        f"تعداد پلن: {int(category['plan_count'] or 0) if 'plan_count' in category.keys() else '-'}"
    )


def _parse_category_form(text):
    values = {}
    aliases = {
        "عنوان": "title", "title": "title", "ایموجی": "emoji", "emoji": "emoji",
        "توضیح": "description", "description": "description", "ترتیب": "sort_order", "order": "sort_order",
        "نمایش": "audience", "audience": "audience", "شروع": "starts_at", "starts_at": "starts_at",
        "پایان": "ends_at", "ends_at": "ends_at",
    }
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        field = aliases.get(key.strip().lower()) or aliases.get(key.strip())
        if field:
            values[field] = value.strip()
    if not values.get("title"):
        raise ValueError("عنوان دسته الزامی است")
    if "sort_order" in values:
        values["sort_order"] = int(values["sort_order"] or 100)
    values.setdefault("sort_order", 100)
    values.setdefault("is_active", 1)
    audience = (values.get("audience") or "all").strip().lower()
    if audience not in db.ALLOWED_CUSTOM_BUTTON_AUDIENCES:
        raise ValueError("گروه هدف معتبر نیست: all, buyers, no_buy, has_service, no_service, normal, test, admins")
    values["audience"] = audience
    for field in ("starts_at", "ends_at"):
        if values.get(field) in {"-", "none", "ندارد"}:
            values[field] = None
    return values


async def cb_categories(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(c, "🗂 مدیریت دسته‌های فروشگاه\n\nدسته‌ها صفحه اول خرید مشتری را می‌سازند؛ مثل VIP و اقتصادی.", reply_markup=categories_menu_kb())


async def cb_category_create(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    current = await state.get_data()
    await state.update_data(
        category_action="create",
        category_return_to_plan=current.get("plan_action") == "create_wizard",
    )
    await _replace_callback_message(
        c,
        "➕ ساخت دسته جدید\n\nفرم را بفرستید:\n\nعنوان: اقتصادی\nایموجی: 🌱\nتوضیح: پلن‌های مقرون‌به‌صرفه\nترتیب: 20",
        reply_markup=cancel_kb("adm_categories", "⬅️ دسته‌ها"),
    )
    await AdminStates.waiting_category_form.set()


async def process_category_form(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("فرم را به صورت متن بفرستید.", reply_markup=cancel_kb("adm_categories", "⬅️ دسته‌ها"))
    flow = await state.get_data()
    try:
        data = _parse_category_form(m.text)
        category_id = db.create_plan_category(data)
    except Exception as exc:
        return await m.answer(f"❌ دسته ساخته نشد: {exc}", reply_markup=cancel_kb("adm_categories", "⬅️ دسته‌ها"))
    db.log_admin_action(m.from_user.id, "create_category", None, f"category_id={category_id};title={data['title']}")
    if flow.get("category_return_to_plan"):
        plan_data = dict(flow.get("plan_data") or {})
        plan_data["category_id"] = category_id
        await state.set_data({"plan_action": "create_wizard", "plan_step": "title", "plan_data": plan_data})
        await state.set_state(AdminStates.waiting_plan_form.state)
        return await m.answer("✅ دسته ساخته و برای پلن انتخاب شد.\n\n" + _plan_wizard_step_text("title"), reply_markup=cancel_kb())
    await state.finish()
    category = db.get_plan_category(category_id)
    await m.answer("✅ دسته ساخته شد.\n\n" + _fmt_category(category), reply_markup=category_detail_kb(category_id))


async def cb_category_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    category_id = int(c.data.rsplit("_", 1)[1])
    category = db.get_plan_category(category_id)
    if not category:
        return await _replace_callback_message(c, "دسته پیدا نشد.", reply_markup=categories_menu_kb())
    await _replace_callback_message(c, _fmt_category(category), reply_markup=category_detail_kb(category_id))


CATEGORY_EDIT_FIELDS = {
    "title": "عنوان", "emoji": "ایموجی", "description": "توضیح",
    "audience": "گروه هدف", "starts_at": "شروع نمایش", "ends_at": "پایان نمایش",
}


async def cb_category_set(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("category_set_", 1)[1]
    field, category_id = raw.rsplit("_", 1)
    if field not in CATEGORY_EDIT_FIELDS:
        return await c.answer("فیلد نامعتبر است.", show_alert=True)
    category = db.get_plan_category(category_id)
    if not category:
        return await _replace_callback_message(c, "دسته پیدا نشد.", reply_markup=categories_menu_kb())
    if field == "audience":
        return await _replace_callback_message(c, "👥 گروه هدف دسته را انتخاب کنید:", reply_markup=category_audience_kb(category_id))
    await state.update_data(category_id=int(category_id), category_field=field)
    await _replace_callback_message(c, f"مقدار جدید «{CATEGORY_EDIT_FIELDS[field]}» را بفرستید.\nمقدار فعلی: {category[field] or '-'}", reply_markup=cancel_kb(f"category_detail_{category_id}", "⬅️ جزئیات دسته"))
    await AdminStates.waiting_category_setting.set()


async def process_category_setting(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    category_id = int(data["category_id"])
    field = data["category_field"]
    value = (m.text or "").strip()
    if value == "-":
        value = ""
    if field == "title" and not value:
        return await m.answer("عنوان نمی‌تواند خالی باشد.", reply_markup=cancel_kb(f"category_detail_{category_id}"))
    if field == "audience":
        value = value.lower()
        if value not in db.ALLOWED_CUSTOM_BUTTON_AUDIENCES:
            return await m.answer("مجاز: all, buyers, no_buy, has_service, no_service, normal, test, admins", reply_markup=cancel_kb(f"category_detail_{category_id}"))
    if field in {"starts_at", "ends_at"} and value in {"", "-", "none", "ندارد"}:
        value = None
    db.update_plan_category(category_id, {field: value})
    await state.finish()
    db.log_admin_action(m.from_user.id, "update_category", None, f"category_id={category_id};field={field}")
    category = db.get_plan_category(category_id)
    await m.answer("✅ دسته به‌روزرسانی شد.\n\n" + _fmt_category(category), reply_markup=category_detail_kb(category_id))


async def cb_category_audience(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("category_audience_", 1)[1]
    category_id_text, audience = raw.split("_", 1)
    if audience not in db.ALLOWED_CUSTOM_BUTTON_AUDIENCES:
        return await c.answer("گروه هدف نامعتبر است.", show_alert=True)
    category_id = int(category_id_text)
    if not db.update_plan_category(category_id, {"audience": audience}):
        return await _replace_callback_message(c, "دسته پیدا نشد.", reply_markup=categories_menu_kb())
    category = db.get_plan_category(category_id)
    db.log_admin_action(c.from_user.id, "update_category_audience", None, f"category_id={category_id};audience={audience}")
    await _replace_callback_message(c, "✅ گروه هدف دسته تغییر کرد.\n\n" + _fmt_category(category), reply_markup=category_detail_kb(category_id))


async def cb_category_toggle(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    category_id = int(c.data.rsplit("_", 1)[1])
    db.toggle_plan_category(category_id)
    category = db.get_plan_category(category_id)
    await _replace_callback_message(c, "✅ وضعیت دسته تغییر کرد.\n\n" + _fmt_category(category), reply_markup=category_detail_kb(category_id))


async def cb_category_move(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("category_move_", 1)[1]
    direction, category_id = raw.rsplit("_", 1)
    db.move_record("plan_categories", "id", int(category_id), direction)
    await _replace_callback_message(c, "✅ ترتیب دسته‌ها به‌روزرسانی شد.", reply_markup=categories_menu_kb())


async def cb_category_delete(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    category_id = int(c.data.rsplit("_", 1)[1])
    ok, reason = db.delete_plan_category(category_id)
    text = "✅ دسته حذف شد." if ok else ("❌ ابتدا پلن‌های داخل این دسته را جابه‌جا کنید." if reason == "not_empty" else "دسته پیدا نشد.")
    await _replace_callback_message(c, text, reply_markup=categories_menu_kb())


def providers_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    for provider in subs.list_provider_adapters(configured_only=False):
        status = "✅ متصل" if provider.configured() else "⚠️ تنظیم نشده"
        kb.add(InlineKeyboardButton(f"{provider.label} | {status}", callback_data=f"adm_provider_{provider.key}"))
    kb.add(InlineKeyboardButton("⬅️ کاتالوگ و فروش", callback_data="adm_section_services"))
    return kb


async def cb_providers(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    await _replace_callback_message(c, "🔌 تأمین‌کننده‌ها\n\nمنطق فروشگاه مستقل از پنل است. هر پنل از این بخش به‌عنوان یک تأمین‌کننده مدیریت می‌شود.", reply_markup=providers_menu_kb())


async def cb_provider_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    key = c.data.split("adm_provider_", 1)[1]
    try:
        provider = subs.get_provider_adapter(key)
    except Exception as exc:
        return await _replace_callback_message(c, f"❌ {exc}", reply_markup=providers_menu_kb())
    text = f"🔌 {provider.label}\n\nکلید فنی: {provider.key}\nوضعیت تنظیمات: {'کامل' if provider.configured() else 'ناقص'}"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🧪 تست اتصال", callback_data=f"adm_provider_health_{key}"))
    kb.add(InlineKeyboardButton("⬅️ تأمین‌کننده‌ها", callback_data="adm_providers"))
    await _replace_callback_message(c, text, reply_markup=kb)


async def cb_provider_health(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer("در حال بررسی اتصال...")
    key = c.data.split("adm_provider_health_", 1)[1]
    try:
        result = await subs.provider_health_check(key)
        provider = subs.get_provider_adapter(key)
        username = result.get("username") or result.get("admin", {}).get("username") or "-"
        text = f"✅ اتصال {provider.label} برقرار است.\nحساب: {username}"
    except Exception as exc:
        text = f"❌ تست اتصال ناموفق بود.\n{exc}"
    await _replace_callback_message(c, text, reply_markup=providers_menu_kb())


def trials_menu_kb(rows):
    kb = InlineKeyboardMarkup(row_width=1)
    for row in rows[:20]:
        label = row["username"] and f"@{row['username']}" or row["display_name"] or row["user_id"]
        status = {"completed": "✅", "pending": "⏳", "failed": "❌"}.get(row["status"], "•")
        kb.add(InlineKeyboardButton(f"{status} {label} | {row['status']}", callback_data=f"adm_trial_detail_{row['user_id']}"))
    kb.add(InlineKeyboardButton("⬅️ کاتالوگ و فروش", callback_data="adm_section_services"))
    return kb


async def cb_trials(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    stats = db.trial_claim_stats()
    rows = db.list_trial_claims(limit=30)
    text = (
        "🧪 اکانت‌های تست\n\n"
        f"کل درخواست‌ها: {stats['total']}\n"
        f"ساخته‌شده: {stats['completed']}\n"
        f"در حال ساخت: {stats['pending']}\n"
        f"ناموفق: {stats['failed']}\n\n"
        "برای جزئیات هر تست روی نام کاربر بزنید."
    )
    await _replace_callback_message(c, text, reply_markup=trials_menu_kb(rows), context="admin_trials", kind="list")


async def cb_trial_detail(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    user_id = c.data.split("adm_trial_detail_", 1)[1]
    claim = db.get_trial_claim(user_id)
    user = db.get_user(user_id)
    if not claim:
        return await _replace_callback_message(c, "رکورد تست پیدا نشد.", reply_markup=trials_menu_kb(db.list_trial_claims(limit=30)))
    lines = [
        "🧪 جزئیات اکانت تست",
        "",
        f"کاربر: {_display_username(user) if user else user_id}",
        f"Telegram ID: {user_id}",
        f"وضعیت: {claim['status']}",
        f"تأمین‌کننده: {subs.provider_label(claim['provider_key'] if 'provider_key' in claim.keys() else 'youpanel')}",
        f"نام کاربری تأمین‌کننده: {claim['panel_username'] or '-'}",
        f"تاریخ ساخت: {_dual(claim['created_at'])}",
        f"آخرین تغییر: {_dual(claim['updated_at'])}",
        f"خطا: {claim['error'] or '-'}",
    ]
    kb = InlineKeyboardMarkup(row_width=1)
    if claim["sub_id"]:
        kb.add(InlineKeyboardButton("📦 جزئیات سرویس تست", callback_data=f"adm_user_sub_detail_{claim['sub_id']}_{user_id}"))
    kb.add(InlineKeyboardButton("💬 ارسال پیام به کاربر", callback_data=f"adm_msg_user_{user_id}"))
    kb.add(InlineKeyboardButton("⬅️ اکانت‌های تست", callback_data="adm_trials"))
    await _replace_callback_message(c, "\n".join(lines), reply_markup=kb, context="admin_trial_detail", kind="list")


def admin_layout_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    for item in db.list_admin_menu_items(active_only=False):
        title = item["title"] or item["default_title"]
        mark = "✅" if int(item["is_active"] or 0) else "🚫"
        kb.row(
            InlineKeyboardButton("⬆️", callback_data=f"adm_layout_up_{item['key']}"),
            InlineKeyboardButton(f"{mark} {title}", callback_data=f"adm_layout_toggle_{item['key']}"),
            InlineKeyboardButton("⬇️", callback_data=f"adm_layout_down_{item['key']}"),
        )
    kb.add(InlineKeyboardButton("♻️ بازگردانی پیش‌فرض", callback_data="adm_layout_reset"))
    kb.add(InlineKeyboardButton("⬅️ محتوا و ظاهر", callback_data="adm_section_personalize"))
    return kb


async def cb_admin_layout(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    lines = ["🧭 چیدمان پنل مدیریت", "", "با فلش‌ها ترتیب را عوض کنید و با زدن عنوان، نمایش آن بخش را فعال/غیرفعال کنید."]
    await _replace_callback_message(c, "\n".join(lines), reply_markup=admin_layout_kb())


async def cb_admin_layout_action(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    if c.data == "adm_layout_reset":
        db.reset_admin_menu_items()
    else:
        raw = c.data.split("adm_layout_", 1)[1]
        action, key = raw.split("_", 1)
        item = db.get_admin_menu_item(key)
        if item:
            if action in {"up", "down"}:
                db.update_admin_menu_item(key, direction=action)
            elif action == "toggle":
                db.update_admin_menu_item(key, is_active=not bool(int(item["is_active"] or 0)))
    await _replace_callback_message(c, "✅ چیدمان پنل به‌روزرسانی شد.", reply_markup=admin_layout_kb())


def plans_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("➕ ساخت پلن جدید", callback_data="plan_create"))
    categories = {int(row["id"]): row for row in db.list_plan_categories(active_only=False)}
    for plan in db.list_plans(limit=100, include_disabled=True):
        active = "✅" if int(plan["is_active"] or 0) and db.plan_purchase_mode(plan) != "disabled" else "🚫"
        default = " ⭐" if int(plan["is_default"] or 0) else ""
        category = categories.get(int(plan["category_id"] or 0))
        cat = f"{category['emoji'] or '📦'} {category['title']}" if category else "بدون دسته"
        kb.add(InlineKeyboardButton(f"{active} {plan['title']} | {cat}{default}", callback_data=f"plan_detail_{plan['id']}"))
    kb.add(InlineKeyboardButton("⬅️ کاتالوگ و فروش", callback_data="adm_section_services"))
    return kb


def plan_detail_kb(plan_id):
    kb = InlineKeyboardMarkup(row_width=2)
    plan = db.get_plan(plan_id)
    provider_key = db.plan_provider_key(plan) if plan else "pool"
    kb.add(
        InlineKeyboardButton("⚙️ تنظیمات پلن", callback_data=f"plan_settings_{plan_id}"),
        InlineKeyboardButton("👁 فعال/غیرفعال", callback_data=f"plan_toggle_{plan_id}"),
        InlineKeyboardButton("⬆️ بالا", callback_data=f"plan_move_up_{plan_id}"),
        InlineKeyboardButton("⬇️ پایین", callback_data=f"plan_move_down_{plan_id}"),
    )
    if provider_key == "pool":
        kb.add(InlineKeyboardButton("📥 افزودن لینک به این پلن", callback_data=f"adm_addsub_plan_{plan_id}"))
    else:
        kb.add(InlineKeyboardButton("🔌 وضعیت تأمین‌کننده", callback_data=f"adm_provider_{provider_key}"))
    kb.add(InlineKeyboardButton("⬅️ مدیریت پلن‌ها", callback_data="adm_plans"))
    kb.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm_back"))
    return kb


PLAN_EDIT_FIELDS = {
    "title": ("عنوان پلن", "text"),
    "volume_label": ("حجم", "text"),
    "duration_label": ("مدت", "text"),
    "price": ("قیمت فروش", "int"),
    "description": ("توضیح کوتاه", "text"),
    "sort_order": ("ترتیب نمایش", "int"),
    "max_per_order": ("حداکثر خرید در سفارش", "int"),
    "cost_price": ("قیمت خرید/هزینه", "int"),
    "tag": ("برچسب", "text"),
    "low_stock_threshold": ("حد هشدار موجودی", "int"),
    "pre_purchase_text": ("متن اختصاصی قبل از خرید", "text"),
    "post_purchase_text": ("متن اختصاصی بعد از خرید", "text"),
    "panel_max_devices": ("حداکثر دستگاه", "optional_int"),
}


def plan_settings_kb(plan_id):
    plan = db.get_plan(plan_id)
    provider_key = db.plan_provider_key(plan) if plan else "pool"
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🗂 دسته", callback_data=f"plan_category_{plan_id}"),
        InlineKeyboardButton("🛒 نحوه خرید", callback_data=f"plan_purchase_mode_{plan_id}"),
        InlineKeyboardButton("عنوان", callback_data=f"plan_set_title_{plan_id}"),
        InlineKeyboardButton("قیمت", callback_data=f"plan_set_price_{plan_id}"),
        InlineKeyboardButton("حجم", callback_data=f"plan_set_volume_label_{plan_id}"),
        InlineKeyboardButton("مدت", callback_data=f"plan_set_duration_label_{plan_id}"),
        InlineKeyboardButton("توضیح", callback_data=f"plan_set_description_{plan_id}"),
        InlineKeyboardButton("برچسب", callback_data=f"plan_set_tag_{plan_id}"),
        InlineKeyboardButton("حداکثر خرید", callback_data=f"plan_set_max_per_order_{plan_id}"),
        InlineKeyboardButton("متن قبل خرید", callback_data=f"plan_set_pre_purchase_text_{plan_id}"),
        InlineKeyboardButton("متن بعد خرید", callback_data=f"plan_set_post_purchase_text_{plan_id}"),
        InlineKeyboardButton("🔄 روش تحویل", callback_data=f"plan_provider_{plan_id}"),
    )
    if provider_key == "pool":
        kb.add(
            InlineKeyboardButton("حد هشدار موجودی", callback_data=f"plan_set_low_stock_threshold_{plan_id}"),
            InlineKeyboardButton("👁 نمایش موجودی", callback_data=f"plan_toggle_stock_{plan_id}"),
        )
    else:
        kb.add(
            InlineKeyboardButton("⏱ شروع اعتبار", callback_data=f"plan_toggle_start_{plan_id}"),
            InlineKeyboardButton("📱 سقف دستگاه", callback_data=f"plan_set_panel_max_devices_{plan_id}"),
        )
    kb.add(InlineKeyboardButton("⬅️ جزئیات پلن", callback_data=f"plan_detail_{plan_id}"))
    return kb


def _fmt_bytes(value):
    value = int(value or 0)
    if value <= 0:
        return "-"
    if value % (1024 ** 3) == 0:
        return f"{value // (1024 ** 3)} GB"
    return f"{value / (1024 ** 2):.1f} MB"


def _parse_size_bytes(value):
    raw = (value or "").strip().upper().replace(" ", "").replace(",", ".")
    if not raw:
        return None
    import re
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(GB|G|MB|M)?", raw)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2) or "GB"
    multiplier = 1024 ** 3 if unit in {"GB", "G"} else 1024 ** 2
    result = int(amount * multiplier)
    return result if result > 0 else None


def _parse_duration_days(value):
    match = re.search(r"(\d+)", (value or "").replace(",", ""))
    if not match:
        return None
    days = int(match.group(1))
    return days if days > 0 else None


def _purchase_mode_label(value):
    return {"direct": "خرید مستقیم", "quantity": "انتخاب تعداد", "wholesale": "فقط عمده", "disabled": "غیرفعال"}.get(value, value)


def _fmt_plan(plan):
    stock = db.plan_stock_count(plan["id"])
    sold = db.plan_sold_count(plan["id"])
    category = db.get_plan_category(plan["category_id"]) if plan["category_id"] else None
    provider_key = db.plan_provider_key(plan)
    provider_name = "استخر لینک" if provider_key == "pool" else subs.provider_label(provider_key)
    pre_text = (plan["pre_purchase_text"] or "").strip()
    post_text = (plan["post_purchase_text"] or "").strip()
    lines = [
        f"🏷 پلن #{plan['id']}", "",
        f"دسته: {(category['emoji'] or '📦') + ' ' + category['title'] if category else '-'}",
        f"عنوان: {plan['title']}",
        f"حجم: {plan['volume_label'] or '-'}",
        f"مدت: {plan['duration_label'] or '-'}",
        f"قیمت فروش: {_fmt_money(plan['price'])}",
        f"نحوه خرید: {_purchase_mode_label(db.plan_purchase_mode(plan))}",
        f"روش تحویل: {provider_name}",
        f"توضیح: {plan['description'] or '-'}",
        f"برچسب: {plan['tag'] or '-'}",
        f"ترتیب نمایش: {plan['sort_order']}",
        f"حداکثر خرید: {plan['max_per_order']}",
        f"متن قبل خرید: {_short(pre_text, 80)}",
        f"متن بعد خرید: {_short(post_text, 80)}",
    ]
    if provider_key == "pool":
        lines += [
            f"نمایش موجودی: {'بله' if int(plan['show_stock'] or 0) else 'خیر'}",
            f"حد هشدار: {plan['low_stock_threshold']}",
        ]
    else:
        lines += [
            f"شروع اعتبار: {'از زمان ساخت' if plan['panel_start_mode'] == 'active' else 'از اولین اتصال'}",
            f"دستگاه مجاز: {int(plan['panel_max_devices']) if plan['panel_max_devices'] not in (None, '') else 'بدون محدودیت'}",
        ]
    lines += [
        f"وضعیت: {'فعال' if int(plan['is_active'] or 0) else 'غیرفعال'}",
        f"موجودی آزاد: {stock if provider_key == 'pool' else 'ساخت خودکار'}",
        f"فروخته‌شده: {sold}",
    ]
    return "\n".join(lines)


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
        "هشدار موجودی: 5\n"
        "متن قبل خرید: \n"
        "متن بعد خرید: \n"
        "روش تحویل: pool\n"
        "حجم پنلی: 50GB\n"
        "مدت پنلی: 30\n"
        "حداکثر دستگاه: 2\n"
        "شروع اعتبار: on_hold"
    )
    if current:
        sample = current
    return "فرم کامل پلن را به این شکل بفرستید:\n\n" + sample


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
        "متن قبل خرید": "pre_purchase_text", "pre_purchase_text": "pre_purchase_text",
        "متن بعد خرید": "post_purchase_text", "post_purchase_text": "post_purchase_text",
        "روش تحویل": "delivery_type", "delivery_type": "delivery_type",
        "حجم پنلی": "panel_data_limit_bytes", "panel_size": "panel_data_limit_bytes",
        "مدت پنلی": "panel_duration_days", "panel_days": "panel_duration_days",
        "حداکثر دستگاه": "panel_max_devices", "panel_max_devices": "panel_max_devices",
        "شروع اعتبار": "panel_start_mode", "panel_start": "panel_start_mode",
    }
    data = {}
    for raw in (text or "").splitlines():
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        key = aliases.get(k.strip())
        if key:
            value = v.strip()
            if key in {"price", "sort_order", "max_per_order", "cost_price", "low_stock_threshold", "panel_duration_days"}:
                value = int(value.replace(",", "") or 0)
            if key == "panel_max_devices":
                raw_devices = value.replace(",", "").strip()
                value = None if raw_devices in {"", "-", "0", "none", "unlimited", "نامحدود"} else int(raw_devices)
                if value is not None and value <= 0:
                    raise ValueError("حداکثر دستگاه باید عدد مثبت یا نامحدود باشد")
            if key == "panel_data_limit_bytes":
                parsed_size = _parse_size_bytes(value)
                if not parsed_size:
                    raise ValueError("حجم پنلی معتبر نیست؛ مثال 50GB یا 200MB")
                value = parsed_size
            if key == "delivery_type":
                value = "youpanel" if value.lower() in {"youpanel", "panel", "پنل", "خودکار"} else "pool"
            if key == "panel_start_mode":
                value = "active" if value.lower() in {"active", "ساخت", "زمان ساخت"} else "on_hold"
            if key in {"is_active", "show_stock"}:
                value = 0 if value.lower() in {"inactive", "off", "0", "false", "غیرفعال", "no", "خیر"} else 1
            data[key] = value
    return data


async def cb_plans(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    categories = {int(row["id"]): row for row in db.list_plan_categories(active_only=False)}
    lines = ["🏷 مدیریت پلن‌ها", "", "ساخت، ویرایش و ترتیب همه پلن‌ها از همین صفحه انجام می‌شود.", ""]
    for idx, plan in enumerate(db.list_plans(limit=100, include_disabled=True), start=1):
        category = categories.get(int(plan["category_id"] or 0))
        cat = f"{category['emoji'] or '📦'} {category['title']}" if category else "بدون دسته"
        provider = "استخر" if db.plan_provider_key(plan) == "pool" else subs.provider_label(db.plan_provider_key(plan))
        lines.append(f"{idx}. {'✅' if int(plan['is_active'] or 0) and db.plan_purchase_mode(plan) != 'disabled' else '🚫'} {plan['title']} | {cat}\n   {_fmt_money(plan['price'])} | {_purchase_mode_label(db.plan_purchase_mode(plan))} | {provider}")
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
    await state.update_data(plan_action="create_wizard", plan_step="category", plan_data={})
    await _replace_callback_message(c, "➕ ساخت پلن جدید\n\nابتدا دسته نمایش پلن را انتخاب کنید.", reply_markup=_plan_category_select_kb())
    await AdminStates.waiting_plan_form.set()


async def cb_plan_edit(c: types.CallbackQuery, state: FSMContext):
    """فرم پیشرفته سازگار؛ مسیر اصلی ویرایش، تنظیمات تک‌فیلدی است."""
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_edit_", 1)[1])
    plan = db.get_plan(plan_id)
    if not plan:
        return await c.message.answer("این پلن پیدا نشد.", reply_markup=plans_menu_kb())
    current = (
        f"عنوان: {plan['title']}\nحجم: {plan['volume_label'] or ''}\nمدت: {plan['duration_label'] or ''}\n"
        f"قیمت: {plan['price']}\nتوضیح: {plan['description'] or ''}\nترتیب: {plan['sort_order']}\n"
        f"حداکثر: {plan['max_per_order']}\nبرچسب: {plan['tag'] or ''}\n"
        f"نمایش موجودی: {'yes' if int(plan['show_stock'] or 0) else 'no'}\n"
        f"هشدار موجودی: {plan['low_stock_threshold']}\nروش تحویل: {db.plan_provider_key(plan)}\n"
        f"حداکثر دستگاه: {plan['panel_max_devices'] if plan['panel_max_devices'] not in (None, '') else 'نامحدود'}\n"
        f"شروع اعتبار: {plan['panel_start_mode'] or 'on_hold'}"
    )
    await state.update_data(plan_action="edit", plan_id=plan_id)
    await _replace_callback_message(c, "✏️ تنظیمات پیشرفته پلن\n\n" + _plan_form_help(current), reply_markup=cancel_kb(f"plan_detail_{plan_id}", "⬅️ جزئیات پلن"))
    await AdminStates.waiting_plan_form.set()


def _plan_wizard_preview(data):
    category = db.get_plan_category(data.get("category_id")) if data.get("category_id") else None
    provider_key = data.get("provider_key") or "pool"
    provider = "استخر لینک" if provider_key == "pool" else subs.provider_label(provider_key)
    lines = [
        "🧪 پیش‌نمایش پلن جدید:", "",
        f"دسته: {(category['emoji'] or '📦') + ' ' + category['title'] if category else '-'}",
        f"عنوان: {data.get('title') or '-'}",
        f"حجم: {data.get('volume_label') or '-'}",
        f"مدت: {data.get('duration_label') or '-'}",
        f"قیمت: {int(data.get('price') or 0):,} تومان",
        f"نحوه خرید: {_purchase_mode_label(data.get('purchase_mode') or 'quantity')}",
        f"روش تحویل: {provider}",
        f"توضیح: {data.get('description') or '-'}",
    ]
    if provider_key != "pool":
        lines += [
            f"تعداد دستگاه: {data.get('panel_max_devices') if data.get('panel_max_devices') not in (None, '') else 'بدون محدودیت'}",
            f"شروع اعتبار: {'از زمان ساخت' if data.get('panel_start_mode') == 'active' else 'از اولین اتصال'}",
        ]
    return "\n".join(lines)


def _plan_wizard_confirm_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("✅ ثبت پلن", callback_data="plan_wizard_save"))
    kb.add(InlineKeyboardButton("⬅️ برگشت به مرحله قبل", callback_data="fsm_back"))
    kb.add(InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    return kb


async def process_plan_form(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.content_type != "text":
        return await m.answer("لطفاً مقدار را به صورت متن بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    if data.get("plan_action") == "create_wizard":
        step = data.get("plan_step") or "category"
        plan_data = dict(data.get("plan_data") or {})
        value = (m.text or "").strip()
        if value in {"-", "رد", "skip", "Skip"}:
            value = ""
        if step == "title":
            if not value:
                return await m.answer("عنوان پلن الزامی است.", reply_markup=cancel_kb())
            plan_data["title"] = value
            await state.update_data(plan_step="volume", plan_data=plan_data)
            return await m.answer(_plan_wizard_step_text("volume"), reply_markup=cancel_kb())
        if step == "volume":
            size = _parse_size_bytes(value)
            if not size:
                return await m.answer("حجم را مثل 50GB یا 200MB بفرستید.", reply_markup=cancel_kb())
            plan_data["volume_label"] = value.upper().replace(" ", "")
            plan_data["panel_data_limit_bytes"] = size
            await state.update_data(plan_step="duration", plan_data=plan_data)
            return await m.answer(_plan_wizard_step_text("duration"), reply_markup=cancel_kb())
        if step == "duration":
            days = _parse_duration_days(value)
            if not days:
                return await m.answer("مدت را به شکل عدد روز بفرستید؛ مثال: 30 روز", reply_markup=cancel_kb())
            plan_data["duration_label"] = value
            plan_data["panel_duration_days"] = days
            await state.update_data(plan_step="price", plan_data=plan_data)
            return await m.answer(_plan_wizard_step_text("price"), reply_markup=cancel_kb())
        if step == "price":
            raw = value.replace(",", "")
            if not raw.isdigit() or int(raw) <= 0:
                return await m.answer("قیمت معتبر نیست. فقط عدد مثبت بفرستید.", reply_markup=cancel_kb())
            plan_data["price"] = int(raw)
            await state.update_data(plan_step="purchase_mode", plan_data=plan_data)
            return await m.answer("نحوه خرید این پلن را انتخاب کنید:", reply_markup=_plan_purchase_mode_kb())
        if step == "description":
            plan_data["description"] = value
            plan_data.setdefault("low_stock_threshold", settings.low_stock_threshold())
            plan_data.setdefault("show_stock", 1 if plan_data.get("provider_key") == "pool" else 0)
            plan_data.setdefault("is_active", 1)
            plan_data.setdefault("sort_order", 100)
            await state.update_data(plan_step="confirm", plan_data=plan_data)
            return await m.answer(_plan_wizard_preview(plan_data), reply_markup=_plan_wizard_confirm_kb())
        return await m.answer("این مرحله با دکمه‌های زیر ادامه پیدا می‌کند.", reply_markup=cancel_kb())

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
    db.log_admin_action(m.from_user.id, "update_plan", None, f"plan_id={plan_id}")
    await m.answer("✅ پلن ذخیره شد.\n\n" + _fmt_plan(db.get_plan(plan_id)), reply_markup=plan_detail_kb(plan_id))


async def cb_plan_wizard_category(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    data = await state.get_data()
    if data.get("plan_action") != "create_wizard" or data.get("plan_step") != "category":
        return await c.answer("این مرحله فعال نیست.", show_alert=True)
    await c.answer()
    category_id = int(c.data.rsplit("_", 1)[1])
    category = db.get_plan_category(category_id)
    if not category:
        return await c.answer("دسته پیدا نشد.", show_alert=True)
    plan_data = dict(data.get("plan_data") or {})
    plan_data["category_id"] = category_id
    await state.update_data(plan_step="title", plan_data=plan_data)
    await _replace_callback_message(c, _plan_wizard_step_text("title"), reply_markup=cancel_kb(), cleanup=False)


async def cb_plan_wizard_mode(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    data = await state.get_data()
    if data.get("plan_action") != "create_wizard" or data.get("plan_step") != "purchase_mode":
        return await c.answer("این مرحله فعال نیست.", show_alert=True)
    await c.answer()
    mode = c.data.rsplit("_", 1)[-1]
    if mode not in {"direct", "quantity", "wholesale"}:
        return await c.answer("حالت خرید نامعتبر است.", show_alert=True)
    plan_data = dict(data.get("plan_data") or {})
    plan_data["purchase_mode"] = mode
    plan_data["max_per_order"] = 1 if mode in {"direct", "wholesale"} else 4
    await state.update_data(plan_step="delivery", plan_data=plan_data)
    await _replace_callback_message(c, "روش تحویل این پلن را انتخاب کنید:", reply_markup=_plan_delivery_kb(), cleanup=False)


async def cb_plan_wizard_delivery(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    data = await state.get_data()
    if data.get("plan_action") != "create_wizard" or data.get("plan_step") != "delivery":
        return await c.answer("این مرحله فعال نیست.", show_alert=True)
    await c.answer()
    plan_data = dict(data.get("plan_data") or {})
    choice = c.data.rsplit("_", 1)[-1]
    if choice == "pool":
        plan_data.update({"provider_key": "pool", "delivery_type": "pool", "panel_max_devices": None, "panel_start_mode": "on_hold"})
        await state.update_data(plan_step="description", plan_data=plan_data)
        return await _replace_callback_message(c, _plan_wizard_step_text("description"), reply_markup=cancel_kb(), cleanup=False)
    await state.update_data(plan_step="provider", plan_data=plan_data)
    return await _replace_callback_message(c, "تأمین‌کننده ساخت خودکار را انتخاب کنید:", reply_markup=_plan_provider_kb(), cleanup=False)


async def cb_plan_wizard_provider(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    data = await state.get_data()
    if data.get("plan_action") != "create_wizard" or data.get("plan_step") != "provider":
        return await c.answer("این مرحله فعال نیست.", show_alert=True)
    await c.answer()
    provider_key = c.data.split("plan_wizard_provider_", 1)[1]
    try:
        subs.get_provider_adapter(provider_key)
    except Exception as exc:
        return await c.answer(str(exc), show_alert=True)
    plan_data = dict(data.get("plan_data") or {})
    plan_data.update({"provider_key": provider_key, "delivery_type": "youpanel" if provider_key == "youpanel" else provider_key})
    await state.update_data(plan_step="panel_devices", plan_data=plan_data)
    await _replace_callback_message(c, "📱 تعداد دستگاه مجاز را انتخاب کنید:", reply_markup=_plan_device_limit_kb(), cleanup=False)


async def cb_plan_wizard_devices(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    data = await state.get_data()
    if data.get("plan_action") != "create_wizard" or data.get("plan_step") != "panel_devices":
        return await c.answer("این مرحله فعال نیست.", show_alert=True)
    await c.answer()
    plan_data = dict(data.get("plan_data") or {})
    raw_value = c.data.rsplit("_", 1)[-1]
    plan_data["panel_max_devices"] = None if raw_value == "unlimited" else int(raw_value)
    await state.update_data(plan_step="panel_start", plan_data=plan_data)
    await _replace_callback_message(c, "زمان شروع اعتبار را انتخاب کنید:", reply_markup=_plan_start_mode_kb(), cleanup=False)


async def cb_plan_wizard_start(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    data = await state.get_data()
    if data.get("plan_action") != "create_wizard" or data.get("plan_step") != "panel_start":
        return await c.answer("این مرحله فعال نیست.", show_alert=True)
    await c.answer()
    plan_data = dict(data.get("plan_data") or {})
    plan_data["panel_start_mode"] = "active" if c.data.endswith("_active") else "on_hold"
    plan_data["panel_reset_strategy"] = "no_reset"
    await state.update_data(plan_step="description", plan_data=plan_data)
    await _replace_callback_message(c, _plan_wizard_step_text("description"), reply_markup=cancel_kb(), cleanup=False)


async def cb_plan_wizard_save(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    data = await state.get_data()
    if data.get("plan_action") != "create_wizard" or data.get("plan_step") != "confirm":
        return await c.answer("فرم ساخت پلن آماده ثبت نیست.", show_alert=True)
    plan_data = dict(data.get("plan_data") or {})
    try:
        plan_id = db.create_plan(plan_data)
    except Exception as exc:
        return await c.answer(f"خطا در ثبت پلن: {exc}", show_alert=True)
    await state.finish()
    db.log_admin_action(c.from_user.id, "create_plan", None, f"plan_id={plan_id};title={plan_data.get('title','')};provider={plan_data.get('provider_key','pool')}")
    plan = db.get_plan(plan_id)
    kb = InlineKeyboardMarkup(row_width=1)
    if db.plan_provider_key(plan) == "pool":
        kb.add(InlineKeyboardButton("📥 افزودن لینک برای این پلن", callback_data=f"adm_addsub_plan_{plan_id}"))
    else:
        kb.add(InlineKeyboardButton("🔌 مشاهده تأمین‌کننده", callback_data=f"adm_provider_{db.plan_provider_key(plan)}"))
    kb.add(InlineKeyboardButton("⚙️ تنظیمات این پلن", callback_data=f"plan_settings_{plan_id}"))
    kb.add(InlineKeyboardButton("⬅️ مدیریت پلن‌ها", callback_data="adm_plans"))
    await _replace_callback_message(c, "✅ پلن ساخته شد.\n\n" + _fmt_plan(plan), reply_markup=kb)


def plan_category_select_kb(plan_id):
    kb = InlineKeyboardMarkup(row_width=1)
    for category in db.list_plan_categories(active_only=False):
        kb.add(InlineKeyboardButton(f"{category['emoji'] or '📦'} {category['title']}", callback_data=f"plan_set_category_{plan_id}_{category['id']}"))
    kb.add(InlineKeyboardButton("⬅️ تنظیمات پلن", callback_data=f"plan_settings_{plan_id}"))
    return kb


def plan_purchase_mode_select_kb(plan_id):
    kb = InlineKeyboardMarkup(row_width=1)
    for key, label in [("direct", "🛒 خرید مستقیم"), ("quantity", "🔢 انتخاب تعداد"), ("wholesale", "📦 فقط عمده"), ("disabled", "🚫 غیرفعال")]:
        kb.add(InlineKeyboardButton(label, callback_data=f"plan_set_mode_{plan_id}_{key}"))
    kb.add(InlineKeyboardButton("⬅️ تنظیمات پلن", callback_data=f"plan_settings_{plan_id}"))
    return kb


def plan_provider_select_kb(plan_id):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📦 استخر لینک", callback_data=f"plan_set_provider_{plan_id}_pool"))
    for provider in subs.list_provider_adapters(configured_only=False):
        mark = "✅" if provider.configured() else "⚠️"
        kb.add(InlineKeyboardButton(f"{mark} {provider.label}", callback_data=f"plan_set_provider_{plan_id}_{provider.key}"))
    kb.add(InlineKeyboardButton("⬅️ تنظیمات پلن", callback_data=f"plan_settings_{plan_id}"))
    return kb


async def cb_plan_category(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_category_", 1)[1])
    await _replace_callback_message(c, "🗂 دسته جدید پلن را انتخاب کنید:", reply_markup=plan_category_select_kb(plan_id))


async def cb_plan_set_category(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("plan_set_category_", 1)[1]
    plan_id, category_id = map(int, raw.split("_", 1))
    if not db.get_plan_category(category_id):
        return await c.answer("دسته پیدا نشد.", show_alert=True)
    db.update_plan(plan_id, {"category_id": category_id})
    await _replace_callback_message(c, "✅ دسته پلن تغییر کرد.\n\n" + _fmt_plan(db.get_plan(plan_id)), reply_markup=plan_settings_kb(plan_id))


async def cb_plan_purchase_mode(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_purchase_mode_", 1)[1])
    await _replace_callback_message(c, "🛒 نحوه خرید این پلن را انتخاب کنید:", reply_markup=plan_purchase_mode_select_kb(plan_id))


async def cb_plan_set_mode(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("plan_set_mode_", 1)[1]
    plan_id_text, mode = raw.split("_", 1)
    plan_id = int(plan_id_text)
    if mode not in {"direct", "quantity", "wholesale", "disabled"}:
        return await c.answer("حالت نامعتبر است.", show_alert=True)
    values = {"purchase_mode": mode}
    if mode in {"direct", "wholesale"}:
        values["max_per_order"] = 1
    elif mode == "quantity" and int(db.get_plan(plan_id)["max_per_order"] or 1) < 2:
        values["max_per_order"] = 4
    db.update_plan(plan_id, values)
    await _replace_callback_message(c, "✅ نحوه خرید تغییر کرد.\n\n" + _fmt_plan(db.get_plan(plan_id)), reply_markup=plan_settings_kb(plan_id))


async def cb_plan_provider(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_provider_", 1)[1])
    await _replace_callback_message(c, "🔌 روش تحویل/تأمین‌کننده را انتخاب کنید:", reply_markup=plan_provider_select_kb(plan_id))


async def cb_plan_set_provider(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("plan_set_provider_", 1)[1]
    plan_id_text, provider_key = raw.split("_", 1)
    plan_id = int(plan_id_text)
    plan = db.get_plan(plan_id)
    if not plan:
        return await c.answer("پلن پیدا نشد.", show_alert=True)
    values = {"provider_key": provider_key, "delivery_type": "pool" if provider_key == "pool" else provider_key, "show_stock": 1 if provider_key == "pool" else 0}
    if provider_key != "pool":
        try:
            subs.get_provider_adapter(provider_key)
        except Exception as exc:
            return await c.answer(str(exc), show_alert=True)
        size = _parse_size_bytes(plan["volume_label"])
        days = _parse_duration_days(plan["duration_label"])
        if not size or not days:
            return await c.answer("ابتدا حجم و مدت را با قالب قابل تبدیل مثل 50GB و 30 روز تنظیم کنید.", show_alert=True)
        values.update({"panel_data_limit_bytes": size, "panel_duration_days": days})
    db.update_plan(plan_id, values)
    await _replace_callback_message(c, "✅ روش تحویل تغییر کرد.\n\n" + _fmt_plan(db.get_plan(plan_id)), reply_markup=plan_settings_kb(plan_id))


async def cb_plan_move(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("plan_move_", 1)[1]
    direction, plan_id = raw.rsplit("_", 1)
    plan = db.get_plan(plan_id)
    if plan:
        db.move_record("plans", "id", int(plan_id), direction, "category_id=?", (int(plan["category_id"] or 0),))
    await _replace_callback_message(c, "✅ ترتیب پلن‌ها به‌روزرسانی شد.", reply_markup=plans_menu_kb())


async def cb_plan_settings(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_settings_", 1)[1])
    plan = db.get_plan(plan_id)
    if not plan:
        return await _replace_callback_message(c, "این پلن پیدا نشد.", reply_markup=plans_menu_kb())
    await _replace_callback_message(c, "⚙️ تنظیمات اختصاصی این پلن\n\n" + _fmt_plan(plan), reply_markup=plan_settings_kb(plan_id))


async def cb_plan_set_field(c: types.CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("plan_set_", 1)[1]
    field, plan_id = raw.rsplit("_", 1)
    info = PLAN_EDIT_FIELDS.get(field)
    if not info:
        return await c.message.answer("این فیلد قابل ویرایش نیست.", reply_markup=plans_menu_kb())
    plan = db.get_plan(int(plan_id))
    if not plan:
        return await c.message.answer("این پلن پیدا نشد.", reply_markup=plans_menu_kb())
    label, ftype = info
    current = plan[field] if field in plan.keys() else ""
    await state.update_data(plan_id=int(plan_id), plan_setting_plan_id=int(plan_id), plan_field=field, plan_field_type=ftype, plan_field_label=label)
    if ftype == "int":
        hint = "فقط عدد مثبت بفرستید."
    elif ftype == "optional_int":
        hint = "عدد را بفرستید؛ برای بدون محدودیت، - بفرستید."
    elif ftype == "size":
        hint = "حجم را مثل 50GB یا 200MB بفرستید."
    else:
        hint = "متن جدید را بفرستید. برای خالی کردن، - بفرستید."
    await c.message.answer(f"✏️ ویرایش «{label}»\nمقدار فعلی:\n{current or '-'}\n\n{hint}", reply_markup=cancel_kb())
    await AdminStates.waiting_plan_setting_value.set()


async def process_plan_setting_value(m: types.Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    plan_id = int(data["plan_id"])
    field = data["plan_field"]
    ftype = data["plan_field_type"]
    label = data.get("plan_field_label") or field
    value = (m.text or "").strip()
    if value == "-":
        value = ""
    if ftype == "int":
        raw = value.replace(",", "")
        if not raw.isdigit() or int(raw) <= 0:
            return await m.answer("لطفاً فقط عدد مثبت بفرستید.", reply_markup=cancel_kb())
        value = int(raw)
    elif ftype == "optional_int":
        if value in {"", "-"}:
            value = ""
        else:
            raw = value.replace(",", "")
            if not raw.isdigit() or int(raw) <= 0:
                return await m.answer("عدد مثبت بفرستید یا برای بدون محدودیت - بفرستید.", reply_markup=cancel_kb())
            value = int(raw)
    elif ftype == "size":
        parsed_size = _parse_size_bytes(value)
        if not parsed_size:
            return await m.answer("حجم معتبر نیست. مثال: 50GB یا 200MB", reply_markup=cancel_kb())
        value = parsed_size
    updates = {field: value}
    current_plan = db.get_plan(plan_id)
    if current_plan and db.plan_provider_key(current_plan) != "pool":
        if field == "volume_label":
            parsed = _parse_size_bytes(str(value))
            if not parsed:
                return await m.answer("برای پلن خودکار، حجم باید مثل 50GB یا 200MB باشد.", reply_markup=cancel_kb())
            updates["panel_data_limit_bytes"] = parsed
        elif field == "duration_label":
            days = _parse_duration_days(str(value))
            if not days:
                return await m.answer("برای پلن خودکار، مدت باید مثل 30 روز باشد.", reply_markup=cancel_kb())
            updates["panel_duration_days"] = days
    try:
        db.update_plan(plan_id, updates)
    except Exception as exc:
        return await m.answer(f"❌ ذخیره نشد: {exc}", reply_markup=cancel_kb())
    await state.finish()
    db.log_admin_action(m.from_user.id, "update_plan_field", None, f"plan_id={plan_id}; field={field}; label={label}")
    plan = db.get_plan(plan_id)
    await m.answer("✅ تنظیمات پلن به‌روزرسانی شد.\n\n" + _fmt_plan(plan), reply_markup=plan_settings_kb(plan_id))


async def cb_plan_toggle_delivery(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_toggle_delivery_", 1)[1])
    plan = db.get_plan(plan_id)
    if not plan:
        return await _replace_callback_message(c, "این پلن پیدا نشد.", reply_markup=plans_menu_kb())
    new_value = "pool" if db.plan_delivery_type(plan) == "youpanel" else "youpanel"
    db.update_plan(plan_id, {"delivery_type": new_value})
    db.log_admin_action(c.from_user.id, "toggle_plan_delivery", None, f"plan_id={plan_id}; delivery_type={new_value}")
    plan = db.get_plan(plan_id)
    warning = ""
    if new_value == "youpanel" and (int(plan["panel_data_limit_bytes"] or 0) <= 0 or int(plan["panel_duration_days"] or 0) <= 0):
        warning = "\n\n⚠️ قبل از فروش، حجم و مدت پنلی را تنظیم کنید."
    await _replace_callback_message(c, "✅ روش تحویل تغییر کرد." + warning + "\n\n" + _fmt_plan(plan), reply_markup=plan_settings_kb(plan_id))


async def cb_plan_toggle_start(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_toggle_start_", 1)[1])
    plan = db.get_plan(plan_id)
    if not plan:
        return await _replace_callback_message(c, "این پلن پیدا نشد.", reply_markup=plans_menu_kb())
    new_value = "active" if (plan["panel_start_mode"] or "on_hold") == "on_hold" else "on_hold"
    db.update_plan(plan_id, {"panel_start_mode": new_value})
    db.log_admin_action(c.from_user.id, "toggle_plan_start_mode", None, f"plan_id={plan_id}; mode={new_value}")
    await _replace_callback_message(c, "✅ زمان شروع اعتبار تغییر کرد.\n\n" + _fmt_plan(db.get_plan(plan_id)), reply_markup=plan_settings_kb(plan_id))


async def cb_panel_health(c: types.CallbackQuery):
    """Compatibility callback for old YouPanel health buttons."""
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer("در حال بررسی اتصال...", show_alert=False)
    try:
        result = await subs.provider_health_check("youpanel")
        username = result.get("username") or result.get("admin", {}).get("username") or "-"
        quota = result.get("data_limit") or result.get("admin", {}).get("data_limit")
        usage = result.get("users_usage") or result.get("admin", {}).get("users_usage")
        text = "✅ اتصال تأمین‌کننده YouPanel برقرار است.\n" f"حساب پنل: {username}\n"
        if quota is not None:
            text += f"سهمیه پنل: {_fmt_bytes(quota)}\n"
        if usage is not None:
            text += f"مصرف کاربران: {_fmt_bytes(usage)}"
    except subs.ProviderError as exc:
        text = f"❌ اتصال تأمین‌کننده ناموفق است.\nدلیل: {getattr(exc, 'message', str(exc))}"
    await _replace_callback_message(c, text, reply_markup=providers_menu_kb())


async def cb_plan_toggle_stock(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    plan_id = int(c.data.split("plan_toggle_stock_", 1)[1])
    plan = db.get_plan(plan_id)
    if not plan:
        return await _replace_callback_message(c, "این پلن پیدا نشد.", reply_markup=plans_menu_kb())
    new_value = 0 if int(plan["show_stock"] or 0) else 1
    db.update_plan(plan_id, {"show_stock": new_value})
    db.log_admin_action(c.from_user.id, "toggle_plan_show_stock", None, f"plan_id={plan_id}; show_stock={new_value}")
    plan = db.get_plan(plan_id)
    await _replace_callback_message(c, "✅ وضعیت نمایش موجودی تغییر کرد.\n\n" + _fmt_plan(plan), reply_markup=plan_settings_kb(plan_id))


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
    ("ref_reward", "پاداش رفرال", settings.ref_reward, "int"),
    ("card_number", "شماره کارت", settings.card_number, "text"),
    ("card_holder", "نام صاحب کارت", settings.card_holder, "text"),
    ("min_topup", "حداقل شارژ", settings.min_topup, "int"),
]
_STATUS_MESSAGE_FIELDS = {
    "bot_disabled_message": ("پیام خاموش بودن ربات", settings.bot_disabled_message, "text"),
    "sales_closed_message": ("پیام بسته بودن فروش", settings.sales_closed_message, "text"),
}
_FIELDS_BY_KEY = {f[0]: f for f in SETTING_FIELDS}
_FIELDS_BY_KEY.update(_STATUS_MESSAGE_FIELDS)


def settings_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton(f"🤖 وضعیت ربات: {'روشن' if settings.bot_enabled() else 'خاموش'}", callback_data="adm_bot_status"))
    kb.add(InlineKeyboardButton(f"🛒 وضعیت فروش: {'باز' if settings.sales_enabled() else 'بسته'}", callback_data="adm_sales_status"))
    kb.add(InlineKeyboardButton("✏️ پیام خاموش بودن ربات", callback_data="setkey_bot_disabled_message"))
    kb.add(InlineKeyboardButton("✏️ پیام بسته بودن فروش", callback_data="setkey_sales_closed_message"))
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
        "⚙️ تنظیمات کل ربات\n\n"
        "تنظیمات عمومی اینجا می‌ماند. تنظیمات اختصاصی هر پلن از بخش «📦 سرویس‌ها و پلن‌ها» مدیریت می‌شود.",
        reply_markup=settings_menu_kb(),
    )


async def cb_toggle_bot_status(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    new_value = 0 if settings.bot_enabled() else 1
    db.set_setting("bot_enabled", new_value)
    db.log_admin_action(c.from_user.id, "toggle_bot_status", None, f"bot_enabled={new_value}")
    await c.answer("وضعیت ربات تغییر کرد.", show_alert=False)
    await _replace_callback_message(c, "✅ وضعیت ربات به‌روزرسانی شد.", reply_markup=settings_menu_kb())


async def cb_toggle_sales_status(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    new_value = 0 if settings.sales_enabled() else 1
    db.set_setting("sales_enabled", new_value)
    db.log_admin_action(c.from_user.id, "toggle_sales_status", None, f"sales_enabled={new_value}")
    await c.answer("وضعیت فروش تغییر کرد.", show_alert=False)
    await _replace_callback_message(c, "✅ وضعیت فروش به‌روزرسانی شد.", reply_markup=settings_menu_kb())


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
    await c.message.answer(f"مقدار جدید برای «{label}»{hint} رو بفرستید.\nمقدار فعلی:\n{current}", reply_markup=cancel_kb())
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
    db.log_admin_action(m.from_user.id, "update_setting", None, f"key={key}")
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
    "normal": "کاربران عادی",
    "test": "کاربران تست",
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
        InlineKeyboardButton("⬆️ بالاتر", callback_data=f"sysbtn_move_up_{key}"),
        InlineKeyboardButton("⬇️ پایین‌تر", callback_data=f"sysbtn_move_down_{key}"),
    )
    kb.add(
        InlineKeyboardButton("🔢 ترتیب دستی", callback_data=f"sysbtn_order_{key}"),
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
    if m.content_type != "text":
        return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())
    order = parse_int(m.text, allow_negative=True)
    if order is None:
        return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    key = data["system_button_key"]
    db.update_system_button(key, sort_order=order)
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


async def cb_system_button_move(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("sysbtn_move_", 1)[1]
    direction, key = raw.split("_", 1)
    row = db.get_system_button(key)
    if not row:
        return await _replace_callback_message(c, "این دکمه پیدا نشد.", reply_markup=system_buttons_list_kb())
    db.move_record(
        "system_buttons", "key", key, direction,
        where_sql="location=?", where_params=(row["location"],),
    )
    row = db.get_system_button(key)
    await _replace_callback_message(c, "✅ جای دکمه به‌روزرسانی شد.\n\n" + _fmt_system_button(row), reply_markup=system_button_detail_kb(key))


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
        InlineKeyboardButton("🔢 ترتیب دستی", callback_data=f"btn_order_{button_id}"),
    )
    kb.add(
        InlineKeyboardButton("⬆️ بالاتر", callback_data=f"btn_move_up_{button_id}"),
        InlineKeyboardButton("⬇️ پایین‌تر", callback_data=f"btn_move_down_{button_id}"),
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
        "نمایش‌های مجاز: all, buyers, no_buy, has_service, no_service, normal, test, admins\n"
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
    await state.update_data(button_action="create")
    await _replace_callback_message(c, "➕ ساخت دکمه جدید\n\nاول نوع دکمه را انتخاب کنید:", reply_markup=_button_type_select_kb())


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


async def cb_button_move(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await c.answer()
    raw = c.data.split("btn_move_", 1)[1]
    direction, button_id = raw.rsplit("_", 1)
    ok = db.move_custom_button(int(button_id), direction)
    row = db.get_custom_button(button_id)
    if not row:
        return await _replace_callback_message(c, "دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
    message = "✅ ترتیب جدید به‌صورت Draft ذخیره شد؛ برای اعمال، ثبت نهایی کنید." if ok else "این دکمه در این جهت جابه‌جایی دیگری ندارد."
    await _replace_callback_message(c, message + "\n\n" + _fmt_custom_button(row), reply_markup=custom_button_detail_kb(button_id))


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
    if m.content_type != "text":
        return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())
    order = parse_int(m.text, allow_negative=True)
    if order is None:
        return await m.answer("لطفاً فقط عدد بفرستید.", reply_markup=cancel_kb())
    data = await state.get_data()
    row = db.get_custom_button(data["button_id"])
    if not row:
        await state.finish()
        return await m.answer("دکمه پیدا نشد.", reply_markup=custom_buttons_menu_kb())
    current = db.custom_button_effective_data(row)
    current["sort_order"] = order
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
            except Exception as exc:
                logger.debug("could not update broadcast progress: %s", exc)
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
    with tempfile.NamedTemporaryFile(prefix="berserk_restore_", suffix=".db", delete=False) as temp_file:
        tmp_path = temp_file.name
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
    dp.register_callback_query_handler(cb_panel_health, lambda c: c.data == "adm_panel_health")
    dp.register_callback_query_handler(cb_section_finance, lambda c: c.data == "adm_section_finance")
    dp.register_callback_query_handler(cb_section_personalize, lambda c: c.data == "adm_section_personalize")
    dp.register_callback_query_handler(cb_section_reports, lambda c: c.data == "adm_section_reports")
    dp.register_callback_query_handler(cb_fsm_back, lambda c: c.data == "fsm_back", state="*")

    dp.register_callback_query_handler(cb_users, lambda c: c.data == "adm_users")
    dp.register_callback_query_handler(cb_user_profile_info, lambda c: c.data.startswith("adm_user_profile_"))
    dp.register_callback_query_handler(cb_user_history, lambda c: c.data.startswith("adm_user_history_"))
    dp.register_callback_query_handler(cb_user_purchase_detail, lambda c: c.data.startswith("adm_user_purchase_"))
    dp.register_callback_query_handler(cb_user_service_detail, lambda c: c.data.startswith("adm_user_sub_detail_"))
    dp.register_callback_query_handler(cb_user_finance, lambda c: c.data.startswith("adm_user_finance_"))
    dp.register_callback_query_handler(cb_user_referral, lambda c: c.data.startswith("adm_user_referral_"))
    dp.register_callback_query_handler(cb_user_tickets, lambda c: c.data.startswith("adm_user_tickets_"))
    dp.register_callback_query_handler(cb_user_detail, lambda c: c.data.startswith("adm_user_") and not c.data.startswith(("adm_user_note_", "adm_user_test_", "adm_user_profile_", "adm_user_history_", "adm_user_purchase_", "adm_user_sub_detail_", "adm_user_finance_", "adm_user_referral_", "adm_user_tickets_")))
    dp.register_callback_query_handler(cb_user_note, lambda c: c.data.startswith("adm_user_note_"))
    dp.register_message_handler(process_user_note, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_user_note)
    dp.register_callback_query_handler(cb_user_test_toggle, lambda c: c.data.startswith("adm_user_test_"))
    dp.register_callback_query_handler(cb_direct_message_start, lambda c: c.data.startswith("adm_msg_user_"))
    dp.register_message_handler(process_direct_message, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_direct_message)
    dp.register_callback_query_handler(cb_resend_link, lambda c: c.data.startswith("adm_resend_link_"))
    dp.register_callback_query_handler(cb_resend_qr, lambda c: c.data.startswith("adm_resend_qr_"))
    dp.register_callback_query_handler(cb_panel_usage, lambda c: c.data.startswith("adm_panel_usage_"))
    dp.register_callback_query_handler(cb_panel_action_ask, lambda c: c.data.startswith(("adm_panel_reset_ask_", "adm_panel_revoke_ask_", "adm_panel_delete_ask_")))
    dp.register_callback_query_handler(cb_panel_action_confirm, lambda c: c.data.startswith(("adm_panel_reset_confirm_", "adm_panel_revoke_confirm_", "adm_panel_delete_confirm_")))

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

    dp.register_callback_query_handler(cb_categories, lambda c: c.data == "adm_categories")
    dp.register_callback_query_handler(cb_category_create, lambda c: c.data == "category_create")
    dp.register_callback_query_handler(cb_category_detail, lambda c: c.data.startswith("category_detail_"))
    dp.register_callback_query_handler(cb_category_set, lambda c: c.data.startswith("category_set_"))
    dp.register_callback_query_handler(cb_category_audience, lambda c: c.data.startswith("category_audience_"))
    dp.register_callback_query_handler(cb_category_toggle, lambda c: c.data.startswith("category_toggle_"))
    dp.register_callback_query_handler(cb_category_move, lambda c: c.data.startswith("category_move_"))
    dp.register_callback_query_handler(cb_category_delete, lambda c: c.data.startswith("category_delete_"))
    dp.register_message_handler(process_category_form, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_category_form)
    dp.register_message_handler(process_category_setting, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_category_setting)
    dp.register_callback_query_handler(cb_providers, lambda c: c.data == "adm_providers")
    dp.register_callback_query_handler(cb_provider_health, lambda c: c.data.startswith("adm_provider_health_"))
    dp.register_callback_query_handler(cb_provider_detail, lambda c: c.data.startswith("adm_provider_"))
    dp.register_callback_query_handler(cb_trials, lambda c: c.data == "adm_trials")
    dp.register_callback_query_handler(cb_trial_detail, lambda c: c.data.startswith("adm_trial_detail_"))
    dp.register_callback_query_handler(cb_admin_layout, lambda c: c.data == "adm_menu_layout")
    dp.register_callback_query_handler(cb_admin_layout_action, lambda c: c.data.startswith("adm_layout_"))

    dp.register_callback_query_handler(cb_plans, lambda c: c.data == "adm_plans")
    dp.register_callback_query_handler(cb_plan_create, lambda c: c.data == "plan_create")
    dp.register_callback_query_handler(cb_plan_detail, lambda c: c.data.startswith("plan_detail_"))
    dp.register_callback_query_handler(cb_plan_edit, lambda c: c.data.startswith("plan_edit_"))
    dp.register_callback_query_handler(cb_plan_settings, lambda c: c.data.startswith("plan_settings_"))
    dp.register_callback_query_handler(cb_plan_category, lambda c: c.data.startswith("plan_category_"))
    dp.register_callback_query_handler(cb_plan_set_category, lambda c: c.data.startswith("plan_set_category_"))
    dp.register_callback_query_handler(cb_plan_purchase_mode, lambda c: c.data.startswith("plan_purchase_mode_"))
    dp.register_callback_query_handler(cb_plan_set_mode, lambda c: c.data.startswith("plan_set_mode_"))
    dp.register_callback_query_handler(cb_plan_provider, lambda c: c.data.startswith("plan_provider_"))
    dp.register_callback_query_handler(cb_plan_set_provider, lambda c: c.data.startswith("plan_set_provider_"))
    dp.register_callback_query_handler(cb_plan_move, lambda c: c.data.startswith("plan_move_"))
    dp.register_callback_query_handler(cb_plan_set_field, lambda c: c.data.startswith("plan_set_") and not c.data.startswith(("plan_set_category_", "plan_set_mode_", "plan_set_provider_")))
    dp.register_callback_query_handler(cb_plan_toggle_stock, lambda c: c.data.startswith("plan_toggle_stock_"))
    dp.register_callback_query_handler(cb_plan_toggle_delivery, lambda c: c.data.startswith("plan_toggle_delivery_"))
    dp.register_callback_query_handler(cb_plan_toggle_start, lambda c: c.data.startswith("plan_toggle_start_"))
    dp.register_callback_query_handler(cb_plan_toggle, lambda c: c.data.startswith("plan_toggle_"))
    dp.register_callback_query_handler(cb_plan_wizard_category, lambda c: c.data.startswith("plan_wizard_category_"), state=AdminStates.waiting_plan_form)
    dp.register_callback_query_handler(cb_plan_wizard_mode, lambda c: c.data.startswith("plan_wizard_mode_"), state=AdminStates.waiting_plan_form)
    dp.register_callback_query_handler(cb_plan_wizard_provider, lambda c: c.data.startswith("plan_wizard_provider_"), state=AdminStates.waiting_plan_form)
    dp.register_callback_query_handler(cb_plan_wizard_delivery, lambda c: c.data.startswith("plan_wizard_delivery_"), state=AdminStates.waiting_plan_form)
    dp.register_callback_query_handler(cb_plan_wizard_devices, lambda c: c.data.startswith("plan_wizard_devices_"), state=AdminStates.waiting_plan_form)
    dp.register_callback_query_handler(cb_plan_wizard_start, lambda c: c.data.startswith("plan_wizard_start_"), state=AdminStates.waiting_plan_form)
    dp.register_callback_query_handler(cb_plan_wizard_save, lambda c: c.data == "plan_wizard_save", state=AdminStates.waiting_plan_form)
    dp.register_message_handler(process_plan_form, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_plan_form)
    dp.register_message_handler(process_plan_setting_value, content_types=types.ContentTypes.ANY, state=AdminStates.waiting_plan_setting_value)

    dp.register_callback_query_handler(cb_settings, lambda c: c.data == "adm_settings")
    dp.register_callback_query_handler(cb_toggle_bot_status, lambda c: c.data == "adm_bot_status")
    dp.register_callback_query_handler(cb_toggle_sales_status, lambda c: c.data == "adm_sales_status")
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
    dp.register_callback_query_handler(cb_system_button_move, lambda c: c.data.startswith("sysbtn_move_"))
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
    dp.register_callback_query_handler(cb_button_move, lambda c: c.data.startswith("btn_move_"))
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
