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
from utils import cleanup_qr, make_qr

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


def buy_quantity_kb(max_qty: int):
    kb = types.InlineKeyboardMarkup(row_width=2)

    for qty in range(1, 5):
        if qty <= max_qty:
            kb.insert(types.InlineKeyboardButton(f"{qty} عدد", callback_data=f"buy_qty_{qty}"))

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


async def render_buy(target, user_id: int, username: str = ""):
    user_id_str = str(user_id)
    user = db.get_user(user_id_str)

    if user and user["banned"]:
        return await target.answer("⛔ حساب شما مسدود است.")

    db.touch_active(user_id_str, username)

    if user is None:
        user, _ = db.get_or_create_user(user_id_str, username)

    price = settings.plan_price()
    title = settings.plan_title()
    duration = settings.plan_duration_label()
    balance = int(user["balance"] or 0) if user else 0
    stock = subs.stock_count()
    affordable_qty = balance // price if price > 0 else 0
    max_qty = min(4, stock, affordable_qty)

    text = (
        f"🛒 خرید سرویس\n\n"
        f"پلن: {title}\n"
        f"⏳ مدت: {duration}\n"
        f"قیمت هر عدد: {price:,} تومان\n"
        f"موجودی کیف پول شما: {balance:,} تومان\n"
        f"موجودی سرویس: {stock}\n\n"
    )

    if stock <= 0:
        text += (
            "❌ در حال حاضر موجودی آماده نداریم.\n"
            "اگر تعداد بالا می‌خواهید یا هماهنگی دستی لازم دارید، خرید عمده را بزنید."
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("📦 خرید عمده", callback_data="buy_bulk"))
        kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
        return await messages.send(target, "menu_buy", text, reply_markup=kb)

    if balance < price:
        need = price - balance
        text += (
            f"⚠️ موجودی کافی نیست. {need:,} تومان دیگر شارژ کنید.\n"
            "برای هماهنگی دستی یا تعداد بالا می‌توانید خرید عمده بزنید."
        )
        return await messages.send(target, "menu_buy", text, reply_markup=wallet_menu_kb(include_bulk=True))

    text += (
        "تعداد مورد نظر را انتخاب کنید:\n"
        "دکمه‌های ۱ تا ۴ فقط تا سقف موجودی لینک و موجودی کیف پول نمایش داده می‌شوند."
    )
    await messages.send(target, "menu_buy", text, reply_markup=buy_quantity_kb(max_qty))


async def check_low_stock_alert():
    threshold = settings.low_stock_threshold()
    current_stock = subs.stock_count()

    if current_stock <= threshold and not db.is_low_stock_alerted():
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ موجودی سرویس کم شد! فقط {current_stock} لینک باقی مونده "
                    f"(آستانه: {threshold}).\nلطفاً لینک جدید اضافه کنید.",
                )
            except Exception:
                pass
        db.set_low_stock_alerted(True)


@dp.message_handler(commands=["start"])
async def start(m: types.Message):
    user_id = str(m.from_user.id)
    ref = None
    args = m.get_args()

    if args and args.isdigit() and args != user_id:
        ref = args

    row, _ = db.get_or_create_user(user_id, m.from_user.username, ref)
    db.touch_active(user_id, m.from_user.username)

    if row["banned"]:
        return await m.answer("⛔ حساب شما مسدود شده.\nبرای پیگیری با پشتیبانی تماس بگیرید.")

    await messages.send(
        m,
        "welcome",
        "⚡ Berserk VPN Ready\n\nمنوی اصلی پایین صفحه همیشه در دسترس شماست.",
        reply_markup=menus.main_reply_kb(m.from_user.id),
    )


@dp.message_handler(lambda m: m.text == menus.BTN_BUY)
async def text_buy(m: types.Message):
    await render_buy(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: m.text == menus.BTN_MY_SUBS)
async def text_my_subs(m: types.Message):
    await show_my_subs(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: m.text == menus.BTN_WALLET)
async def text_wallet(m: types.Message):
    await show_wallet(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: m.text == menus.BTN_GUIDE)
async def text_guide(m: types.Message):
    await show_guide_menu(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: m.text == menus.BTN_REFERRAL)
async def text_referral(m: types.Message):
    await show_referral(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: m.text == menus.BTN_TICKET)
async def text_ticket(m: types.Message):
    await messages.send(
        m,
        "support_intro",
        "برای ارسال پیام به پشتیبانی، روی دکمه زیر بزنید:",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🎫 ارسال پیام پشتیبانی", callback_data="ticket_start")
        ),
    )


@dp.message_handler(lambda m: m.text == menus.BTN_ADMIN)
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


