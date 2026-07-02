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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

validate()

if ADMIN_COMMAND == "panel_secret":
    logger.warning("ADMIN_COMMAND هنوز مقدار پیش‌فرضه؛ بهتره در Railway مقدار اختصاصی ست شود.")

bot = Bot(token=BOT_TOKEN)

db.init()
settings.ensure_defaults()

dp = Dispatcher(bot, storage=SQLiteStorage())


def wallet_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="topup_start"))
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
        "⚡ Berserk VPN Ready\n\nاز منوی پایین تلگرام استفاده کنید؛ لازم نیست هر بار /start بزنید.",
        reply_markup=menus.main_reply_kb(user_id),
    )


def ensure_user(user_id, username="", ref=None):
    row, _ = db.get_or_create_user(str(user_id), username or "", ref)
    db.touch_active(str(user_id), username or "")
    return db.get_user(str(user_id)) or row


async def render_buy(target, user_id: int, username: str = ""):
    user = ensure_user(user_id, username)
    if user and user["banned"]:
        return await target.answer("⛔ حساب شما مسدود است.")

    price = settings.plan_price()
    title = settings.plan_title()
    duration = settings.plan_duration_label()
    balance = int(user["balance"] if user else 0)
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
        text += "❌ در حال حاضر موجودی نداریم."
        return await messages.send(target, "menu_buy", text, reply_markup=menus.back_main_inline())

    if balance < price:
        need = price - balance
        text += f"⚠️ موجودی کافی نیست. {need:,} تومان دیگر شارژ کنید."
        return await messages.send(target, "menu_buy", text, reply_markup=wallet_menu_kb())

    text += "تعداد مورد نظر را انتخاب کنید. برای خرید عمده، درخواست مستقیم برای ادمین ارسال می‌شود."
    await messages.send(target, "menu_buy", text, reply_markup=buy_quantity_kb(max_qty))


async def check_low_stock_alert():
    threshold = settings.low_stock_threshold()
    current_stock = subs.stock_count()
    if current_stock <= threshold and not db.is_low_stock_alerted():
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ موجودی سرویس کم شد! فقط {current_stock} لینک باقی مونده (آستانه: {threshold}).",
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
    row = ensure_user(user_id, m.from_user.username, ref)
    if row["banned"]:
        return await m.answer("⛔ حساب شما مسدود شده. برای پیگیری با پشتیبانی تماس بگیرید.")
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


@dp.message_handler(lambda m: m.text == menus.BTN_REFERRAL)
async def text_referral(m: types.Message):
    await show_referral(m, m.from_user.id, m.from_user.username or "")


@dp.message_handler(lambda m: m.text == menus.BTN_TICKET)
async def text_ticket(m: types.Message):
    await m.answer(
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
    await c.message.answer("❌ لغو شد.", reply_markup=menus.main_reply_kb(c.from_user.id))


@dp.callback_query_handler(lambda c: c.data == "back_main")
async def back_main(c: types.CallbackQuery):
    await c.answer()
    await send_main_menu(c.message, c.from_user.id)


@dp.callback_query_handler(lambda c: c.data == "buy")
async def buy(c: types.CallbackQuery):
    await c.answer()
    await render_buy(c.message, c.from_user.id, c.from_user.username or "")


@dp.callback_query_handler(lambda c: c.data.startswith("buy_qty_"))
async def buy_qty(c: types.CallbackQuery):
    user_id = str(c.from_user.id)
    user = ensure_user(user_id, c.from_user.username or "")
    await c.answer()

    try:
        qty = int(c.data.split("_")[-1])
    except ValueError:
        return await c.message.answer("درخواست خرید نامعتبر است.", reply_markup=menus.main_reply_kb(c.from_user.id))

    was_first_purchase = int(user["purchased"] or 0) == 0
    price = settings.plan_price()

    try:
        result = db.complete_purchase(user_id, qty, price, note="telegram_multi_buy")
    except db.PurchaseError as exc:
        if exc.code == "insufficient_balance":
            return await c.message.answer(exc.message, reply_markup=wallet_menu_kb())
        return await c.message.answer(f"❌ {exc.message}", reply_markup=menus.main_reply_kb(c.from_user.id))

    if was_first_purchase:
        status, detail = reward_ref(user_id)
        if status == "rewarded":
            try:
                await bot.send_message(int(detail), "💰 یکی از زیرمجموعه‌های شما خرید کرد! پاداش رفرال به کیف پولتون اضافه شد.")
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
        f"تعداد: {result['quantity']} عدد\n"
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
                        f"شناسه: {item['account_name']}\n"
                        f"وضعیت: تحویل‌شده\n\n"
                        f"لینک سرویس:\n{item['link']}"
                    ),
                )
        finally:
            cleanup_qr(qr_path)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🛒 خرید جدید #{result['purchase_id']}\n"
                f"کاربر: {c.from_user.full_name} (@{c.from_user.username or '-'})\n"
                f"ID: {user_id}\n"
                f"تعداد: {result['quantity']}\n"
                f"مبلغ: {result['amount']:,} تومان\n"
                f"اکانت‌ها: {', '.join([x['account_name'] for x in result['items']])}",
            )
        except Exception:
            pass


