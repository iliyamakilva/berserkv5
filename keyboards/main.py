from aiogram import types
from config import ADMIN_IDS

def is_admin(uid):
    return uid in ADMIN_IDS

def main_reply_kb(user_id=None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    kb.row("🛒 خرید سرویس", "📦 اشتراک‌های من")
    kb.row("💳 کیف پول", "👥 دعوت دوستان")
    kb.row("🎫 پشتیبانی")

    if user_id and is_admin(user_id):
        kb.row("⚙️ مدیریت")

    return kb
