from aiogram import types
from config import ADMIN_IDS

BTN_BUY = "🛒 خرید سرویس"
BTN_MY_SUBS = "📦 اشتراک‌های من"
BTN_WALLET = "💳 کیف پول"
BTN_REFERRAL = "👥 دعوت دوستان"
BTN_TICKET = "🎫 پشتیبانی"
BTN_ADMIN = "⚙️ مدیریت"
BTN_MAIN = "🏠 منوی اصلی"


def is_admin_user(user_id) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False


def main_reply_kb(user_id=None):
    """
    منوی ثابت پایین تلگرام.
    این منو همیشه در دسترس کاربر می‌ماند و مشکل نیاز به /start بعد از رسید/تیکت را حل می‌کند.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, selective=False)
    kb.row(BTN_BUY, BTN_MY_SUBS)
    kb.row(BTN_WALLET, BTN_REFERRAL)
    kb.row(BTN_TICKET)
    if user_id is not None and is_admin_user(user_id):
        kb.row(BTN_ADMIN)
    return kb


def back_main_inline():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb


def admin_back_inline():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت به پنل مدیریت", callback_data="adm_back"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main"))
    return kb
