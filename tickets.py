"""
سیستم تیکت پشتیبانی.

Hotfix:
در aiogram 2 هندلرها نباید پارامتر اجباری bot بگیرند.
برای گرفتن نمونه Bot از Bot.get_current() استفاده می‌کنیم.
"""

from aiogram import Bot, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

import db
from config import ADMIN_IDS


class TicketStates(StatesGroup):
    waiting_message = State()


def is_admin(user_id) -> bool:
    return int(user_id) in ADMIN_IDS


def cancel_kb():
    return types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("❌ لغو", callback_data="cancel_fsm")
    )


async def cb_ticket_start(c: types.CallbackQuery):
    await c.answer()
    await c.message.answer(
        "پیام یا سوالتون رو بفرستید (متن، عکس، فایل - هرچی می‌خواید):",
        reply_markup=cancel_kb(),
    )
    await TicketStates.waiting_message.set()


async def process_ticket_message(m: types.Message, state: FSMContext):
    bot = Bot.get_current()

    await state.finish()
    ticket_id = db.create_ticket(m.from_user.id)

    header = (
        f"🎫 تیکت جدید #{ticket_id}\n"
        f"👤 از: {m.from_user.full_name} (@{m.from_user.username or '---'}) | ID: {m.from_user.id}\n"
    )

    for admin_id in ADMIN_IDS:
        try:
            if m.content_type == "text":
                sent = await bot.send_message(admin_id, header + "\n" + m.text)
            else:
                caption = header + ("\n" + m.caption if m.caption else "")
                sent = await bot.copy_message(
                    admin_id,
                    m.chat.id,
                    m.message_id,
                    caption=caption,
                )

            db.record_ticket_message(admin_id, sent.message_id, ticket_id, m.from_user.id)
        except Exception:
            pass

    await m.answer(
        f"✅ پیام شما ثبت شد (تیکت #{ticket_id}).\n"
        "به‌زودی پاسخ داده میشه."
    )


def _is_ticket_reply(message: types.Message) -> bool:
    if not message.reply_to_message:
        return False

    if not is_admin(message.from_user.id):
        return False

    row = db.get_ticket_message_map(message.from_user.id, message.reply_to_message.message_id)
    return row is not None


async def handle_ticket_reply(m: types.Message):
    bot = Bot.get_current()

    row = db.get_ticket_message_map(m.from_user.id, m.reply_to_message.message_id)
    if not row:
        return

    ticket_id, customer_id = row["ticket_id"], row["user_id"]
    header = f"💬 پاسخ پشتیبانی (تیکت #{ticket_id}):\n"

    try:
        if m.content_type == "text":
            await bot.send_message(int(customer_id), header + "\n" + m.text)
        else:
            caption = header + ("\n" + m.caption if m.caption else "")
            await bot.copy_message(int(customer_id), m.chat.id, m.message_id, caption=caption)

        await m.reply("✅ پاسخ برای مشتری ارسال شد.")
    except Exception:
        await m.reply("❌ ارسال پاسخ به مشتری ناموفق بود (احتمالا ربات رو بلاک کرده).")


async def cb_open_tickets(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    rows = db.list_open_tickets()

    if not rows:
        return await c.message.answer("تیکت باز وجود نداره.")

    for r in rows:
        user = db.get_user(r["user_id"])
        uname = user["username"] if user else ""

        text = (
            f"🎫 تیکت #{r['id']}\n"
            f"@{uname or '-'} | ID: {r['user_id']}\n"
            f"{r['created_at']}"
        )

        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("✅ بستن تیکت", callback_data=f"ticket_close_{r['id']}")
        )

        await c.message.answer(text, reply_markup=kb)


async def cb_close_ticket(c: types.CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()

    await c.answer()
    ticket_id = int(c.data.split("_")[-1])
    db.close_ticket(ticket_id)

    try:
        await c.message.edit_text(c.message.text + "\n\n✅ بسته شد.")
    except Exception:
        await c.message.answer(f"تیکت #{ticket_id} بسته شد.")


def register(dp):
    dp.register_callback_query_handler(cb_ticket_start, lambda c: c.data == "ticket_start")

    dp.register_message_handler(
        process_ticket_message,
        content_types=types.ContentTypes.ANY,
        state=TicketStates.waiting_message,
    )

    dp.register_message_handler(
        handle_ticket_reply,
        _is_ticket_reply,
        content_types=types.ContentTypes.ANY,
    )

    dp.register_callback_query_handler(cb_open_tickets, lambda c: c.data == "adm_tickets")
    dp.register_callback_query_handler(cb_close_ticket, lambda c: c.data.startswith("ticket_close_"))
