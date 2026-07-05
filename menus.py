from aiogram import types
from config import ADMIN_IDS

# عنوان‌های پیش‌فرض فقط برای fallback هستند. عنوان واقعی دکمه‌های سیستمی از db.system_buttons خوانده می‌شود.
BTN_BUY = "🛒 خرید سرویس"
BTN_MY_SUBS = "📦 سرویس‌های من"
BTN_WALLET = "💳 کیف پول"
BTN_GUIDE = "📚 آموزش اتصال"
BTN_REFERRAL = "👥 دعوت دوستان"
BTN_TICKET = "🎫 پشتیبانی"
BTN_ADMIN = "⚙️ مدیریت"
BTN_MAIN = "🏠 منوی اصلی"

DEFAULT_SYSTEM_BUTTON_TITLES = {
    "buy": BTN_BUY,
    "my_subs": BTN_MY_SUBS,
    "wallet": BTN_WALLET,
    "guide": BTN_GUIDE,
    "referral": BTN_REFERRAL,
    "ticket": BTN_TICKET,
    "admin": BTN_ADMIN,
}

_SYSTEM_BUTTONS = set(DEFAULT_SYSTEM_BUTTON_TITLES.values()) | {BTN_MAIN}


def is_admin_user(user_id) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False


def system_button_title(key: str) -> str:
    try:
        import db
        return db.system_button_title(key)
    except Exception:
        return DEFAULT_SYSTEM_BUTTON_TITLES.get(key, key)


def matches_system_button(text: str, key: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if text == DEFAULT_SYSTEM_BUTTON_TITLES.get(key):
        return True
    try:
        import db
        row = db.get_system_button(key)
        return bool(row and int(row["is_active"] or 0) == 1 and text == (row["title"] or row["default_title"]))
    except Exception:
        return False


def system_buttons_for_location(location="main", user_id=None):
    fallback = [
        ("buy", BTN_BUY),
        ("my_subs", BTN_MY_SUBS),
        ("wallet", BTN_WALLET),
        ("guide", BTN_GUIDE),
        ("referral", BTN_REFERRAL),
        ("ticket", BTN_TICKET),
    ]
    try:
        import db
        rows = db.list_system_buttons(location=location, active_only=True)
        result = []
        for row in rows:
            key = row["key"]
            if key == "admin" and not is_admin_user(user_id):
                continue
            result.append((key, row["title"] or row["default_title"]))
        return result
    except Exception:
        if location != "main":
            return []
        result = fallback[:]
        if user_id is not None and is_admin_user(user_id):
            result.append(("admin", BTN_ADMIN))
        return result


def _custom_buttons_for_location(location="main", user_id=None):
    """
    دکمه‌های اختصاصی منتشرشده را به منوی انتخاب‌شده اضافه می‌کند.
    خطای دیتابیس نباید منوی اصلی ربات را از کار بیندازد.
    """
    try:
        import db
        rows = db.list_active_custom_buttons(location)
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


def _custom_buttons_for_main(user_id=None):
    return _custom_buttons_for_location("main", user_id)


def main_reply_kb(user_id=None):
    """
    منوی ثابت پایین تلگرام.
    عنوان، ترتیب و فعال/غیرفعال بودن دکمه‌های سیستمی از پنل ادمین قابل تغییر است؛
    اما عملکرد اصلی دکمه‌ها در کد قفل می‌ماند.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, selective=False)
    labels = [title for _, title in system_buttons_for_location("main", user_id)]
    custom_buttons = _custom_buttons_for_main(user_id)
    all_buttons = labels + custom_buttons
    for index in range(0, len(all_buttons), 2):
        kb.row(*all_buttons[index:index + 2])
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
