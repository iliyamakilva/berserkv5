from aiogram import types
from config import ADMIN_IDS

BTN_BUY = "🛒 خرید سرویس"
BTN_MY_SUBS = "📦 سرویس‌های من"
BTN_WALLET = "💳 کیف پول"
BTN_GUIDE = "📚 آموزش اتصال"
BTN_REFERRAL = "👥 دعوت دوستان"
BTN_TICKET = "🎫 پشتیبانی"
BTN_ADMIN = "⚙️ مدیریت"
BTN_MAIN = "🏠 منوی اصلی"

_SYSTEM_BUTTONS = {
    BTN_BUY,
    BTN_MY_SUBS,
    BTN_WALLET,
    BTN_GUIDE,
    BTN_REFERRAL,
    BTN_TICKET,
    BTN_ADMIN,
    BTN_MAIN,
}


def is_admin_user(user_id) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False


def _custom_buttons_for_main(user_id=None):
    """
    دکمه‌های اختصاصی منتشرشده را به منوی اصلی اضافه می‌کند.
    خطای دیتابیس نباید منوی اصلی ربات را از کار بیندازد.
    """
    try:
        import db

        rows = db.list_active_custom_buttons("main")
    except Exception:
        return []

    result = []
    for row in rows:
        title = (row["title"] or "").strip()
        if not title or title in _SYSTEM_BUTTONS:
            continue

        audience = row["audience"] or "all"
        if audience == "admins" and not is_admin_user(user_id):
            continue

        # فیلترهای دقیق‌تر buyers/no_buy/has_service/no_service در زمان اجرای دکمه هم بررسی می‌شوند.
        result.append(title)

    return result


def main_reply_kb(user_id=None):
    """
    منوی ثابت پایین تلگرام.
    این منو بعد از ارسال رسید/تیکت/خرید دوباره به کاربر برمی‌گردد.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, selective=False)
    kb.row(BTN_BUY, BTN_MY_SUBS)
    kb.row(BTN_WALLET, BTN_GUIDE)
    kb.row(BTN_REFERRAL, BTN_TICKET)

    custom_buttons = _custom_buttons_for_main(user_id)
    for index in range(0, len(custom_buttons), 2):
        kb.row(*custom_buttons[index:index + 2])

    if user_id is not None and is_admin_user(user_id):
        kb.row(BTN_ADMIN)

    return kb


def back_main_inline():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def admin_back_inline():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb
