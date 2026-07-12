"""Wallet top-up, receipt review, and targeted checkout flow."""

import logging

from aiogram import Bot, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

import db
import menus
import settings
import subs
from affiliate import reward_ref
from config import ADMIN_IDS
from utils import cleanup_qr, make_qr, parse_int

logger = logging.getLogger(__name__)


class TopupStates(StatesGroup):
    waiting_amount = State()
    waiting_receipt = State()


def cancel_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def topup_button_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 شارژ کیف پول", callback_data="topup_start"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
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
            "لطفاً فقط عدد بفرستید. مثال: 100000",
            reply_markup=cancel_kb(),
        )

    amount = parse_int(m.text)

    if amount is None:
        return await m.answer(
            "لطفاً فقط عدد بفرستید. مثال: 100000",
            reply_markup=cancel_kb(),
        )
    min_amount = settings.min_topup()

    if amount < min_amount:
        return await m.answer(
            f"حداقل مبلغ شارژ {min_amount:,} تومانه.\nدوباره بفرستید:",
            reply_markup=cancel_kb(),
        )

    topup_id = db.create_topup(m.from_user.id, amount)
    await state.update_data(topup_id=topup_id)

    await m.answer(
        f"💳 لطفاً مبلغ {amount:,} تومان رو به شماره کارت زیر واریز کنید:\n"
        f"{settings.card_number()}\n"
        f"به نام: {settings.card_holder()}\n\n"
        "بعد از واریز، عکس رسید پرداخت رو همینجا بفرستید.",
        reply_markup=cancel_kb(),
    )
    await TopupStates.waiting_receipt.set()


async def process_receipt(m: types.Message, state: FSMContext):
    bot = Bot.get_current()

    if m.content_type not in ("photo", "text"):
        return await m.answer(
            "لطفاً عکس رسید پرداخت رو بفرستید.",
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
            "لطفاً از منوی پایین وارد کیف پول شوید و دوباره شارژ را شروع کنید.",
            reply_markup=menus.main_reply_kb(m.from_user.id),
        )

    topup = db.get_topup(topup_id)

    if not topup or topup["status"] != "awaiting_receipt":
        await state.finish()
        return await m.answer(
            "این درخواست قبلاً بررسی شده یا معتبر نیست.",
            reply_markup=menus.main_reply_kb(m.from_user.id),
        )

    if not m.photo:
        return await m.answer(
            "لطفاً عکس رسید پرداخت رو بفرستید، نه متن خالی.",
            reply_markup=cancel_kb(),
        )

    photo = m.photo[-1]

    ok, reason, submitted_topup, previous_uses = db.submit_topup_receipt_atomic(
        topup_id,
        m.from_user.id,
        photo.file_unique_id,
    )
    if not ok:
        await state.finish()
        return await m.answer(
            "این درخواست قبلاً ارسال یا بررسی شده است. لطفاً از کیف پول دوباره شروع کنید.",
            reply_markup=menus.main_reply_kb(m.from_user.id),
        )

    topup = submitted_topup
    is_duplicate = previous_uses > 0
    await state.finish()

    test_marker = "\n🧪 این درخواست متعلق به کاربر تست است." if int(topup["is_test"] or 0) else ""
    caption = (
        f"💳 درخواست شارژ جدید #{topup_id}\n"
        f"👤 کاربر: {m.from_user.full_name} (@{m.from_user.username or '---'}) | ID: {m.from_user.id}\n"
        f"💰 مبلغ: {topup['amount']:,} تومان"
        f"{test_marker}"
    )

    if is_duplicate:
        caption = (
            f"⚠️ هشدار: این عکس قبلاً {previous_uses} بار به‌عنوان رسید فرستاده شده!\n"
            "احتمال تقلب - قبل از تایید حتماً دستی بررسی کنید.\n\n"
            + caption
        )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ تایید شارژ", callback_data=f"topup_confirm_{topup_id}"),
        types.InlineKeyboardButton("❌ رد", callback_data=f"topup_reject_{topup_id}"),
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, photo.file_id, caption=caption, reply_markup=kb)
        except Exception:
            logger.exception("Could not send topup %s receipt to admin %s", topup_id, admin_id)

    await m.answer(
        "✅ رسید شما برای بررسی ارسال شد.\n"
        "بعد از تایید، کیف پولتون شارژ میشه.\n\n"
        "منوی پایین تلگرام همچنان فعاله و لازم نیست دوباره /start بزنید.",
        reply_markup=menus.main_reply_kb(m.from_user.id),
    )


