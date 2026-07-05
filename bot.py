import asyncio
import logging
import traceback

from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.utils import executor

import admin
import backup
import db
import menus
import messages
import settings
import subs
import tickets
import wallet
from affiliate import reward_ref
from config import ADMIN_COMMAND, ADMIN_IDS, BOT_TOKEN, validate
from fsm_storage import SQLiteStorage
from utils import cleanup_qr, make_qr, format_dual_datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

validate()

if ADMIN_COMMAND == "panel_secret":
    logger.warning(
        "ADMIN_COMMAND هنوز مقدار پیش‌فرضه! حتماً توی Railway یه اسم اختصاصی "
        "براش ست کنید، مثلاً panel_x7k9."
    )

bot = Bot(token=BOT_TOKEN)

db.init()
settings.ensure_defaults()

dp = Dispatcher(bot, storage=SQLiteStorage())


def wallet_menu_kb(include_bulk=False):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="topup_start"))
    if include_bulk:
        kb.add(types.InlineKeyboardButton("📦 خرید عمده", callback_data="buy_bulk"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def plans_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for plan in db.list_plans(active_only=True):
        stock = subs.stock_count(plan["id"])
        label = f"{plan['title']} | {int(plan['price']):,} تومان"
        if int(plan["show_stock"] or 0):
            label += f" | موجودی {stock}"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"buy_plan_{plan['id']}"))
    kb.add(types.InlineKeyboardButton("📦 خرید عمده", callback_data="buy_bulk"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def buy_quantity_kb(max_qty: int, plan_id=None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    plan_suffix = f"_{int(plan_id)}" if plan_id is not None else ""

    # گزینه‌های ۱ تا ۴ حتی اگر موجودی کیف پول کافی نباشد نمایش داده می‌شوند؛
    # بررسی پرداخت در مرحله بعد انجام می‌شود.
    for qty in range(1, 5):
        if qty <= max_qty:
            kb.insert(types.InlineKeyboardButton(f"{qty} عدد", callback_data=f"buy_qty_{qty}{plan_suffix}"))

    kb.add(types.InlineKeyboardButton("📦 خرید عمده", callback_data="buy_bulk"))
    kb.add(types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="topup_start"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


async def send_main_menu(target, user_id: int):
    await target.answer(
        "⚡ Berserk VPN Ready\n\n"
        "از منوی پایین تلگرام استفاده کنید؛ لازم نیست هر بار /start بزنید.",
        reply_markup=menus.main_reply_kb(user_id),
    )

async def _safe_delete_callback_message(c: types.CallbackQuery):
    try:
        await c.message.delete()
        return True
    except Exception:
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return False


async def _track_sent(user_id, sent, context=""):
    if sent is None:
        return
    if not isinstance(sent, (list, tuple)):
        sent = [sent]
    for msg in sent:
        try:
            db.track_bot_message(msg.chat.id, user_id, msg.message_id, context)
        except Exception:
            pass


async def _cleanup_user_messages(chat_id, user_id):
    rows = db.list_tracked_bot_messages(chat_id, user_id, limit=40)
    for row in rows:
        try:
            await bot.delete_message(int(row["chat_id"]), int(row["message_id"]))
        except Exception:
            try:
                await bot.edit_message_reply_markup(int(row["chat_id"]), int(row["message_id"]), reply_markup=None)
            except Exception:
                pass
        finally:
            db.clear_tracked_bot_message(row["chat_id"], row["message_id"])


async def _start_clean_section(target, user_id, context=""):
    try:
        chat_id = target.chat.id
    except Exception:
        try:
            chat_id = target.message.chat.id
        except Exception:
            return
    await _cleanup_user_messages(chat_id, user_id)


async def _send_template(target, user_id, key, body_text, reply_markup=None, context=""):
    sent = await messages.send(target, key, body_text, reply_markup=reply_markup)
    await _track_sent(user_id, sent, context or key)
    return sent


async def _send_answer(target, user_id, text, reply_markup=None, context=""):
    sent = await target.answer(text, reply_markup=reply_markup)
    await _track_sent(user_id, sent, context)
    return sent


async def render_buy(target, user_id: int, username: str = "", plan_id=None):
    await _start_clean_section(target, user_id, "buy")
    user_id_str = str(user_id)
    user = db.get_user(user_id_str)

    if user and user["banned"]:
        return await _send_answer(target, user_id, "⛔ حساب شما مسدود است.", context="buy")

    if not _is_admin_user(user_id) and not settings.sales_enabled():
        return await _send_template(target, user_id, "menu_buy", settings.sales_closed_message(), reply_markup=menus.main_reply_kb(user_id), context="buy_closed")

    db.touch_active(user_id_str, username, getattr(target.from_user, "full_name", None) if hasattr(target, "from_user") else None)

    if user is None:
        user, _ = db.get_or_create_user(user_id_str, username)

    active_plans = db.list_plans(active_only=True)
    if plan_id is None and len(active_plans) > 1:
        lines = ["🛒 خرید سرویس", "", "لطفاً پلن موردنظر را انتخاب کنید:", ""]
        for idx, plan in enumerate(active_plans, start=1):
            stock = subs.stock_count(plan["id"])
            extra = f" | {plan['volume_label']}" if plan["volume_label"] else ""
            duration = f" | {plan['duration_label']}" if plan["duration_label"] else ""
            stock_text = f" | موجودی: {stock}" if int(plan["show_stock"] or 0) else ""
            tag = f" | {plan['tag']}" if plan["tag"] else ""
            lines.append(f"{idx}. {plan['title']}{extra}{duration}{tag}\nقیمت: {int(plan['price']):,} تومان{stock_text}\n")
        return await _send_template(target, user_id, "menu_buy", "\n".join(lines), reply_markup=plans_kb(), context="buy")

    plan = db.get_plan(plan_id) if plan_id is not None else (active_plans[0] if active_plans else db.get_plan())
    if not plan:
        return await _send_answer(target, user_id, "❌ پلنی برای فروش تنظیم نشده است.", reply_markup=menus.main_reply_kb(user_id), context="buy")

    plan_id = int(plan["id"])
    price = int(plan["price"])
    title = plan["title"]
    duration = plan["duration_label"] or settings.plan_duration_label()
    balance = int(user["balance"] or 0) if user else 0
    stock = subs.stock_count(plan_id)
    max_per_order = max(1, min(4, int(plan["max_per_order"] or 4)))
    max_qty = min(max_per_order, 4, stock) if stock > 0 else 0

    text = (
        f"🛒 خرید سرویس\n\n"
        f"پلن: {title}\n"
        f"حجم: {plan['volume_label'] or '-'}\n"
        f"⏳ مدت: {duration}\n"
        f"قیمت هر عدد: {price:,} تومان\n"
        f"موجودی کیف پول شما: {balance:,} تومان\n"
        f"موجودی سرویس: {stock}\n\n"
    )

    pre_purchase_text = (plan["pre_purchase_text"] if "pre_purchase_text" in plan.keys() else "") or ""
    if pre_purchase_text.strip():
        text += pre_purchase_text.strip() + "\n\n"

    if stock <= 0:
        text += (
            "❌ در حال حاضر موجودی آماده برای این پلن نداریم.\n"
            "اگر تعداد بالا می‌خواهید یا هماهنگی دستی لازم دارید، خرید عمده را بزنید."
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        if len(active_plans) > 1:
            kb.add(types.InlineKeyboardButton("⬅️ انتخاب پلن دیگر", callback_data="buy"))
        kb.add(types.InlineKeyboardButton("📦 خرید عمده", callback_data="buy_bulk"))
        kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
        return await _send_template(target, user_id, "menu_buy", text, reply_markup=kb, context="buy")

    text += (
        "تعداد مورد نظر را انتخاب کنید.\n"
        "اگر موجودی کیف پول کافی نباشد، مستقیم به پرداخت همان تعداد هدایت می‌شوید."
    )
    return await _send_template(target, user_id, "menu_buy", text, reply_markup=buy_quantity_kb(max_qty, plan_id), context="buy")

async def check_low_stock_alert(plan_id=None):
    """هشدار موجودی کم برای هر پلن، بدون ارسال تکراری تا وقتی موجودی دوباره بالا برود."""
    plans = []
    if plan_id is not None:
        plan = db.get_plan(plan_id)
        if plan:
            plans = [plan]
    else:
        plans = db.list_plans(active_only=True, limit=50)

    for plan in plans:
        threshold = int(plan["low_stock_threshold"] or settings.low_stock_threshold())
        current_stock = subs.stock_count(plan["id"])
        if current_stock > threshold:
            db.set_plan_low_stock_alerted(plan["id"], False)
            continue
        if db.is_plan_low_stock_alerted(plan["id"]):
            continue
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ موجودی پلن «{plan['title']}» کم شده است.\n"
                    f"موجودی فعلی: {current_stock} لینک\n"
                    f"حد هشدار: {threshold} لینک\n"
                    "لطفاً برای این پلن لینک جدید وارد کنید.",
                )
            except Exception:
                pass
        db.set_plan_low_stock_alerted(plan["id"], True)


def _is_admin_user(user_id) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False


async def _send_bot_disabled(target, user_id):
    return await target.answer(settings.bot_disabled_message(), reply_markup=menus.main_reply_kb(user_id))


async def _send_sales_closed(target, user_id):
    return await target.answer(settings.sales_closed_message(), reply_markup=menus.main_reply_kb(user_id))


@dp.message_handler(lambda m: not _is_admin_user(m.from_user.id) and not settings.bot_enabled(), content_types=types.ContentTypes.ANY, state="*")
async def bot_disabled_message_handler(m: types.Message):
    await _send_bot_disabled(m, m.from_user.id)


@dp.callback_query_handler(lambda c: not _is_admin_user(c.from_user.id) and not settings.bot_enabled(), state="*")
async def bot_disabled_callback_handler(c: types.CallbackQuery):
    await c.answer()
    await _send_bot_disabled(c.message, c.from_user.id)


@dp.message_handler(commands=["start"])
async def start(m: types.Message):
    user_id = str(m.from_user.id)
    ref = None
    args = m.get_args()

    if args and args.isdigit() and args != user_id:
        ref = args

    row, _ = db.get_or_create_user(user_id, m.from_user.username, ref, m.from_user.full_name)
    db.touch_active(user_id, m.from_user.username, m.from_user.full_name)

    if row["banned"]:
        return await m.answer("⛔ حساب شما مسدود شده.\nبرای پیگیری با پشتیبانی تماس بگیرید.")

    await messages.send(
        m,
        "welcome",
        "⚡ Berserk VPN Ready\n\nمنوی اصلی پایین صفحه همیشه در دسترس شماست.",
        reply_markup=menus.main_reply_kb(m.from_user.id),
    )


@dp.message_handler(lambda m: menus.matches_system_button(m.text, "buy"))
async def text_buy(m: types.Message):
    await render_buy(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: menus.matches_system_button(m.text, "my_subs"))
async def text_my_subs(m: types.Message):
    await show_my_subs(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: menus.matches_system_button(m.text, "wallet"))
async def text_wallet(m: types.Message):
    await show_wallet(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: menus.matches_system_button(m.text, "guide"))
async def text_guide(m: types.Message):
    await show_guide_menu(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: menus.matches_system_button(m.text, "referral"))
async def text_referral(m: types.Message):
    await show_referral(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: menus.matches_system_button(m.text, "ticket"))
async def text_ticket(m: types.Message):
    await messages.send(
        m,
        "support_intro",
        "برای ارسال پیام به پشتیبانی، روی دکمه زیر بزنید:",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🎫 ارسال پیام پشتیبانی", callback_data="ticket_start")
        ),
    )


@dp.message_handler(lambda m: menus.matches_system_button(m.text, "admin"))
async def text_admin(m: types.Message):
    if not admin.is_admin(m.from_user.id):
        return
    await m.answer("⚙️ پنل مدیریت Berserk VPN", reply_markup=admin.admin_menu_kb())


@dp.message_handler(commands=["cancel"], state="*")
async def cmd_cancel(m: types.Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        return await m.answer("چیزی برای لغو کردن نیست.", reply_markup=menus.main_reply_kb(m.from_user.id))
    await state.finish()
    await m.answer("❌ لغو شد.", reply_markup=menus.main_reply_kb(m.from_user.id))


@dp.callback_query_handler(lambda c: c.data == "cancel_fsm", state="*")
async def cb_cancel_fsm(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    current = await state.get_state()

    if current is not None:
        await state.finish()

    # پیام inline قبلی حذف می‌شود تا دکمه‌های قدیمی قابل اسپم نباشند.
    await _safe_delete_callback_message(c)

    await bot.send_message(
        c.message.chat.id,
        "❌ لغو شد.",
        reply_markup=menus.main_reply_kb(c.from_user.id),
    )


@dp.callback_query_handler(lambda c: c.data == "back_main")
async def back_main(c: types.CallbackQuery):
    await c.answer()

    # پیام inline قبلی حذف می‌شود تا دکمه‌های قدیمی قابل اسپم نباشند.
    await _safe_delete_callback_message(c)

    await bot.send_message(
        c.message.chat.id,
        "⚡ Berserk VPN Ready\n\nاز منوی پایین تلگرام استفاده کنید؛ لازم نیست هر بار /start بزنید.",
        reply_markup=menus.main_reply_kb(c.from_user.id),
    )


@dp.callback_query_handler(lambda c: c.data == "buy")
async def buy(c: types.CallbackQuery):
    await c.answer()
    await render_buy(c.message, c.from_user.id, c.from_user.username or "")


@dp.callback_query_handler(lambda c: c.data.startswith("buy_plan_"))
async def buy_plan(c: types.CallbackQuery):
    await c.answer()
    try:
        plan_id = int(c.data.split("buy_plan_", 1)[1])
    except Exception:
        plan_id = None
    await render_buy(c.message, c.from_user.id, c.from_user.username or "", plan_id=plan_id)


@dp.callback_query_handler(lambda c: c.data.startswith("buy_qty_"))
async def buy_qty(c: types.CallbackQuery, state: FSMContext):
    user_id = str(c.from_user.id)
    user = db.get_user(user_id)

    if user and user["banned"]:
        return await c.answer("⛔ حساب شما مسدود است.", show_alert=True)

    if not _is_admin_user(c.from_user.id) and not settings.sales_enabled():
        await c.answer()
        return await _send_sales_closed(c.message, c.from_user.id)

    await c.answer()

    try:
        parts = c.data.split("_")
        qty = int(parts[2])
        plan_id = int(parts[3]) if len(parts) > 3 else db.default_plan_id()
    except ValueError:
        return await c.message.answer("درخواست خرید نامعتبر است.", reply_markup=menus.main_reply_kb(c.from_user.id))

    if qty < 1 or qty > 4:
        return await c.message.answer("تعداد انتخاب‌شده معتبر نیست.", reply_markup=menus.main_reply_kb(c.from_user.id))

    if user is None:
        user, _ = db.get_or_create_user(user_id, c.from_user.username, display_name=c.from_user.full_name)

    plan = db.get_plan(plan_id)
    if not plan or int(plan["is_active"] or 0) != 1:
        return await c.message.answer("این پلن فعال نیست یا پیدا نشد.", reply_markup=menus.main_reply_kb(c.from_user.id))

    was_first_purchase = int(user["purchased"] or 0) == 0
    price = int(plan["price"])
    total = qty * price
    balance = int(user["balance"] or 0)
    missing = max(0, total - balance)

    if missing > 0:
        topup_id = db.create_topup(
            user_id,
            missing,
            target_quantity=qty,
            target_plan_id=plan_id,
            target_total=total,
            target_unit_price=price,
        )
        await state.update_data(topup_id=topup_id)
        await wallet.TopupStates.waiting_receipt.set()
        text = (
            f"💳 پرداخت خرید {qty} سرویس\n\n"
            f"پلن: {plan['title']}\n"
            f"قیمت کل: {total:,} تومان\n"
            f"موجودی فعلی کیف پول: {balance:,} تومان\n"
            f"مبلغ قابل پرداخت برای تکمیل خرید: {missing:,} تومان\n\n"
            f"شماره کارت:\n`{settings.card_number()}`\n"
            f"به نام: {settings.card_holder()}\n\n"
            "بعد از واریز، عکس رسید پرداخت را همینجا ارسال کنید.\n"
            "بعد از تأیید ادمین، ربات تلاش می‌کند همین خرید را خودکار تکمیل کند."
        )
        sent = await c.message.answer(text, parse_mode="Markdown", reply_markup=wallet.cancel_kb())
        await _track_sent(c.from_user.id, sent, "targeted_topup")
        return

    try:
        result = db.complete_purchase(user_id, qty, price, plan_id=plan_id)
    except db.PurchaseError as exc:
        if exc.code == "insufficient_balance":
            return await c.message.answer(exc.message, reply_markup=wallet_menu_kb(include_bulk=True))
        return await c.message.answer(exc.message, reply_markup=menus.main_reply_kb(c.from_user.id))

    if was_first_purchase:
        status, detail = reward_ref(user_id)

        if status == "rewarded":
            try:
                await bot.send_message(
                    int(detail),
                    "💰 یکی از زیرمجموعه‌های شما اولین خرید واقعی خود را انجام داد. پاداش رفرال به کیف پول شما اضافه شد.",
                )
            except Exception:
                logger.warning("could not notify referrer %s", detail)

        elif status == "blocked":
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, detail)
                except Exception:
                    pass

    await check_low_stock_alert(plan_id)

    post_purchase_text = (plan["post_purchase_text"] if "post_purchase_text" in plan.keys() else "") or ""
    success_text = (
        f"✅ خرید موفق!\n"
        f"شماره خرید: #{result['purchase_id']}\n"
        f"پلن: {plan['title']}\n"
        f"تعداد تحویل‌شده: {len(result['items'])} عدد\n"
        f"مبلغ کسرشده: {result['amount']:,} تومان\n"
        f"موجودی جدید: {result['balance_after']:,} تومان"
    )
    if post_purchase_text.strip():
        success_text += "\n\n" + post_purchase_text.strip()
    sent = await c.message.answer(
        success_text,
        reply_markup=menus.main_reply_kb(c.from_user.id),
    )
    await _track_sent(c.from_user.id, sent, "purchase_result")

    for index, item in enumerate(result["items"], start=1):
        qr_path = make_qr(item["link"], user_id)
        try:
            with open(qr_path, "rb") as f:
                sent_photo = await c.message.answer_photo(
                    f,
                    caption=(
                        f"✅ سرویس #{index}\n"
                        f"شناسه سرویس: {item['account_name']}\n\n"
                        f"لینک سرویس:\n{item['link']}"
                    ),
                )
                await _track_sent(c.from_user.id, sent_photo, "purchase_link")
        finally:
            cleanup_qr(qr_path)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🛒 خرید جدید\n"
                f"کاربر: {c.from_user.full_name} (@{c.from_user.username or '-'})\n"
                f"ID: {user_id}\n"
                f"شماره خرید: #{result['purchase_id']}\n"
                f"پلن: {plan['title']}\n"
                f"تعداد: {len(result['items'])}\n"
                f"مبلغ: {result['amount']:,} تومان",
            )
        except Exception:
            pass

@dp.callback_query_handler(lambda c: c.data == "confirm_buy")
async def confirm_buy(c: types.CallbackQuery):
    await c.answer()
    await render_buy(c.message, c.from_user.id, c.from_user.username or "", plan_id=db.default_plan_id())


@dp.callback_query_handler(lambda c: c.data == "buy_bulk")
async def buy_bulk(c: types.CallbackQuery):
    await c.answer()
    if not _is_admin_user(c.from_user.id) and not settings.sales_enabled():
        return await _send_sales_closed(c.message, c.from_user.id)
    user_id = str(c.from_user.id)
    db.touch_active(user_id, c.from_user.username)
    ticket_id = db.create_ticket(user_id)

    text = (
        f"📦 درخواست خرید عمده #{ticket_id}\n\n"
        f"کاربر: {c.from_user.full_name}\n"
        f"یوزرنیم: @{c.from_user.username or '-'}\n"
        f"User ID: {user_id}\n\n"
        "لطفاً با کاربر صحبت کنید و تعداد/شرایط خرید عمده را دستی هماهنگ کنید."
    )

    for admin_id in ADMIN_IDS:
        try:
            sent = await bot.send_message(admin_id, text)
            db.record_ticket_message(admin_id, sent.message_id, ticket_id, user_id)
        except Exception:
            pass

    await c.message.answer(
        "✅ درخواست خرید عمده برای مدیریت ارسال شد.\n"
        "به‌زودی برای هماهنگی با شما تماس گرفته می‌شود.",
        reply_markup=menus.main_reply_kb(c.from_user.id),
    )


async def show_my_subs(target, user_id: int, username: str = ""):
    await _start_clean_section(target, user_id, "my_subs")
    user_id_str = str(user_id)
    db.touch_active(user_id_str, username, getattr(target.from_user, "full_name", None) if hasattr(target, "from_user") else None)
    rows = subs.user_subs(user_id_str)

    if not rows:
        return await _send_template(
            target,
            user_id,
            "my_services_empty",
            "هنوز هیچ سرویسی خریداری نکردید.",
            reply_markup=menus.main_reply_kb(user_id),
            context="my_subs",
        )

    lines = ["📦 سرویس‌های شما:\n"]

    for index, r in enumerate(rows, start=1):
        plan = db.get_plan(r["plan_id"]) if "plan_id" in r.keys() and r["plan_id"] else None
        plan_title = plan["title"] if plan else "سرویس"
        lines.append(
            f"{index}️⃣ {plan_title}\n"
            f"شناسه سرویس: {r['account_name'] or '-'}\n"
            f"تاریخ خرید: {format_dual_datetime(r['assigned_at'])}\n"
            f"لینک:\n{r['link']}\n"
        )

    await _send_answer(target, user_id, "\n".join(lines), reply_markup=menus.main_reply_kb(user_id), context="my_subs")

@dp.callback_query_handler(lambda c: c.data == "my_subs")
async def my_subs(c: types.CallbackQuery):
    await c.answer()
    await show_my_subs(c.message, c.from_user.id, c.from_user.username or "")




def guide_menu_kb(user_id=None):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📱 آموزش اندروید", callback_data="guide_android"))
    kb.add(types.InlineKeyboardButton("🍎 آموزش آیفون", callback_data="guide_ios"))
    kb.add(types.InlineKeyboardButton("💻 آموزش ویندوز", callback_data="guide_windows"))
    kb.add(types.InlineKeyboardButton("🖥 آموزش مک", callback_data="guide_mac"))
    kb.add(types.InlineKeyboardButton("❓ مشکل اتصال دارم", callback_data="guide_troubleshoot"))
    kb.add(types.InlineKeyboardButton("🔄 بروزرسانی ساب‌لینک", callback_data="guide_update"))

    callback_map = {
        "buy": "buy",
        "my_subs": "my_subs",
        "wallet": "wallet",
        "referral": "referral",
        "ticket": "ticket_start",
        "guide": "guide_home",
    }
    for key, title in menus.system_buttons_for_location("guide", user_id):
        cb = callback_map.get(key)
        if cb and key != "admin":
            kb.add(types.InlineKeyboardButton(title, callback_data=cb))

    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb

_GUIDE_DEFAULTS = {
    "guide_home": "📚 آموزش اتصال\n\nدستگاه خود را انتخاب کنید:",
    "guide_android": "📱 آموزش اندروید\n\n۱. یک برنامه سازگار با ساب‌لینک نصب کنید.\n۲. لینک سرویس را از بخش سرویس‌های من کپی کنید.\n۳. داخل برنامه گزینه Import/Subscription را بزنید.\n۴. لینک را وارد و بروزرسانی کنید.",
    "guide_ios": "🍎 آموزش آیفون\n\n۱. یک کلاینت سازگار نصب کنید.\n۲. ساب‌لینک را کپی کنید.\n۳. از بخش Subscription یا Import، لینک را اضافه کنید.\n۴. اتصال را تست کنید.",
    "guide_windows": "💻 آموزش ویندوز\n\n۱. برنامه مناسب ویندوز را نصب کنید.\n۲. لینک سرویس را از بخش سرویس‌های من کپی کنید.\n۳. از بخش Subscription لینک را اضافه کنید.\n۴. Update subscription را بزنید.",
    "guide_mac": "🖥 آموزش مک\n\n۱. کلاینت سازگار با مک را نصب کنید.\n۲. لینک سرویس را اضافه کنید.\n۳. ساب‌لینک را بروزرسانی و اتصال را فعال کنید.",
    "guide_troubleshoot": "❓ مشکل اتصال دارم\n\nاول اینترنت اصلی را بررسی کنید، سپس ساب‌لینک را بروزرسانی کنید. اگر مشکل ادامه داشت، از بخش پشتیبانی پیام بدهید.",
    "guide_update": "🔄 بروزرسانی ساب‌لینک\n\nدر برنامه خود گزینه Update/Refresh Subscription را بزنید تا لیست سرورها تازه شود.",
}


async def show_guide_menu(target, user_id: int, username: str = ""):
    db.touch_active(str(user_id), username)
    await _start_clean_section(target, user_id, "guide")
    await _send_template(
        target,
        user_id,
        "guide_home",
        _GUIDE_DEFAULTS["guide_home"],
        reply_markup=guide_menu_kb(user_id),
        context="guide",
    )


async def show_guide_page(target, key: str, user_id: int):
    await _send_template(
        target,
        user_id,
        key,
        _GUIDE_DEFAULTS.get(key, "📚 آموزش اتصال"),
        reply_markup=guide_menu_kb(user_id),
        context="guide_page",
    )


@dp.callback_query_handler(lambda c: c.data == "guide_home")
async def cb_guide_home(c: types.CallbackQuery):
    await c.answer()
    await show_guide_menu(c.message, c.from_user.id, c.from_user.username or "")


@dp.callback_query_handler(lambda c: c.data.startswith("guide_") and c.data != "guide_home")
async def cb_guide_page(c: types.CallbackQuery):
    await c.answer()
    key = c.data
    await show_guide_page(c.message, key, c.from_user.id)


def _custom_button_allowed(row, user_id):
    audience = row["audience"] or "all"
    if audience == "all":
        return True
    if audience == "admins":
        return menus.is_admin_user(user_id)

    try:
        user = db.get_user(str(user_id))
        purchased = int(user["purchased"] or 0) if user else 0
        service_count = db.delivered_sub_count_by_user(str(user_id))
    except Exception:
        return False

    if audience == "buyers":
        return purchased > 0
    if audience == "no_buy":
        return purchased == 0
    if audience == "has_service":
        return service_count > 0
    if audience == "no_service":
        return service_count == 0
    return False


async def render_custom_button(target, row, user_id: int, username: str = ""):
    db.touch_active(str(user_id), username)
    if not _custom_button_allowed(row, user_id):
        return await target.answer("این دکمه برای حساب شما فعال نیست.", reply_markup=menus.main_reply_kb(user_id))

    button_type = row["button_type"] or "text"
    payload = row["payload"] or ""
    title = row["title"] or "دکمه اختصاصی"

    if button_type == "link":
        if not payload.startswith(("http://", "https://", "tg://")):
            return await target.answer("لینک این دکمه معتبر نیست. لطفاً به پشتیبانی اطلاع دهید.", reply_markup=menus.main_reply_kb(user_id))
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(title, url=payload))
        kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
        return await target.answer(f"برای باز کردن «{title}» روی دکمه زیر بزنید:", reply_markup=kb)

    if button_type == "support":
        return await messages.send(
            target,
            "support_intro",
            payload or "برای ارسال پیام به پشتیبانی، روی دکمه زیر بزنید:",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🎫 ارسال پیام پشتیبانی", callback_data="ticket_start")
            ),
        )

    if button_type == "buy_plan":
        plan_id = None
        if str(payload).strip().isdigit():
            plan_id = int(str(payload).strip())
        return await render_buy(target, user_id, username, plan_id=plan_id)

    if button_type == "file":
        if payload:
            try:
                return await target.answer_document(payload, caption=title, reply_markup=menus.main_reply_kb(user_id))
            except Exception:
                pass
        return await target.answer("فایل این دکمه در دسترس نیست.", reply_markup=menus.main_reply_kb(user_id))

    # text / faq / guide / submenu در نسخه ربات به صورت پیام امن نمایش داده می‌شوند.
    await target.answer(payload or title, reply_markup=menus.main_reply_kb(user_id))


@dp.message_handler(lambda m: db.get_active_custom_button_by_title(m.text or "") is not None)
async def text_custom_button(m: types.Message):
    row = db.get_active_custom_button_by_title(m.text or "")
    await render_custom_button(m, row, m.from_user.id, m.from_user.username or "")

async def show_wallet(target, user_id: int, username: str = ""):
    user_id_str = str(user_id)
    db.touch_active(user_id_str, username, getattr(target.from_user, "full_name", None) if hasattr(target, "from_user") else None)
    user = db.get_user(user_id_str)

    if user is None:
        user, _ = db.get_or_create_user(user_id_str, username)

    bal = user["balance"] if user else 0
    purchased = user["purchased"] if user else 0

    await messages.send(
        target,
        "menu_wallet",
        f"💳 موجودی: {bal:,} تومان\n📦 تعداد خرید: {purchased}",
        reply_markup=wallet_menu_kb(),
    )


@dp.callback_query_handler(lambda c: c.data == "wallet")
async def wallet_menu(c: types.CallbackQuery):
    await c.answer()
    await show_wallet(c.message, c.from_user.id, c.from_user.username or "")


async def show_referral(target, user_id: int, username: str = ""):
    user_id_str = str(user_id)
    bot_user = (await bot.get_me()).username
    link = f"https://t.me/{bot_user}?start={user_id_str}"
    count = db.referral_count(user_id_str)
    reward = settings.ref_reward()

    await messages.send(
        target,
        "menu_referral",
        f"👥 لینک دعوت اختصاصی شما:\n{link}\n\n"
        f"تعداد زیرمجموعه: {count} نفر\n"
        f"پاداش هر اولین خرید واقعی زیرمجموعه: {reward:,} تومان\n\n"
        "پاداش فقط بعد از اولین خرید واقعی زیرمجموعه پرداخت می‌شود.",
        reply_markup=menus.main_reply_kb(user_id),
    )


@dp.callback_query_handler(lambda c: c.data == "referral")
async def referral(c: types.CallbackQuery):
    await c.answer()
    await show_referral(c.message, c.from_user.id, c.from_user.username or "")


wallet.register(dp)
tickets.register(dp)
admin.register(dp)


@dp.errors_handler()
async def global_error_handler(update: types.Update, exception: Exception):
    logger.exception("خطای پیش‌بینی‌نشده: %s", exception)

    try:
        if update.message:
            await update.message.answer(
                "⚠️ مشکلی پیش اومد. لطفاً دوباره تلاش کنید یا /cancel رو بزنید.",
                reply_markup=menus.main_reply_kb(update.message.from_user.id),
            )
        elif update.callback_query:
            await update.callback_query.message.answer(
                "⚠️ مشکلی پیش اومد. لطفاً دوباره تلاش کنید یا /cancel رو بزنید.",
                reply_markup=menus.main_reply_kb(update.callback_query.from_user.id),
            )
    except Exception:
        pass

    tb = traceback.format_exc()[-1500:]
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"🚨 خطای پیش‌بینی‌نشده:\n{tb}")
        except Exception:
            pass
    return True


@dp.message_handler(content_types=types.ContentTypes.ANY, state="*")
async def fallback_message(m: types.Message, state: FSMContext):
    await m.answer(
        "متوجه نشدم. از منوی پایین تلگرام استفاده کنید یا /start رو بزنید.",
        reply_markup=menus.main_reply_kb(m.from_user.id),
    )


@dp.callback_query_handler(lambda c: True, state="*")
async def fallback_callback(c: types.CallbackQuery):
    await c.answer("این دکمه دیگه معتبر نیست.", show_alert=True)


async def on_startup(dispatcher):
    asyncio.create_task(backup.daily_backup_loop(bot, ADMIN_IDS))
    logger.info("Berserk VPN bot started, daily backup loop scheduled.")


if __name__ == "__main__":
    logger.info("Berserk VPN bot starting...")
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
