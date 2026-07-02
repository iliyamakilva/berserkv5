import asyncio
import logging
import traceback

from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.utils import executor

import admin
import backup
import db
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
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# اگه BOT_TOKEN یا ADMIN_ID تنظیم نشده باشن، اینجا با پیام واضح متوقف میشه
# به‌جای کرش کردن وسط کتابخونه aiogram.
validate()

if ADMIN_COMMAND == "panel_secret":
    logger.warning(
        "ADMIN_COMMAND هنوز مقدار پیش‌فرضه! حتما توی Railway یه اسم اختصاصی "
        "براش ست کنید (مثلا panel_x7k9) تا امنیت پنل ادمین حفظ بشه."
    )

bot = Bot(token=BOT_TOKEN)

db.init()
settings.ensure_defaults()

# فیکس نسخه ۴: به‌جای MemoryStorage (که با هر ری‌استارت Railway همه state
# ها رو پاک می‌کنه)، از یه storage مبتنی بر SQLite استفاده می‌کنیم.
dp = Dispatcher(bot, storage=SQLiteStorage())


def main_menu_kb(user_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💰 خرید سرویس", callback_data="buy"))
    kb.add(types.InlineKeyboardButton("💳 کیف پول", callback_data="wallet"))
    kb.add(types.InlineKeyboardButton("👥 دعوت دوستان", callback_data="referral"))
    kb.add(types.InlineKeyboardButton("🎫 پشتیبانی", callback_data="ticket_start"))
    # دکمه مخفی پنل ادمین: فقط برای خود ادمین اضافه میشه، بقیه کاربرا اصلا نمی‌بینن‌ش
    if user_id is not None and admin.is_admin(user_id):
        kb.add(types.InlineKeyboardButton("🛠 مدیریت", callback_data="open_admin_panel"))
    return kb


def wallet_menu_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="topup_start"))
    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"))
    return kb


@dp.message_handler(commands=["start"])
async def start(m: types.Message):
    user_id = str(m.from_user.id)

    # خوندن کد رفرال از لینک دیپ‌لینک: t.me/BotName?start=123456789
    ref = None
    args = m.get_args()
    if args and args.isdigit() and args != user_id:
        ref = args

    row, created = db.get_or_create_user(user_id, m.from_user.username, ref)
    db.touch_active(user_id, m.from_user.username)

    if row["banned"]:
        return await m.answer("⛔ حساب شما مسدود شده. برای پیگیری با پشتیبانی تماس بگیرید.")

    await messages.send(m, "welcome", "⚡ Berserk VPN Ready", reply_markup=main_menu_kb(m.from_user.id))


@dp.message_handler(commands=["cancel"], state="*")
async def cmd_cancel(m: types.Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        return await m.answer("چیزی برای لغو کردن نیست.")
    await state.finish()
    await m.answer("❌ لغو شد.", reply_markup=main_menu_kb(m.from_user.id))


@dp.callback_query_handler(lambda c: c.data == "cancel_fsm", state="*")
async def cb_cancel_fsm(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    current = await state.get_state()
    if current is not None:
        await state.finish()
    await c.message.answer("❌ لغو شد.", reply_markup=main_menu_kb(c.from_user.id))


@dp.callback_query_handler(lambda c: c.data == "back_main")
async def back_main(c: types.CallbackQuery):
    await c.answer()
    await c.message.answer("⚡ Berserk VPN Ready", reply_markup=main_menu_kb(c.from_user.id))


@dp.callback_query_handler(lambda c: c.data == "buy")
async def buy(c: types.CallbackQuery):
    user_id = str(c.from_user.id)
    user = db.get_user(user_id)

    if user and user["banned"]:
        return await c.answer("⛔ حساب شما مسدود است.", show_alert=True)

    await c.answer()
    db.touch_active(user_id, c.from_user.username)

    price = settings.plan_price()
    title = settings.plan_title()
    duration = settings.plan_duration_label()
    balance = user["balance"] if user else 0
    stock = subs.stock_count()

    text = (
        f"📦 پلن: {title}\n"
        f"⏳ مدت: {duration}\n"
        f"💰 قیمت: {price:,} تومان\n"
        f"💳 موجودی کیف پول شما: {balance:,} تومان\n"
        f"📊 موجودی سرویس: {stock}"
    )

    kb = types.InlineKeyboardMarkup()
    if stock <= 0:
        text += "\n\n❌ در حال حاضر موجودی نداریم."
    elif balance < price:
        need = price - balance
        text += f"\n\n⚠️ موجودی کافی نیست. {need:,} تومان دیگه شارژ کنید."
        kb.add(types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="topup_start"))
    else:
        kb.add(types.InlineKeyboardButton("✅ خرید با کیف پول", callback_data="confirm_buy"))

    kb.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"))
    await messages.send(c.message, "menu_buy", text, reply_markup=kb)


async def check_low_stock_alert():
    """بعد از هر فروش صدا زده میشه. اگه موجودی به آستانه رسیده باشه، فقط
    یکبار (تا شارژ مجدد) به ادمین‌ها هشدار می‌ده تا اسپم نشه."""
    threshold = settings.low_stock_threshold()
    current_stock = subs.stock_count()
    if current_stock <= threshold and not db.is_low_stock_alerted():
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ موجودی سرویس کم شد! فقط {current_stock} لینک باقی مونده "
                    f"(آستانه: {threshold}). لطفا لینک جدید اضافه کنید.",
                )
            except Exception:
                pass
        db.set_low_stock_alerted(True)