async def cb_confirm(c: types.CallbackQuery):
    bot = Bot.get_current()

    if int(c.from_user.id) not in ADMIN_IDS:
        return await c.answer("فقط ادمین می‌تواند این کار را انجام دهد.", show_alert=True)

    await c.answer()
    topup_id = int(c.data.split("_")[-1])
    ok, reason, topup, new_balance = db.approve_topup_atomic(topup_id, admin_id=c.from_user.id)

    if not ok:
        if reason == "not_found":
            return await _edit_safely(c, "این درخواست پیدا نشد.")
        return await _edit_safely(c, "این درخواست قبلاً بررسی شده.")

    db.log_admin_action(c.from_user.id, "approve_topup", topup["user_id"], f"topup_id={topup_id}; amount={topup['amount']}")
    user = db.get_user(topup["user_id"])
    auto_purchase_msg = ""

    target_qty = topup["target_quantity"] if "target_quantity" in topup.keys() else None
    target_plan_id = topup["target_plan_id"] if "target_plan_id" in topup.keys() else None
    target_unit_price = topup["target_unit_price"] if "target_unit_price" in topup.keys() else None

    if target_qty and target_plan_id and not topup["purchase_completed_at"]:
        was_first_purchase = int(user["purchased"] or 0) == 0 if user else False
        try:
            plan = db.get_plan(int(target_plan_id))
            if plan and db.plan_provider_key(plan) != "pool":
                result = await subs.provision_provider_purchase(
                    topup["user_id"],
                    int(target_qty),
                    int(target_plan_id),
                    int(target_unit_price) if target_unit_price else None,
                    note=f"auto_after_topup_id={topup_id}",
                )
            else:
                result = db.complete_purchase(
                    topup["user_id"],
                    int(target_qty),
                    int(target_unit_price) if target_unit_price else None,
                    note=f"auto_after_topup_id={topup_id}",
                    plan_id=int(target_plan_id),
                )
            db.mark_topup_purchase_completed(topup_id)
            new_balance = result["balance_after"]
            plan = db.get_plan(target_plan_id)
            auto_purchase_msg = (
                f"\n\n🛒 خرید شما خودکار تکمیل شد.\n"
                f"شماره خرید: #{result['purchase_id']}\n"
                f"پلن: {plan['title'] if plan else '-'}\n"
                f"تعداد سرویس: {len(result['items'])}\n"
                f"مبلغ کسرشده: {result['amount']:,} تومان\n"
                f"موجودی جدید: {result['balance_after']:,} تومان"
            )

            if was_first_purchase:
                referral_status, referral_detail = reward_ref(topup["user_id"])
                if referral_status == "rewarded":
                    try:
                        await bot.send_message(
                            int(referral_detail),
                            "💰 یکی از زیرمجموعه‌های شما اولین خرید واقعی خود را انجام داد. "
                            "پاداش رفرال به کیف پول شما اضافه شد.",
                        )
                    except Exception:
                        logger.warning(
                            "Could not notify referrer %s after auto purchase",
                            referral_detail,
                            exc_info=True,
                        )
                elif referral_status == "blocked":
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(admin_id, referral_detail)
                        except Exception:
                            logger.warning(
                                "Could not send referral fraud warning to admin %s",
                                admin_id,
                                exc_info=True,
                            )

            for index, item in enumerate(result["items"], start=1):
                qr_path = make_qr(item["link"], topup["user_id"])
                try:
                    with open(qr_path, "rb") as f:
                        sent_service = await bot.send_photo(
                            int(topup["user_id"]),
                            f,
                            caption=(
                                f"✅ سرویس #{index}\n"
                                f"شناسه سرویس: {item['account_name']}\n\n"
                                f"لینک سرویس:\n{item['link']}"
                            ),
                        )
                        db.track_bot_message(
                            sent_service.chat.id,
                            topup["user_id"],
                            sent_service.message_id,
                            "auto_purchase_delivery",
                            kind="delivery",
                        )
                finally:
                    cleanup_qr(qr_path)
        except db.PurchaseError as exc:
            auto_purchase_msg = (
                "\n\n⚠️ پرداخت تأیید شد و کیف پول شارژ شد، اما خرید خودکار تکمیل نشد.\n"
                f"دلیل: {exc.message}\n"
                "لطفاً از بخش خرید سرویس دوباره تلاش کنید یا با پشتیبانی تماس بگیرید."
            )

    try:
        await bot.send_message(
            int(topup["user_id"]),
            f"✅ کیف پول شما به مبلغ {topup['amount']:,} تومان شارژ شد.\n"
            f"💰 موجودی فعلی: {new_balance:,} تومان"
            f"{auto_purchase_msg}",
            reply_markup=menus.main_reply_kb(topup["user_id"]),
        )
    except Exception:
        logger.warning("Could not notify user %s about approved topup", topup["user_id"], exc_info=True)

    await _edit_safely(c, f"✅ درخواست #{topup_id} تایید شد.{auto_purchase_msg}")

async def cb_reject(c: types.CallbackQuery):
    bot = Bot.get_current()

    if int(c.from_user.id) not in ADMIN_IDS:
        return await c.answer("فقط ادمین می‌تواند این کار را انجام دهد.", show_alert=True)

    await c.answer()
    topup_id = int(c.data.split("_")[-1])
    ok, reason, topup = db.reject_topup_atomic(topup_id, admin_id=c.from_user.id)
    if not ok:
        if reason == "not_found":
            return await _edit_safely(c, "این درخواست پیدا نشد.")
        return await _edit_safely(c, "این درخواست قبلاً بررسی شده.")

    db.log_admin_action(c.from_user.id, "reject_topup", topup["user_id"], f"topup_id={topup_id}; amount={topup['amount']}")

    try:
        await bot.send_message(
            int(topup["user_id"]),
            f"❌ درخواست شارژ #{topup_id} رد شد.\n"
            "در صورت سوال با پشتیبانی تماس بگیرید.",
            reply_markup=menus.main_reply_kb(topup["user_id"]),
        )
    except Exception:
        logger.warning("Could not notify user %s about rejected topup", topup["user_id"], exc_info=True)

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
