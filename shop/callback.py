from aiogram import types
from core.logger import log_event
from core.antifraud import check_fraud

async def handle_buy(call: types.CallbackQuery):
    uid = call.from_user.id

    if not check_fraud(uid):
        await call.answer("⛔ محدودیت موقت فعال است", show_alert=True)
        return

    log_event(uid, call.data)
    await call.answer("✔ در حال پردازش خرید...")