@dp.callback_query_handler(lambda c: c.data == "confirm_buy")
async def confirm_buy(c: types.CallbackQuery):
    user_id = str(c.from_user.id)
    user = db.get_user(user_id)

    if user and user["banned"]:
        return await c.answer("⛔ حساب شما مسدود است.", show_alert=True)

    await c.answer()

    price = settings.plan_price()
    balance = user["balance"] if user else 0

    if balance < price:
        return await c.message.answer("⚠️ موجودی کیف پول کافی نیست.")

    sub = subs.get_sub()
    if not sub:
        return await c.message.answer("❌ در حال حاضر موجودی سرویس نداریم. لطفا بعدا تلاش کنید.")

    sub_id, link = sub["id"], sub["link"]

    db.add_balance(user_id, -price)
    subs.assign_sub(sub_id, user_id, price_paid=price)
    db.increment_purchased(user_id)

    status, detail = reward_ref(user_id)
    if status == "rewarded":
        try:
            await bot.send_message(
                int(detail),
                "🎉 یکی از زیرمجموعه‌های شما خرید کرد! پاداش رفرال به کیف پولتون اضافه شد.",
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

    qr_path = make_qr(link, user_id)
    try:
        with open(qr_path, "rb") as f:
            await c.message.answer_photo(
                f,
                caption=f"✅ خرید موفق!\n🔗 لینک سرویس:\n`{link}`",
                parse_mode="Markdown",
            )
    finally:
        cleanup_qr(qr_path)


@dp.callback_query_handler(lambda c: c.data == "wallet")
async def wallet_menu(c: types.CallbackQuery):
    await c.answer()
    user_id = str(c.from_user.id)
    db.touch_active(user_id, c.from_user.username)

    user = db.get_user(user_id)
    bal = user["balance"] if user else 0
    purchased = user["purchased"] if user else 0
    await messages.send(
        c.message,
        "menu_wallet",
        f"💰 موجودی: {bal:,} تومان\n🛒 تعداد خرید: {purchased}",
        reply_markup=wallet_menu_kb(),
    )


@dp.callback_query_handler(lambda c: c.data == "referral")
async def referral(c: types.CallbackQuery):
    await c.answer()
    user_id = str(c.from_user.id)
    bot_user = (await bot.get_me()).username
    link = f"https://t.me/{bot_user}?start={user_id}"
    count = db.referral_count(user_id)
    reward = settings.ref_reward()
    await messages.send(
        c.message,
        "menu_referral",
        f"👥 لینک دعوت اختصاصی شما:\n{link}\n\n"
        f"تعداد زیرمجموعه: {count} نفر\n"
        f"پاداش هر خرید زیرمجموعه: {reward:,} تومان — به‌صورت خودکار به کیف پولتون واریز میشه "
        f"و می‌تونید باهاش سرویس بخرید.",
    )


wallet.register(dp)
tickets.register(dp)
admin.register(dp)


@dp.errors_handler()
async def global_error_handler(update: types.Update, exception: Exception):
    logger.exception("خطای پیش‌بینی‌نشده: %s", exception)

    try:
        if update.message:
            await update.message.answer(
                "⚠️ مشکلی پیش اومد. لطفا دوباره تلاش کنید یا /cancel رو بزنید."
            )
        elif update.callback_query:
            await update.callback_query.message.answer(
                "⚠️ مشکلی پیش اومد. لطفا دوباره تلاش کنید یا /cancel رو بزنید."
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
    await m.answer("متوجه نشدم. برای شروع /start رو بزنید یا از منو استفاده کنید.")


@dp.callback_query_handler(lambda c: True, state="*")
async def fallback_callback(c: types.CallbackQuery):
    await c.answer("این دکمه دیگه معتبر نیست.", show_alert=True)


async def on_startup(dispatcher):
    # بک‌آپ خودکار روزانه در پس‌زمینه
    asyncio.create_task(backup.daily_backup_loop(bot, ADMIN_IDS))
    logger.info("Berserk VPN bot started, daily backup loop scheduled.")


if __name__ == "__main__":
    logger.info("Berserk VPN bot starting...")
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
