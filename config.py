import os


def _get_int(name, default=None):
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# دستور مخفی برای باز کردن پنل ادمین به‌جای /admin (که خیلی از بات‌ها دارن
# و راحت حدس زده میشه). مثال: ADMIN_COMMAND=panel_x7k9 در Railway
ADMIN_COMMAND = os.getenv("ADMIN_COMMAND", "panel_secret").lstrip("/")

# پشتیبانی از چند ادمین: ADMIN_ID=123456789,987654321
_admin_ids_raw = os.getenv("ADMIN_ID", "")
ADMIN_IDS = {
    int(x) for x in _admin_ids_raw.replace(" ", "").split(",") if x.strip().isdigit()
}

REF_REWARD = _get_int("REF_REWARD", 30000)

# آستانه‌های ضد تقلب (با متغیر محیطی قابل تنظیمن)
MAX_TOTAL_REFERRALS = _get_int("MAX_TOTAL_REFERRALS", 50)
MAX_REFERRALS_PER_DAY = _get_int("MAX_REFERRALS_PER_DAY", 10)


def validate():
    """به‌جای کرش کردن وسط کتابخونه aiogram، یه پیام واضح می‌ده و خارج میشه."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_IDS:
        missing.append("ADMIN_ID")
    if missing:
        raise SystemExit(
            "متغیر(های) محیطی الزامی تنظیم نشده: " + ", ".join(missing)
        )
