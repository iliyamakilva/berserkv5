"""
تنظیمات قابل ویرایش از پنل ادمین، بدون نیاز به کدنویسی یا ری‌دیپلوی.
مقادیر در جدول `settings` دیتابیس ذخیره میشن؛ DEFAULTS فقط برای اولین
اجرا (وقتی هنوز چیزی در دیتابیس ثبت نشده) استفاده میشه.
"""

from db import get_setting, get_setting_int, set_setting

DEFAULTS = {
    "plan_title": "یک ماهه",
    "plan_duration_label": "۳۰ روز",
    "plan_price": "100000",
    "ref_reward": "30000",
    "card_number": "0000-0000-0000-0000",
    "card_holder": "نام صاحب کارت",
    "min_topup": "50000",
    "low_stock_threshold": "5",
}


def ensure_defaults():
    """موقع استارت ربات صدا زده میشه تا مقدار پیش‌فرض هر تنظیم، اگه قبلا
    در دیتابیس ست نشده، ذخیره بشه."""
    for key, value in DEFAULTS.items():
        if get_setting(key) is None:
            set_setting(key, value)


def plan_title():
    return get_setting("plan_title", DEFAULTS["plan_title"])


def plan_duration_label():
    return get_setting("plan_duration_label", DEFAULTS["plan_duration_label"])


def plan_price():
    return get_setting_int("plan_price", int(DEFAULTS["plan_price"]))


def ref_reward():
    return get_setting_int("ref_reward", int(DEFAULTS["ref_reward"]))


def card_number():
    return get_setting("card_number", DEFAULTS["card_number"])


def card_holder():
    return get_setting("card_holder", DEFAULTS["card_holder"])


def min_topup():
    return get_setting_int("min_topup", int(DEFAULTS["min_topup"]))


def low_stock_threshold():
    return get_setting_int("low_stock_threshold", int(DEFAULTS["low_stock_threshold"]))
