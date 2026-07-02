"""
جریان شارژ کیف پول.

Hotfix:
در aiogram 2 هندلرها نباید پارامتر اجباری bot بگیرند.
برای گرفتن نمونه Bot از Bot.get_current() استفاده می‌کنیم.
"""

from aiogram import Bot, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

import db
import settings
from config import ADMIN_IDS


class TopupStates(StatesGroup):
    waiting_amount = State()
    waiting_receipt = State()


def cancel_kb():
    return types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm")
    )


def topup_button_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="topup_start"))
    return kb


async def cb_topup_start(c: types.CallbackQuery):
    await c.answer()
    await c.message.answer(
        f"چه مبلغی می‌خواید شارژ کنید؟\n"
        f"(حداقل مبلغ شارژ: {settings.min_topup():,} تومان — فقط عدد بفرستید)",
        reply_markup=cancel_kb(),
    )
    await TopupStates.waiting_amount.set()


async def process_amount(m: types.Message, state: FSMContext):
    if m.content_type != "text":
        return await m.answer(
            "لطفا فقط عدد بفرستید (مثال: 100000).",
            reply_markup=cancel_kb(),
        )

    text = m.text.strip().replace(",", "")
    if not text.isdigit():
        return await m.answer(
            "لطفا فقط عدد بفرستید. مثال: 100000",
            reply_markup=cancel_kb(),
        )

    amount = int(text)
    min_amount = settings.min_topup()

    if amount < min_amount:
        return await m.answer(
            f"حداقل مبلغ شارژ {min_amount:,} تومانه.\nدوباره بفرستید:",
            reply_markup=cancel_kb(),
        )

    topup_id = db.create_topup(m.from_user.id, amount)
    await state.update_data(topup_id=topup_id)

    await m.answer(
        f"💳 لطفا مبلغ {amount:,} تومان رو به شماره کارت زیر واریز کنید:\n"
        f"`{settings.card_number()}`\n"
        f"به نام: {settings.card_holder()}\n\n"
        "بعد از واریز، عکس رسید پرداخت رو همینجا بفرستید.",
        parse_mode="Markdown",
        reply_markup=cancel_kb(),
    )
    await TopupStates.waiting_receipt.set()


async def process_receipt(m: types.Message, state: FSMContext):
    bot = Bot.get_current()

    if m.content_type not in ("photo", "text"):
        return await m.answer(
            "لطفا عکس رسید پرداخت رو بفرستید (نه فایل یا نوع دیگه).",
            reply_markup=cancel_kb(),
        )

    data = await state.get_data()
    topup_id = data.get("topup_id")

    if not topup_id:
        row = db.get_pending_receipt_topup(m.from_user.id)
        topup_id = row["id"] if row else None

    if not topup_id:
        await state.finish()
        return await m.answer(
            "درخواست شارژ فعالی برای شما پیدا نشد.\n"
            "لطفا اول روی «شارژ کیف پول» بزنید."
        )

    topup = db.get_topup(topup_id)

    if not topup or topup["status"] != "awaiting_receipt":
        await state.finish()
        return await m.answer("این درخواست قبلا بررسی شده یا معتبر نیست.")

    if not m.photo:
        return await m.answer(
            "لطفا عکس رسید پرداخت رو بفرستید (نه فقط متن).",
            reply_markup=cancel_kb(),
        )

    photo = m.photo[-1]

    previous_uses = db.find_receipt(photo.file_unique_id)
    is_duplicate = len(previous_uses) > 0

    db.record_receipt(photo.file_unique_id, m.from_user.id, topup_id)
    db.set_topup_status(topup_id, "pending_review")
    await state.finish()

    caption = (
        f"💳 درخواست شارژ جدید #{topup_id}\n"
        f"👤 کاربر: {m.from_user.full_name} (@{m.from_user.username or '---'}) | ID: {m.from_user.id}\n"
        f"💰 مبلغ: {topup['amount']:,} تومان"
    )

    if is_duplicate:
        caption = (
            f"⚠️ هشدار: این عکس قبلا {len(previous_uses)} بار به‌عنوان رسید فرستاده شده!\n"
            f"احتمال تقلب - قبل از تایید حتما دستی بررسی کنید.\n\n"
            + caption
        )

    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("✅ تایید شارژ", callback_data=f"topup_confirm_{topup_id}"),
        types.InlineKeyboardButton("❌ رد", callback_data=f"topup_reject_{topup_id}"),
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, photo.file_id, caption=caption, reply_markup=kb)
        except Exception:
            pass

    await m.answer(
        "رسید شما برای بررسی ارسال شد.\n"
        "بعد از تایید، کیف پولتون شارژ میشه."
    )


async def cb_confirm(c: types.CallbackQuery):
    bot = Bot.get_current()

    if int(c.from_user.id) not in ADMIN_IDS:
        return await c.answer("فقط ادمین می‌تواند این کار را انجام دهد.", show_alert=True)

    await c.answer()
    topup_id = int(c.data.split("_")[-1])
    topup = db.get_topup(topup_id)

    if not topup:
        return await _edit_safely(c, "این درخواست پیدا نشد.")

    if topup["status"] != "pending_review":
        return await _edit_safely(c, "این درخواست قبلا بررسی شده.")

    db.add_balance(topup["user_id"], topup["amount"])
    db.set_topup_status(topup_id, "approved")
    user = db.get_user(topup["user_id"])

    try:
        await bot.send_message(
            int(topup["user_id"]),
            f"✅ کیف پول شما به مبلغ {topup['amount']:,} تومان شارژ شد.\n"
            f"💰 موجودی فعلی: {user['balance']:,} تومان",
        )
    except Exception:
        pass

    await _edit_safely(c, f"✅ درخواست #{topup_id} تایید و کیف پول شارژ شد.")


async def cb_reject(c: types.CallbackQuery):
    bot = Bot.get_current()

    if int(c.from_user.id) not in ADMIN_IDS:
        return await c.answer("فقط ادمین می‌تواند این کار را انجام دهد.", show_alert=True)

    await c.answer()
    topup_id = int(c.data.split("_")[-1])
    topup = db.get_topup(topup_id)

    if not topup:
        return await _edit_safely(c, "این درخواست پیدا نشد.")

    if topup["status"] != "pending_review":
        return await _edit_safely(c, "این درخواست قبلا بررسی شده.")

    db.set_topup_status(topup_id, "rejected")

    try:
        await bot.send_message(
            int(topup["user_id"]),
            f"❌ درخواست شارژ #{topup_id} رد شد.\n"
            "در صورت سوال با پشتیبانی تماس بگیرید.",
        )
    except Exception:
        pass

    await _edit_safely(c, f"❌ درخواست #{topup_id} رد شد.")


async def _edit_safely(c: types.CallbackQuery, text: str):
    try:
        if c.message.photo:
            await c.message.edit_caption(caption=text)
        else:
            await c.message.edit_text(text)
    except Exception:
        await c.message.answer(text)


def register(dp):
    dp.register_callback_query_handler(cb_topup_start, lambda c: c.data == "topup_start")

    dp.register_message_handler(
        process_amount,
        content_types=types.ContentTypes.ANY,
        state=TopupStates.waiting_amount,
    )

    dp.register_message_handler(
        process_receipt,
        content_types=types.ContentTypes.ANY,
        state=TopupStates.waiting_receipt,
    )

    dp.register_callback_query_handler(cb_confirm, lambda c: c.data.startswith("topup_confirm_"))
    dp.register_callback_query_handler(cb_reject, lambda c: c.data.startswith("topup_reject_"))
