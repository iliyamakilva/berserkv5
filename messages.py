"""
پیام‌های قابل‌ویرایش برای صفحات اصلی ربات.

نکته طراحی: قیمت پلن، موجودی، و مبلغ کیف‌پول همیشه باید زنده و دقیق
نمایش داده بشن - نمی‌تونن با یه متن ثابت جایگزین بشن. برای همین این ماژول
یه "بنر/مقدمه" اختیاری (متن + عکس) رو *قبل از* متن دینامیک اصلی هر صفحه
اضافه می‌کنه، نه اینکه کل پیام رو جایگزین کنه. اینطوری هم شما می‌تونید
برندینگ و بنر دلخواه بذارید، هم عددها همیشه درست و به‌روز می‌مونن.
"""

from aiogram import types

import db

MESSAGE_KEYS = [
    ("welcome", "پیام خوش‌آمد (بعد از /start)"),
    ("menu_buy", "پیام صفحه خرید"),
    ("menu_wallet", "پیام صفحه کیف پول"),
    ("menu_referral", "پیام صفحه دعوت دوستان"),
]
_VALID_KEYS = {k for k, _ in MESSAGE_KEYS}


def get(key):
    """برمی‌گردونه (prefix_text یا None, photo_file_id یا None)."""
    row = db.get_message(key)
    if not row:
        return None, None
    return row["text"], row["photo_file_id"]


async def send(target, key, body_text, reply_markup=None):
    """
    یه پیام با ترکیب بنر سفارشی (در صورت وجود) + متن دینامیک اصلی می‌فرسته.
    target باید یه شیء با متد answer/answer_photo باشه (یعنی types.Message).
    """
    prefix_text, photo_file_id = get(key)
    full_text = f"{prefix_text}\n\n{body_text}" if prefix_text else body_text

    if photo_file_id:
        # کپشن عکس توی تلگرام محدود به ۱۰۲۴ کاراکتره؛ اگه رد شد، عکس رو
        # جدا و متن رو کامل می‌فرستیم تا چیزی قطع نشه.
        if len(full_text) <= 1024:
            await target.answer_photo(photo_file_id, caption=full_text, reply_markup=reply_markup)
        else:
            await target.answer_photo(photo_file_id)
            await target.answer(full_text, reply_markup=reply_markup)
    else:
        await target.answer(full_text, reply_markup=reply_markup)


def set_text(key, text):
    if key not in _VALID_KEYS:
        raise ValueError(f"unknown message key: {key}")
    db.set_message_text(key, text)


def set_photo(key, photo_file_id):
    if key not in _VALID_KEYS:
        raise ValueError(f"unknown message key: {key}")
    db.set_message_photo(key, photo_file_id)


def clear(key):
    if key not in _VALID_KEYS:
        raise ValueError(f"unknown message key: {key}")
    db.clear_message(key)