@dp.callback_query_handler(lambda c: c.data.startswith("buy_qty_"))
async def buy_qty(c: types.CallbackQuery):
    user_id = str(c.from_user.id)
    user = db.get_user(user_id)

    if user and user["banned"]:
        return await c.answer("⛔ حساب شما مسدود است.", show_alert=True)

    await c.answer()

    try:
        qty = int(c.data.split("_")[-1])
    except ValueError:
        return await c.message.answer("درخواست خرید نامعتبر است.", reply_markup=menus.main_reply_kb(c.from_user.id))

    if qty < 1 or qty > 4:
        return await c.message.answer("تعداد انتخاب‌شده معتبر نیست.", reply_markup=menus.main_reply_kb(c.from_user.id))

    if user is None:
        user, _ = db.get_or_create_user(user_id, c.from_user.username)

    was_first_purchase = int(user["purchased"] or 0) == 0
    price = settings.plan_price()

    try:
        result = db.complete_purchase(user_id, qty, price)
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

    await check_low_stock_alert()

    await c.message.answer(
        f"✅ خرید موفق!\n"
        f"شماره خرید: #{result['purchase_id']}\n"
        f"تعداد تحویل‌شده: {len(result['items'])} عدد\n"
        f"مبلغ کسرشده: {result['amount']:,} تومان\n"
        f"موجودی جدید: {result['balance_after']:,} تومان",
        reply_markup=menus.main_reply_kb(c.from_user.id),
    )

    for index, item in enumerate(result["items"], start=1):
        qr_path = make_qr(item["link"], user_id)
        try:
            with open(qr_path, "rb") as f:
                await c.message.answer_photo(
                    f,
                    caption=(
                        f"✅ سرویس #{index}\n"
                        f"شناسه سرویس: {item['account_name']}\n\n"
                        f"لینک سرویس:\n{item['link']}"
                    ),
                )
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
                f"تعداد: {len(result['items'])}\n"
                f"مبلغ: {result['amount']:,} تومان",
            )
        except Exception:
            pass


@dp.callback_query_handler(lambda c: c.data == "confirm_buy")
async def confirm_buy(c: types.CallbackQuery):
    class _Shim:
        data = "buy_qty_1"
        from_user = c.from_user
        message = c.message

        async def answer(self, *args, **kwargs):
            return await c.answer(*args, **kwargs)

    await buy_qty(_Shim())


@dp.callback_query_handler(lambda c: c.data == "buy_bulk")
async def buy_bulk(c: types.CallbackQuery):
    await c.answer()
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
    user_id_str = str(user_id)
    db.touch_active(user_id_str, username)
    rows = subs.user_subs(user_id_str)

    if not rows:
        return await messages.send(
            target,
            "my_services_empty",
            "هنوز هیچ سرویسی خریداری نکردید.",
            reply_markup=menus.main_reply_kb(user_id),
        )

    lines = ["📦 سرویس‌های شما:\n"]

    for r in rows:
        lines.append(
            f"شناسه سرویس: {r['account_name'] or '-'}\n"
            f"تاریخ خرید: {r['assigned_at'] or '-'}\n"
            f"لینک:\n{r['link']}\n"
        )

    await target.answer("\n".join(lines), reply_markup=menus.main_reply_kb(user_id))


@dp.callback_query_handler(lambda c: c.data == "my_subs")
async def my_subs(c: types.CallbackQuery):
    await c.answer()
    await show_my_subs(c.message, c.from_user.id, c.from_user.username or "")




def guide_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📱 آموزش اندروید", callback_data="guide_android"))
    kb.add(types.InlineKeyboardButton("🍎 آموزش آیفون", callback_data="guide_ios"))
    kb.add(types.InlineKeyboardButton("💻 آموزش ویندوز", callback_data="guide_windows"))
    kb.add(types.InlineKeyboardButton("🖥 آموزش مک", callback_data="guide_mac"))
    kb.add(types.InlineKeyboardButton("❓ مشکل اتصال دارم", callback_data="guide_troubleshoot"))
    kb.add(types.InlineKeyboardButton("🔄 بروزرسانی ساب‌لینک", callback_data="guide_update"))
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
    await messages.send(
        target,
        "guide_home",
        _GUIDE_DEFAULTS["guide_home"],
        reply_markup=guide_menu_kb(),
    )


async def show_guide_page(target, key: str, user_id: int):
    await messages.send(
        target,
        key,
        _GUIDE_DEFAULTS.get(key, "📚 آموزش اتصال"),
        reply_markup=guide_menu_kb(),
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
        return await render_buy(target, user_id, username)

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
    db.touch_active(user_id_str, username)
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