@dp.callback_query_handler(lambda c: c.data == "confirm_buy")
async def confirm_buy(c: types.CallbackQuery):
    class Shim:
        data = "buy_qty_1"
        from_user = c.from_user
        message = c.message

        async def answer(self, *args, **kwargs):
            return await c.answer(*args, **kwargs)

    await buy_qty(Shim())


@dp.callback_query_handler(lambda c: c.data == "buy_bulk")
async def buy_bulk(c: types.CallbackQuery):
    await c.answer()
    user_id = str(c.from_user.id)
    ensure_user(user_id, c.from_user.username or "")
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
        "✅ درخواست خرید عمده برای مدیریت ارسال شد. به‌زودی برای هماهنگی با شما تماس گرفته می‌شود.",
        reply_markup=menus.main_reply_kb(c.from_user.id),
    )


async def show_my_subs(target, user_id: int, username: str = ""):
    ensure_user(user_id, username)
    rows = subs.user_subs(str(user_id), limit=10)
    if not rows:
        return await target.answer("هنوز هیچ اشتراکی خریداری نکردید.", reply_markup=menus.main_reply_kb(user_id))

    lines = ["📦 اشتراک‌های شما:\n"]
    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx, r in enumerate(rows, start=1):
        lines.append(
            f"{idx}) {r['account_name'] or '-'}\n"
            f"تاریخ خرید: {r['assigned_at'] or '-'}\n"
            f"مبلغ: {(r['price_paid'] or 0):,} تومان\n"
            f"وضعیت: {r['status'] or 'delivered'}\n"
        )
        kb.add(
            types.InlineKeyboardButton(f"🔗 لینک {idx}", callback_data=f"sub_link_{r['id']}"),
            types.InlineKeyboardButton(f"📷 QR {idx}", callback_data=f"sub_qr_{r['id']}"),
        )
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    await target.answer("\n".join(lines), reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data == "my_subs")
async def my_subs(c: types.CallbackQuery):
    await c.answer()
    await show_my_subs(c.message, c.from_user.id, c.from_user.username or "")


@dp.callback_query_handler(lambda c: c.data.startswith("sub_link_"))
async def show_sub_link(c: types.CallbackQuery):
    await c.answer()
    sub_id = int(c.data.split("_")[-1])
    row = subs.get_sub_by_id(sub_id)
    if not row or str(row["owner"]) != str(c.from_user.id):
        return await c.message.answer("این سرویس برای شما نیست یا پیدا نشد.")
    await c.message.answer(
        f"🔗 لینک سرویس\nشناسه: {row['account_name']}\n\n{row['link']}",
        reply_markup=menus.main_reply_kb(c.from_user.id),
    )


@dp.callback_query_handler(lambda c: c.data.startswith("sub_qr_"))
async def show_sub_qr(c: types.CallbackQuery):
    await c.answer()
    sub_id = int(c.data.split("_")[-1])
    row = subs.get_sub_by_id(sub_id)
    if not row or str(row["owner"]) != str(c.from_user.id):
        return await c.message.answer("این سرویس برای شما نیست یا پیدا نشد.")
    qr_path = make_qr(row["link"], c.from_user.id)
    try:
        with open(qr_path, "rb") as f:
            await c.message.answer_photo(
                f,
                caption=f"📷 QR سرویس\nشناسه: {row['account_name']}",
                reply_markup=menus.main_reply_kb(c.from_user.id),
            )
    finally:
        cleanup_qr(qr_path)


async def show_wallet(target, user_id: int, username: str = ""):
    user = ensure_user(user_id, username)
    await messages.send(
        target,
        "menu_wallet",
        f"💳 موجودی: {int(user['balance']):,} تومان\n📦 تعداد سرویس خریداری‌شده: {int(user['purchased']):,}",
        reply_markup=wallet_menu_kb(),
    )


@dp.callback_query_handler(lambda c: c.data == "wallet")
async def wallet_menu(c: types.CallbackQuery):
    await c.answer()
    await show_wallet(c.message, c.from_user.id, c.from_user.username or "")


async def show_referral(target, user_id: int, username: str = ""):
    ensure_user(user_id, username)
    bot_user = (await bot.get_me()).username
    link = f"https://t.me/{bot_user}?start={user_id}"
    count = db.referral_count(str(user_id))
    reward = settings.ref_reward()
    await messages.send(
        target,
        "menu_referral",
        f"👥 لینک دعوت اختصاصی شما:\n{link}\n\n"
        f"تعداد زیرمجموعه: {count} نفر\n"
        f"پاداش هر خرید زیرمجموعه: {reward:,} تومان\n\n"
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
                "⚠️ مشکلی پیش اومد. لطفا دوباره تلاش کنید یا /cancel رو بزنید.",
                reply_markup=menus.main_reply_kb(update.message.from_user.id),
            )
        elif update.callback_query:
            await update.callback_query.message.answer(
                "⚠️ مشکلی پیش اومد. لطفا دوباره تلاش کنید یا /cancel رو بزنید.",
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
