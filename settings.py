from db import get_setting, get_setting_int, set_setting

DEFAULTS = {
    "plan_title": "یک ماهه | ۱۰۰ گیگ | ۳ کاربره",
    "plan_duration_label": "۳۰ روز",
    "plan_price": "100000",
    "ref_reward": "30000",
    "card_number": "0000-0000-0000-0000",
    "card_holder": "نام صاحب کارت",
    "min_topup": "50000",
    "low_stock_threshold": "5",
    "bot_enabled": "1",
    "bot_disabled_message": "⛔ ربات موقتاً غیرفعال است.\n\nلطفاً کمی بعد دوباره مراجعه کنید.",
    "sales_enabled": "1",
    "sales_closed_message": "⛔ فروش در حال حاضر بسته است.\n\nدر حال بروزرسانی موجودی سرویس‌ها هستیم. لطفاً بعداً دوباره تلاش کنید.",
}


def ensure_defaults():
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



def bot_enabled():
    return get_setting_int("bot_enabled", int(DEFAULTS["bot_enabled"])) == 1


def bot_disabled_message():
    return get_setting("bot_disabled_message", DEFAULTS["bot_disabled_message"])


def sales_enabled():
    return get_setting_int("sales_enabled", int(DEFAULTS["sales_enabled"])) == 1


def sales_closed_message():
    return get_setting("sales_closed_message", DEFAULTS["sales_closed_message"])
